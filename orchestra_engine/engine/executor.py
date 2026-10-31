"""run_loop: walk the pipeline's stages, threading summary + session ID between
them. Usually that walk is forward, but every plain stage decides its own next
step — advance, run again, jump to a named stage, or finish — so the walk can
loop. Shape selection (plain / repeat / fan-out) happens here; the shapes
themselves live in shapes.py."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

from .. import channel, interaction
from ..model import Pipeline, Stage, StageResult
from .runlog import (
    ARTIFACTS_DIRNAME,
    ESCALATIONS_FILE,
    PIN_CLAIM_RE,
    PIN_ID_RE,
    PINS_FILE,
    PRIOR_DIRNAME,
    RESTORED_MARKER,
    SUPERSEDED_DIRNAME,
    WITHHELD_DIRNAME,
    format_duration,
    new_run_dir,
    next_log_name,
    reuse_run_dir,
    write_run_log,
)
from .session import DEFAULT_SETTING_SOURCES, default_log
from .shapes import FanOutInterrupted, fan_out_checkpoint_path, run_chain, run_fan_out

DECISION_FILE = "decision.json"

# Reserved values of decision.json's "next": moves every stage can make without
# declaring anything. A stage name is the fourth option, and it must be declared
# in that stage's `decide:` targets.
NEXT_ADVANCE = "advance"
NEXT_AGAIN = "again"
NEXT_DONE = "done"


# A run only loops because a stage decided to, and even then it must halt. The
# budget counts stage executions, not stages: a plain forward run of N stages
# spends ~N and never comes near this, but a stage that keeps routing back burns
# it down and is then sent to the terminal stage. Scaled to the pipeline so
# longer ones get proportionally more room; overridable per run.
def _default_hop_budget(stage_count: int) -> int:
    return stage_count * 2


def _artifact_mtimes(artifacts: Path) -> dict[str, float]:
    """Snapshot the artifacts directory, so a stage's writes can be attributed.

    Top-level files only. Stage documents live there, and the caller's question
    is "which documents did this stage produce", not "what did it touch".
    """
    if not artifacts.is_dir():
        return {}
    seen: dict[str, float] = {}
    for path in artifacts.iterdir():
        try:
            if path.is_file():
                seen[path.name] = path.stat().st_mtime
        except OSError:
            # A file that vanished between listing and stat is one this stage
            # did not leave behind, which is exactly what omitting it records.
            continue
    return seen


def _touched_since(artifacts: Path, before: dict[str, float]) -> set[str]:
    """Artifact filenames created or rewritten since the snapshot was taken."""
    after = _artifact_mtimes(artifacts)
    return {name for name, mtime in after.items() if before.get(name) != mtime}


def _read_escalations(artifacts: Path) -> str:
    """The escalation record as it stands, or empty if nothing has been raised."""
    path = artifacts / ESCALATIONS_FILE
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""


def _restore_escalations(artifacts: Path, before: str, log: Callable[[str], None]) -> None:
    """Put back anything a stage removed from the escalation record.

    A stage may append to this file and may not rewrite it. The rule exists
    because route-backs mean the stage that raised a concern is rarely the last
    one to write: a later pass that disagrees would otherwise settle the matter
    by deleting the sentence that recorded it, and the run ends looking like
    nothing was ever given up.

    Disagreement is still available — append the rebuttal. What is not available
    is leaving no trace, so an edit that drops earlier text is undone and the
    stage's version is kept below it rather than instead of it.
    """
    if not before:
        return
    path = artifacts / ESCALATIONS_FILE
    now = _read_escalations(artifacts)
    if now.startswith(before):
        return
    kept = now.removeprefix(before) if now.startswith(before) else now
    path.write_text(
        f"{before.rstrip()}\n\n{RESTORED_MARKER}\n\n{kept.lstrip()}\n",
        encoding="utf-8",
    )
    log(
        f"{ESCALATIONS_FILE} is append-only — a stage rewrote it, so the earlier "
        f"text was restored above its version."
    )


def _pin_claims(artifacts: Path) -> dict[str, str]:
    """Every pin currently in the record, mapped to the outcome it asserts.

    An id with no `PIN-n: …` claim line maps to the empty string: it is still
    watched for disappearing, it just has no wording to compare. Whitespace is
    normalised so reflowing the document is not mistaken for changing it.
    """
    path = artifacts / PINS_FILE
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return {}
    claims = {pid: " ".join(claim.split()) for pid, claim in PIN_CLAIM_RE.findall(text)}
    for pid in PIN_ID_RE.findall(text):
        claims.setdefault(pid, "")
    return claims


# Directories the pin scan never descends into. A pin test lives with the
# project's other tests; everything here is either the repository's own
# bookkeeping or third-party code, where a `PIN-` match would be a coincidence
# and the walk would cost more than the whole run.
_PIN_SCAN_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
     "coverage", ".next", ".tox", "target", "vendor", ".mypy_cache", ".pytest_cache"}
)

# Files larger than this are not read during the scan: a pin test is source, and
# a multi-megabyte match is a lockfile, a bundle or a fixture dump.
_PIN_SCAN_MAX_BYTES = 1_000_000

# How many lines after a `PIN-n` marker count as that pin's assertion. Long
# enough to hold a test body, short enough that the next test added below it is
# usually outside the window.
_PIN_ASSERTION_WINDOW = 12


def _pin_assertion_files(workspace: Path) -> list[Path]:
    """The workspace files carrying `PIN-n` markers, found once after the pins land.

    One bounded walk, kept so later boundaries re-read a known list instead of
    crawling the repository eight more times. A pin test moved to a file that
    wasn't on the list leaves its old file, which reads as the marker
    disappearing — the right answer, since what is being watched is whether the
    assertion is still where the record says it is.
    """
    found: list[Path] = []
    for path in workspace.rglob("*"):
        if any(part in _PIN_SCAN_SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > _PIN_SCAN_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if PIN_ID_RE.search(text):
            found.append(path)
    return sorted(found)


def _pin_assertions(files: list[Path]) -> dict[str, str]:
    """Each pin id mapped to the text of the test that carries it.

    `PINS.md` records what the run *promises*; these files are where it is
    actually *asserted*, and the two can part company. A stage that inverts a
    pin's test and leaves the document alone changes nothing the claim watcher
    can see, so the run ends with a record describing a promise the code does
    not keep. That is not hypothetical — it is how a run shipped a parser
    accepting the input three of its own pins said must be rejected, with all
    three claims still on the page.

    The window after each marker is short and fixed rather than running to the
    next id, so an unrelated test added nearby is less likely to read as a
    change. It is still only a heuristic, and it errs toward reporting: a false
    "changed" costs a line in the log that a reader can dismiss, while a missed
    inversion is the failure this exists to catch.
    """
    blocks: dict[str, list[str]] = {}
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            for pid in dict.fromkeys(PIN_ID_RE.findall(line)):
                window = lines[i : i + _PIN_ASSERTION_WINDOW]
                blocks.setdefault(pid, []).append(" ".join(" ".join(window).split()))
    return {pid: " ⏎ ".join(parts) for pid, parts in blocks.items()}


def _report_pin_assertion_changes(
    artifacts: Path,
    before: dict[str, str],
    files: list[Path],
    claims: dict[str, str],
    stage_name: str,
    log: Callable[[str], None],
) -> dict[str, str]:
    """Announce a pin whose *test* moved, however still the document held.

    The claim watcher covers the stage that edits `PINS.md`. This covers the
    stage that edits the code and leaves `PINS.md` alone — the same withdrawal,
    reached by not touching the record at all, and the one an id-and-claim watch
    is blind to by construction.

    The pin's claim line goes in the report, because the claim is the promise in
    words and it is the thing a reader needs in order to judge whether the new
    assertion still keeps it. Without it the entry says only that some test
    changed, which is what a run does all day.
    """
    now = _pin_assertions(files)
    gone = sorted(set(before) - set(now))
    changed = sorted(pid for pid in set(before) & set(now) if before[pid] != now[pid])
    if not gone and not changed:
        return now

    parts = []
    if gone:
        parts.append(f"no longer asserted anywhere: {', '.join(gone)}")
    if changed:
        parts.append(f"assert differently: {', '.join(changed)}")
    summary = "; ".join(parts)
    log(
        f"[{stage_name}] PIN TESTS CHANGED — {summary}. {PINS_FILE} still says what "
        f"these promise; the tests no longer say the same. Recorded on {ESCALATIONS_FILE}."
    )

    lines = [f"## [{stage_name}] Pin tests changed: {summary}\n"]
    lines.append(
        f"Recorded by the engine, not by the stage. {PINS_FILE} was not edited — "
        f"these ids still carry their original claims there. What changed is the "
        f"test that makes each claim executable, which is the only place the "
        f"promise was ever actually kept.\n"
    )
    for pid in changed + gone:
        claim = claims.get(pid) or "(no claim recorded)"
        lines.append(
            f"- **{pid}** promises: `{claim}`\n"
            f"  was asserted by: `{before[pid][:400]}`\n"
            f"  now: `{(now.get(pid) or '(absent)')[:400]}`\n"
        )
    entry = "\n".join(lines)

    path = artifacts / ESCALATIONS_FILE
    existing = _read_escalations(artifacts)
    path.write_text(
        f"{existing.rstrip()}\n\n{entry}" if existing.strip() else entry, encoding="utf-8"
    )
    return now


def _report_pin_changes(
    artifacts: Path, before: dict[str, str], stage_name: str, log: Callable[[str], None]
) -> dict[str, str]:
    """Notice a stated criterion losing an input, and make it cost something.

    A pin is an input form of a criterion the request stated, made executable
    before any design existed to defend. Revising one is sometimes right — a pin
    can encode a misreading, and a run that could never revise one would be worse
    than this. What is never right is revising it quietly, and quietly is the
    only way it has ever happened: no test goes red, no differential moves, and
    every document that remains agrees the criterion is met.

    Two ways to do it, and they are the same event. Delete the row. Or keep the
    id and rewrite what it asserts — which is how a real run reported "41
    declared, 41 present, 41 pass" over a requirement it had withdrawn, because
    an id-only watch cannot see an inverted assertion.

    Both are announced at the boundary that produced them and appended to the
    escalation record, which is append-only and already put in front of a human
    at the end. Returns the claims to compare against next time, so a pin changed
    once is not re-reported by every stage after it.
    """
    now = _pin_claims(artifacts)
    dropped = sorted(set(before) - set(now))
    # Only pins that carried a claim can be compared; an id that never had one
    # has no wording to have changed.
    inverted = sorted(
        pid for pid in set(before) & set(now) if before[pid] and before[pid] != now[pid]
    )
    if not dropped and not inverted:
        return now

    parts = []
    if dropped:
        parts.append(f"dropped: {', '.join(dropped)}")
    if inverted:
        parts.append(f"now assert something else: {', '.join(inverted)}")
    summary = "; ".join(parts)
    log(
        f"[{stage_name}] PINS CHANGED — {summary}. A stated criterion no longer "
        f"promises what it promised. Recorded on {ESCALATIONS_FILE}."
    )

    lines = [f"## [{stage_name}] Pins changed: {summary}\n"]
    lines.append(
        f"Recorded by the engine, not by the stage. Each of these was an input form "
        f"of a criterion carrying the request's own words, made executable before "
        f"any design existed to defend. If the revision was deliberate the reasoning "
        f"is somewhere else; if it was not, this is the only place it appears.\n"
    )
    for pid in inverted:
        lines.append(f"- **{pid}** was: `{before[pid]}`\n  now: `{now[pid] or '(no claim)'}`\n")
    for pid in dropped:
        lines.append(f"- **{pid}** was: `{before[pid] or '(no claim)'}`\n  now: absent\n")
    entry = "\n".join(lines)

    path = artifacts / ESCALATIONS_FILE
    existing = _read_escalations(artifacts)
    path.write_text(
        f"{existing.rstrip()}\n\n{entry}" if existing.strip() else entry, encoding="utf-8"
    )
    return now


def _report_new_escalations(artifacts: Path, before: str, log: Callable[[str], None]) -> None:
    """Say out loud what a stage just added to the escalation record.

    The file is where a run records that it now promises less than it did, and
    until now it was written and read but never announced — so a run driven in
    the background raised its concern into a document nobody was watching, and
    the first anyone heard of it was the end. Growth is the signal, and the
    stage boundary is where it means something, so it is logged here.

    The text is written by an agent, and what reads this log is often another
    agent deciding whether to let the run continue. So it is quoted rather than
    echoed: every line is prefixed, and nothing a stage writes can arrive
    looking like a line the engine printed.
    """
    stem = before.rstrip()
    now = _read_escalations(artifacts)
    added = (now[len(stem):] if now.startswith(stem) else now).strip()
    if not added:
        return
    quoted = "\n".join(f"  | {line}" for line in added.splitlines())
    log(f"{ESCALATIONS_FILE} grew — this stage raised:\n{quoted}")
    channel.emit("escalation", text=added)


def _supersede_prior_outputs(
    run_dir: Path,
    artifacts: Path,
    stage_name: str,
    wrote: dict[str, set[str]],
    log: Callable[[str], None],
) -> None:
    """Move a blind stage's earlier documents out of the artifacts directory.

    A `handoff: none` stage that runs a second time — because a router sent the
    loop back to an earlier stage — would otherwise find its own previous
    document in the directory it is told to write to. That is the one file
    guaranteed to defeat the barrier: withholding the other stages' reports
    while leaving the stage's own prior reading in reach makes the second pass
    an edit of the first, which is precisely what an independent reading is not.

    Moved rather than deleted, and moved outside artifacts/ rather than into a
    subdirectory of it. Superseded readings are worth keeping — comparing the
    two passes is how you see whether the second one actually differed — but
    they are only out of reach if they leave the one directory the stage is
    granted.
    """
    names = sorted(wrote.get(stage_name, ()))
    if not names:
        return
    attempt = 1
    while (dest := run_dir / SUPERSEDED_DIRNAME / f"{stage_name}-{attempt}").exists():
        attempt += 1
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for name in names:
        source = artifacts / name
        if source.is_file():
            source.replace(dest / name)
            moved.append(name)
    if moved:
        log(f"[{stage_name}] sees no prior stage — moved {', '.join(moved)} to {dest}")


def _snapshot_prior_outputs(
    artifacts: Path,
    stage_name: str,
    wrote: dict[str, set[str]],
    log: Callable[[str], None],
) -> None:
    """Keep a re-entered stage's earlier documents where a later stage can diff them.

    A route-back sends a stage back to rewrite the document it already wrote,
    and the rewrite lands on the same filename. By the time anything downstream
    reads it, the version it replaced is gone — so the one comparison that shows
    what the second pass actually changed is unavailable, and in particular
    whether the revision delivered *less* than the version it replaced. That is
    the cheapest way for a routed-back defect to disappear, and it leaves no
    trace in a directory holding only the latest of everything.

    Copied rather than moved, unlike a blind stage's outputs: this stage is
    meant to build on what it wrote, so the live document stays exactly where it
    was. The copy lands inside artifacts/ because the stages that need to read
    it are granted that directory and nothing else, and in a subdirectory
    because attribution counts top-level files only — a snapshot records a
    stage's output rather than being one.
    """
    names = sorted(wrote.get(stage_name, ()))
    if not names:
        return
    attempt = 1
    while (dest := artifacts / PRIOR_DIRNAME / f"{stage_name}-{attempt}").exists():
        attempt += 1
    dest.mkdir(parents=True, exist_ok=True)
    kept = []
    for name in names:
        source = artifacts / name
        if source.is_file():
            shutil.copy2(source, dest / name)
            kept.append(name)
    if kept:
        log(f"[{stage_name}] ran before — kept {', '.join(kept)} in {dest}")


def _snapshot_for_resume(artifacts: Path, log: Callable[[str], None]) -> None:
    """Keep the documents a previous pass left, before this one overwrites them.

    _keep_prior_artifacts does this within a run, driven by which stage wrote
    what — attribution the engine holds in memory and loses when the process
    ends. So a stage re-entered by a *resume* rather than a route-back would
    rewrite its document with no snapshot taken, and the comparison that shows
    whether the second version delivered less than the first would be gone.
    That comparison is the thing a route-back is checked against, and it should
    not depend on whether the human's answer arrived inside the same process.

    Attribution can't be recovered — run.json records what each stage said, not
    which files it wrote — so this snapshots the directory rather than any one
    stage's share of it. One folder, taken once, before the first stage runs.
    Top-level files only, matching how attribution counts them: prior/ and the
    other subdirectories are already history and don't need copying into it.
    """
    files = [entry for entry in sorted(artifacts.iterdir()) if entry.is_file()]
    if not files:
        return
    attempt = 1
    while (dest := artifacts / PRIOR_DIRNAME / f"resumed-{attempt}").exists():
        attempt += 1
    dest.mkdir(parents=True, exist_ok=True)
    for entry in files:
        shutil.copy2(entry, dest / entry.name)
    log(f"resuming — kept {len(files)} document(s) from the previous pass in {dest}")


def _withhold_artifacts(
    run_dir: Path,
    artifacts: Path,
    stage_name: str,
    log: Callable[[str], None],
) -> list[tuple[Path, Path]]:
    """Empty artifacts/ for the duration of a blind stage, returning what to put back.

    `handoff: none` withholds the earlier stages' summaries and the pointers to
    their sessions, and then hands the stage a directory containing the reports
    those summaries were written from. The prompt asks the stage not to open
    them, and mostly it doesn't — but a barrier that holds because the agent
    chose to honour it is not a barrier, it is a convention, and the one place
    it fails is the run where the stage most wants an answer.

    So the documents leave the directory while the stage runs and come back
    afterwards. Restored rather than superseded, unlike the stage's own prior
    output: every later stage is meant to read these, and the blind stage is the
    only one that must not.
    """
    if not artifacts.is_dir():
        return []
    attempt = 1
    while (dest := run_dir / WITHHELD_DIRNAME / f"{stage_name}-{attempt}").exists():
        attempt += 1
    moved: list[tuple[Path, Path]] = []
    for entry in sorted(artifacts.iterdir()):
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / entry.name
        shutil.move(str(entry), str(target))
        moved.append((target, entry))
    if moved:
        log(f"[{stage_name}] sees no prior stage — withheld {len(moved)} artifact(s) for this stage")
    return moved


def _restore_artifacts(
    moved: list[tuple[Path, Path]],
    log: Callable[[str], None],
) -> None:
    """Put withheld documents back, without overwriting what the stage wrote.

    A name collision means the blind stage wrote a document belonging to another
    stage, which is a pipeline fault worth seeing rather than resolving: neither
    file is destroyed, the stage's own output stays where it wrote it, and the
    withheld one is left in place with a line saying so.
    """
    for source, target in moved:
        if target.exists():
            log(f"kept {source} out of artifacts/: the stage wrote its own {target.name}")
            continue
        shutil.move(str(source), str(target))


def _gate_says_skip(stage: Stage, artifacts: Path, log: Callable[[str], None]) -> bool:
    """Evaluate a stage's skip_if gate against the artifacts directory.

    Every outcome is logged, including the one where the gate cannot be
    evaluated. A gate that quietly stops gating is worse than no gate: the run
    still looks conditional, so you go on believing a check is protecting you
    long after it stopped running. An unreadable gate therefore fails open and
    says so, loudly, rather than silently skipping the rest of a pipeline.
    """
    gate = stage.skip_if
    if gate is None:
        return False

    path = (artifacts / gate.file).resolve()
    # resolve() follows symlinks, so this also catches a gate file an agent
    # pointed outside the artifacts directory.
    if not path.is_relative_to(artifacts.resolve()):
        log(
            f"[{stage.name}] GATE NOT EVALUATED: {gate.file!r} resolves outside "
            f"the artifacts directory — running the stage anyway."
        )
        return False
    if not path.is_file():
        log(
            f"[{stage.name}] GATE NOT EVALUATED: {gate.file!r} not found — the "
            f"stage that writes it may have failed. Running the stage anyway."
        )
        return False

    if gate.needle in path.read_text():
        log(f"[{stage.name}] gate: {gate.file} contains {gate.needle!r} — skipping.")
        return True
    log(f"[{stage.name}] gate: {gate.file} does not contain {gate.needle!r} — running.")
    return False


def _resolve_next(
    stage: Stage,
    i: int,
    last_index: int,
    index_of: dict[str, int],
    artifacts: Path,
    log: Callable[[str], None],
) -> int:
    """Read a router stage's decision.json and return the index of the stage to
    run next: i+1 (advance), i (again), the terminal (done), or a named target.

    Fail-safe by design, like the skip_if gate: a stage that can't make a
    legible, in-bounds, declared choice does not get to steer the run — the
    engine advances (i+1) and says why. That keeps a garbled, out-of-bounds, or
    unauthorized decision from either looping forever or jumping somewhere the
    pipeline never sanctioned.
    """
    advance = i + 1
    path = (artifacts / DECISION_FILE).resolve()
    if not path.is_relative_to(artifacts.resolve()):
        log(
            f"[{stage.name}] DECISION IGNORED: {DECISION_FILE} resolves outside the "
            f"artifacts directory — advancing."
        )
        return advance
    if not path.is_file():
        log(f"[{stage.name}] no {DECISION_FILE} written — advancing.")
        return advance
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        log(f"[{stage.name}] DECISION IGNORED: {DECISION_FILE} unreadable ({exc}) — advancing.")
        return advance
    if not isinstance(data, dict):
        log(f"[{stage.name}] DECISION IGNORED: {DECISION_FILE} is not a JSON object — advancing.")
        return advance

    nxt = data.get("next")
    if nxt is None and data.get("done") is True:  # accept {"done": true} too
        nxt = NEXT_DONE
    reason = data.get("reason")
    suffix = f" — {reason}" if reason else ""
    if not isinstance(nxt, str) or not nxt:
        log(f"[{stage.name}] decision names no 'next' — advancing. Got: {data!r}")
        return advance
    if nxt == NEXT_ADVANCE:
        log(f"[{stage.name}] decision: advance{suffix}")
        return advance
    if nxt == NEXT_AGAIN:
        log(f"[{stage.name}] decision: run again{suffix}")
        return i
    if nxt == NEXT_DONE:
        log(f"[{stage.name}] decision: done{suffix}")
        return last_index
    if nxt not in (stage.decide_targets or ()):
        log(
            f"[{stage.name}] DECISION IGNORED: {nxt!r} is not one of this stage's "
            f"declared targets {stage.decide_targets} — advancing."
        )
        return advance
    if nxt not in index_of:
        log(
            f"[{stage.name}] DECISION IGNORED: {nxt!r} is not a stage in this run "
            f"(dropped by --stages?) — advancing."
        )
        return advance
    log(f"[{stage.name}] decision: route to {nxt!r}{suffix}")
    return index_of[nxt]


def _looks_like_session_id(session_id: str | None) -> bool:
    """A real Claude Code session ID is a bare UUID. The fan-out merge stores a
    human sentence in that slot instead ('(multiple — ...)'); skip those."""
    return bool(session_id) and " " not in session_id and "(" not in session_id


def _wall_clock() -> str:
    """Local wall clock, for stamping stage boundaries. Deliberately not the
    monotonic clock: this one is for a human lining the log up against a
    container's or a harness's own timestamps."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log_stage_elapsed(log: Callable[[str], None], name: str, started: float) -> None:
    """Close a stage's timing bracket. Monotonic, so a clock adjustment
    mid-run cannot produce a negative duration."""
    elapsed = format_duration(time.monotonic() - started)
    log(f"[{name}] stage finished in {elapsed} [{_wall_clock()}]")


def _sessions_root() -> Path:
    """Where Claude Code is writing session transcripts for this process.

    Normally ~/.claude, but the CLI lets CLAUDE_CONFIG_DIR move it, and things
    that run the engine inside a container do exactly that so the transcripts
    land on a mounted volume instead of dying with the container. Resolving it
    the same way the CLI does keeps _resolve_transcripts pointed at the files
    that actually exist; hardcoding home means cross-stage transcript access
    silently goes dark the moment anyone sets the variable.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".claude"
    return root / "projects"


def _resolve_transcripts(results: list[StageResult]) -> list[dict]:
    """Map each prior session's ID to its on-disk transcript.

    Claude Code writes every session to <config dir>/projects/<cwd-slug>/<id>.jsonl.
    We don't reconstruct the slug (that couples us to Claude Code's path scheme);
    we glob for the ID, which is unique. Sessions with no transcript on disk are
    simply left out — a router still has the artifacts and the handoffs.

    The IDs come from this run's own StageResults and are shape-checked before
    they reach the glob, so no agent-supplied string can widen the search, and a
    directory only becomes readable to the next stage by containing a transcript
    named for a session this run opened.
    """
    projects = _sessions_root()
    if not projects.is_dir():
        return []
    found: list[dict] = []
    seen: set[str] = set()
    for r in results:
        sid = r.session_id
        if not _looks_like_session_id(sid) or sid in seen:
            continue
        seen.add(sid)
        matches = sorted(projects.glob(f"*/{sid}.jsonl"))
        if matches:
            found.append({"stage": r.stage, "session_id": sid, "path": matches[0]})
    return found


def _transcript_context(transcripts: list[dict]) -> str | None:
    """A prompt block telling this stage it can reach every prior session's
    transcript — and how to use them without drowning in them.

    Handed to every stage, not just routers: the handoff carries one lossy
    summary, but the full record of what each earlier session read, ran, and
    concluded is on disk, keyed by session ID. A terminal write-up, a fan-out
    item, or a repeat pass may all need a specific fact an earlier summary
    dropped. The guidance is deliberately narrow — pull the fact, don't read
    the file — because a fresh stage exists precisely to *not* carry the whole
    history, and slurping a transcript whole would refill the context that the
    handoff bottleneck was there to drain.
    """
    if not transcripts:
        return None
    lines = "\n".join(
        f"- {t['stage']} (session {t['session_id']}): {t['path']}" for t in transcripts
    )
    return (
        "\n\n--- PRIOR SESSION TRANSCRIPTS ---\n"
        "The handoff summaries above are lossy. Each prior stage ran as its own\n"
        "Claude Code session, and its full transcript is on disk at the path below\n"
        "— the complete record of what it read, ran, and concluded, keyed by its\n"
        "session ID. If you need a specific detail a summary dropped (an exact\n"
        "value, a path it touched, why it ruled something out), go get it from the\n"
        "transcript that holds it.\n\n"
        "Get it strategically. These files are large JSONL: grep for the term you\n"
        "need and read only the slice around it. Do NOT read a transcript in full —\n"
        "pulling a whole session into this one blows your context and defeats the\n"
        "point of running as a fresh, focused stage. Extract the fact, not the file.\n\n"
        f"{lines}\n"
        "--- END PRIOR SESSION TRANSCRIPTS ---\n"
    )


def _prior_session_access(
    results: list[StageResult],
) -> tuple[str | None, list[str] | None]:
    """The two things a stage needs to reach prior sessions: a prompt block
    listing their transcripts, and the read-access dirs that make the paths in
    it openable. Both are derived only from this run's own session IDs, so no
    agent-supplied path ever reaches add_dirs. Empty (None, None) before the
    first session produces a transcript on disk."""
    transcripts = _resolve_transcripts(results)
    context = _transcript_context(transcripts)
    add_dirs = sorted({str(t["path"].parent) for t in transcripts}) or None
    return context, add_dirs


async def run_loop(
    task: str,
    workspace: Path,
    pipeline: Pipeline,
    runs_dir: Path,
    run_dir: Path | None = None,
    docs: Path | None = None,
    permission_mode: str = "auto",
    max_turns: int | None = None,
    model: str | None = None,
    interactive: bool = False,
    stage_gates: bool = True,
    max_hops: int | None = None,
    setting_sources: tuple[str, ...] = DEFAULT_SETTING_SOURCES,
    log: Callable[[str], None] = default_log,
) -> list[StageResult]:
    """Walk the pipeline's stages against a run directory.

    runs_dir is not optional, because the run directory is where the stages
    talk to each other: the artifacts one stage writes are what the next one
    reads. The workspace only ever receives the deliverable.

    docs is the operator's documents directory, handed to any stage whose
    prompt asks for it as {docs}. It is the exception to the sentence above:
    everything in a run directory is one agent talking to the next, named by a
    timestamp and read by nobody afterward, which is the wrong shape for a
    pipeline whose whole deliverable is a document somebody has to read. Only
    stages that name the placeholder are granted write access to it — see the
    grant below.

    A fresh directory is minted under runs_dir unless run_dir names an existing
    one to continue in. Continuing is how a run that ended on a question gets
    answered without being re-derived: the earlier pass's documents are still
    there, so the stages that already did their work don't do it again. The
    pass takes its own log file and snapshots what it inherits before writing.

    Every plain sequential stage (not fan-out, not a fixed `repeat: N`) is a
    router: after it runs, it may write decision.json to advance, run again, jump
    to a declared stage, or finish. The last stage is the terminal — it always
    runs once at the end and never routes. max_hops bounds total stage executions
    so a router can never loop forever; on exhaustion routing closes and the run
    walks forward through whatever stages remain, so it always ends with a
    handback and the last revision still meets the stages that check it.
    """
    # monotonic, not wall-clock: this measures how long the run took, and must
    # not jump if the system clock is adjusted mid-run.
    started = time.monotonic()
    workspace.mkdir(parents=True, exist_ok=True)
    # Created only when some stage actually asks to publish: a `feature` or
    # `bugfix` run leaves its deliverable in the workspace as code and has no
    # business minting a folder in the operator's documents directory to leave
    # empty. Up front rather than lazily, so the publishing stage finds the
    # directory already there instead of being the thing that creates it.
    publishes = docs is not None and any(
        "{docs}" in stage.prompt_template for stage in pipeline.stages
    )
    if publishes:
        docs.mkdir(parents=True, exist_ok=True)
    resuming = run_dir is not None
    run_dir = reuse_run_dir(run_dir, runs_dir) if resuming else new_run_dir(runs_dir)
    artifacts = run_dir / ARTIFACTS_DIRNAME
    log(f"Run directory: {run_dir}{' (resumed)' if resuming else ''}")
    channel.emit(
        "run_started", run_dir=str(run_dir), pipeline=pipeline.name, task=task,
        stages=[s.name for s in pipeline.stages],
        workspace=str(workspace), artifacts=str(artifacts),
    )

    stages = pipeline.stages
    last_index = len(stages) - 1
    index_of = {stage.name: idx for idx, stage in enumerate(stages)}
    if max_hops is None:
        max_hops = _default_hop_budget(len(stages))

    if resuming:
        # The gate the previous pass left is still on disk, and gates are how a
        # pipeline stops: a run that ended on STOP has a verdict saying so, and
        # resuming into it skips every stage gated on that verdict. The run then
        # reaches its terminal having done nothing and writes a second handback
        # saying what the first one said — which looks like an answer that
        # didn't take, rather than an answer that was never read. So say it
        # here, where the file that decides it can be named.
        first = stages[0] if stages else None
        if first is not None and _gate_says_skip(first, artifacts, lambda _msg: None):
            gate = first.skip_if
            raise ValueError(
                f"--run-dir {run_dir}: {gate.file} still contains {gate.needle!r}, so "
                f"'{first.name}' would skip and this pass would do nothing. Resolve "
                f"{artifacts / gate.file} before resuming — the verdict is the "
                f"previous pass's answer, and only you can say it has been dealt with."
            )
        _snapshot_for_resume(artifacts, log)

    # Claimed once, so every checkpoint below rewrites this pass's own log
    # instead of adding another beside it.
    log_name = next_log_name(run_dir)

    results: list[StageResult] = []
    prev: StageResult | None = None
    # Which artifact documents each stage left behind, so a stage that runs
    # again can be told apart from the run's other writers. Only consulted for
    # `handoff: none`; every other stage is meant to build on what it wrote.
    wrote_by_stage: dict[str, set[str]] = {}
    # Non-empty only while a blind stage is running; the loop can also leave it
    # that way by breaking out mid-stage, which is why it is put back below too.
    withheld: list[tuple[Path, Path]] = []
    # None until the first boundary at which pins exist, then the fixed list of
    # workspace files carrying pin ids, with the assertion text last seen in
    # them. `PINS.md` says what the run promises; these say whether it still
    # asserts it, and only watching both catches a withdrawal that edits one and
    # leaves the other alone.
    pin_files: list[Path] | None = None
    assertions_before: dict[str, str] = {}
    i = 0
    hops = 0

    def checkpoint() -> None:
        """Persist what the run knows so far, at a stage boundary.

        A stage's summary and session ID are the handoff — and until this is
        called they exist only in the list above. A run killed mid-pipeline
        (a lost stdin, a dropped connection, a machine asleep) would otherwise
        leave its artifacts with nothing recording which stages produced them,
        what they handed on, or what they cost.
        """
        write_run_log(
            run_dir,
            task,
            pipeline.name,
            results,
            time.monotonic() - started,
            complete=False,
            log_name=log_name,
        )

    while i < len(stages):
        stage = stages[i]
        is_last = i == last_index
        # A router is a plain sequential stage that isn't the terminal: it gets
        # to decide what runs next. Fan-out can't (parallel agents would race to
        # write one decision) and a fixed repeat is a shape, not a decision.
        is_router = not stage.fan_out and stage.repeat == 1 and not is_last

        if _gate_says_skip(stage, artifacts, log):
            # Recorded, not just logged: a skipped stage that leaves no trace
            # in the run log is indistinguishable from one that never existed.
            results.append(
                StageResult(
                    stage=stage.name,
                    session_id=None,
                    summary=(
                        f"skipped: {stage.skip_if.file} contains "
                        f"{stage.skip_if.needle!r}"
                    ),
                    is_error=False,
                )
            )
            channel.emit(
                "stage_skipped", stage=stage.name,
                reason=f"{stage.skip_if.file} contains {stage.skip_if.needle!r}",
            )
            checkpoint()
            i += 1
            continue
        if interactive and stage_gates:
            decision = await interaction.stage_gate(stage.name)
            if decision == "skip":
                log(f"[{stage.name}] skipped by operator.")
                channel.emit("stage_skipped", stage=stage.name, reason="skipped by operator")
                i += 1
                continue
            if decision == "quit":
                log("Loop stopped by operator.")
                break
        log("")
        banner = f"===== STAGE: {stage.name.upper()}"
        if stage.fan_out:
            banner += f" (parallel over {stage.fan_out}"
            banner += f", {stage.repeat} passes each)" if stage.repeat > 1 else ")"
        elif stage.repeat > 1:
            banner += f" ({stage.repeat} passes)"
        # Stamped, and paired with an elapsed line when the stage returns. The
        # sum of the stages is not the run's wall clock — anything spent between
        # them (SDK setup, retry backoff, a stalled handoff) shows up nowhere
        # else, and without two stamps to subtract you cannot tell a slow stage
        # from a long wait before one. A benchmark trial has been observed
        # spending three hours outside its stages with no trace of where.
        log(f"{banner} ===== [{_wall_clock()}]")
        channel.emit("stage_started", stage=stage.name)
        stage_started = time.monotonic()

        # Every stage may reach back into the prior sessions' transcripts, not
        # just routers: a terminal write-up or a fan-out item can need a fact an
        # earlier summary dropped just as much as a router weighing a route-back.
        # The block only lists sessions whose transcript is on disk (none before
        # the first), so the opening stage gets nothing extra.
        #
        # Except under `handoff: none`, where the whole point is that this stage
        # forms its own view. Withholding the summaries while handing over the
        # transcripts they were drawn from would be a barrier in name only, so
        # the pointers go too and the stage sees the request and the workspace.
        # Snapshot before anything is withheld: a document that leaves and comes
        # back keeps its mtime, so it matches here and is not misread below as
        # something this stage wrote.
        artifacts_before = _artifact_mtimes(artifacts)
        escalations_before = _read_escalations(artifacts)
        pins_before = _pin_claims(artifacts)
        # The file list is found once, on the first boundary where pins exist,
        # and reused after that. Before the pin stage runs there is nothing to
        # find, and walking the workspace to learn that on every stage would be
        # the most expensive way to get an empty dict.
        if pins_before and pin_files is None:
            pin_files = _pin_assertion_files(workspace)
            assertions_before = _pin_assertions(pin_files)

        if stage.handoff == "none":
            extra_context, extra_add_dirs = None, []
            _supersede_prior_outputs(run_dir, artifacts, stage.name, wrote_by_stage, log)
            withheld = _withhold_artifacts(run_dir, artifacts, stage.name, log)
        else:
            extra_context, extra_add_dirs = _prior_session_access(results)
            _snapshot_prior_outputs(artifacts, stage.name, wrote_by_stage, log)

        # A stage runs with cwd=workspace and add_dirs=[artifacts]; anything
        # else is denied. The documents directory is granted only to the stages
        # that ask for it by name, so a pipeline's publishing stage can write
        # there and the ten stages before it — which have no business in the
        # operator's own folder — still cannot.
        stage_docs = ""
        if publishes and "{docs}" in stage.prompt_template:
            stage_docs = str(docs)
            extra_add_dirs = [*(extra_add_dirs or []), stage_docs]


        if stage.fan_out:
            try:
                stage_results, prev_candidate = await run_fan_out(
                    stage,
                    pipeline,
                    task,
                    workspace,
                    artifacts,
                    prev,
                    docs=stage_docs,
                    extra_context=extra_context,
                    extra_add_dirs=extra_add_dirs,
                    permission_mode=permission_mode,
                    max_turns=max_turns,
                    model=model,
                    interactive=interactive,
                    setting_sources=setting_sources,
                    log=log,
                    checkpoint_path=fan_out_checkpoint_path(run_dir, stage.name),
                )
            except FanOutInterrupted as exc:
                # Not a failure of the pipeline — an outage partway through one
                # stage. The finished items are on disk and recorded, so stop
                # here and say exactly how to pick it back up, rather than walk
                # on to stages that would build on a half-finished one.
                exc.resume_hint = (
                    f"--run-dir {run_dir} --stages "
                    + ",".join(s.name for s in stages[i:])
                )
                log(
                    f"[{stage.name}] {exc}\n"
                    f"[{stage.name}] finished items are kept. Resume with the same "
                    f"command plus: {exc.resume_hint}"
                )
                results.append(
                    StageResult(stage=stage.name, session_id=None, summary=str(exc), is_error=True)
                )
                checkpoint()
                raise
            except ValueError as exc:
                log(f"[{stage.name}] {exc} — stopping the loop.")
                results.append(
                    StageResult(stage=stage.name, session_id=None, summary=str(exc), is_error=True)
                )
                break
            results.extend(stage_results)
            _log_stage_elapsed(log, stage.name, stage_started)
            channel.emit(
                "stage_finished", stage=stage.name, is_error=prev_candidate.is_error,
                cost_usd=sum(r.cost_usd or 0 for r in stage_results),
            )
            hops += 1
            if prev_candidate.is_error:
                log(f"[{stage.name}] every item failed — stopping the loop.")
                break
            prev = prev_candidate
        else:
            if is_router:
                # Start clean: a router that runs but writes nothing must read as
                # "advance", never inherit the stale decision an earlier stage
                # left in this same file.
                (artifacts / DECISION_FILE).unlink(missing_ok=True)

            chain = await run_chain(
                stage,
                pipeline,
                task,
                workspace,
                artifacts,
                prev,
                docs=stage_docs,
                extra_context=extra_context,
                extra_add_dirs=extra_add_dirs,
                permission_mode=permission_mode,
                max_turns=max_turns,
                model=model,
                interactive=interactive,
                setting_sources=setting_sources,
                log=log,
            )
            results.extend(chain)
            _log_stage_elapsed(log, stage.name, stage_started)
            channel.emit(
                "stage_finished", stage=stage.name, is_error=chain[-1].is_error,
                cost_usd=sum(r.cost_usd or 0 for r in chain),
            )
            hops += 1
            if chain[-1].is_error:
                log(f"[{stage.name}] stage errored — stopping the loop.")
                break
            prev = chain[-1]

        # The blind stage is done, so the documents it could not see come back
        # before any later stage looks for them.
        _restore_artifacts(withheld, log)
        withheld = []

        # Attribute what landed in artifacts/ to the stage that just ran. A
        # stage reached twice replaces its earlier entry, so the record always
        # names the documents currently on disk rather than accumulating the
        # ones already superseded.
        # Pins first: a dropped one appends to the escalation record, and the
        # announcement below should carry it rather than report it a stage late.
        # Claims first: the report on the tests quotes them, and a stage that
        # revised both should have the document's version of the promise read
        # before the code's version is compared against it.
        pins_before = _report_pin_changes(artifacts, pins_before, stage.name, log)
        if pin_files is not None:
            assertions_before = _report_pin_assertion_changes(
                artifacts, assertions_before, pin_files, pins_before, stage.name, log
            )
        _restore_escalations(artifacts, escalations_before, log)
        _report_new_escalations(artifacts, escalations_before, log)
        wrote_by_stage[stage.name] = _touched_since(artifacts, artifacts_before)

        # The stage is done and its handoff is decided: write it down before
        # spending another session, so this boundary is the worst case a crash
        # can cost.
        checkpoint()

        if is_last:
            i += 1  # the terminal ran; leave the loop
            continue
        nxt = _resolve_next(stage, i, last_index, index_of, artifacts, log) if is_router else i + 1
        # Out of budget: refuse to go backwards, but keep going forwards. Jumping
        # straight to the terminal here used to skip every remaining stage, and
        # the stages it skipped are the checking ones — so the last revision a
        # run makes, which is the one most likely to have quietly reduced scope
        # because it was made to satisfy a route-back, was the one revision
        # guaranteed never to be checked. Advancing still terminates: every step
        # from here moves at least one stage on.
        if hops >= max_hops and nxt <= i:
            # Recorded here rather than told to the stage. A stage that knew how
            # much budget was left could plan around it — hold a finding back,
            # or file one because it is the last chance — and what it decides
            # should turn on the work, not on the engine's accounting. So the
            # request it made and the override are logged where a human and the
            # handback can see them, and the stage is never asked to reason
            # about a number it cannot act on anyway.
            log(
                f"[{stages[i].name}] DECISION OVERRIDDEN: asked for "
                f"{stages[nxt].name!r}, but the hop budget ({max_hops}) is spent — "
                f"advancing. Routing is closed; the remaining stages still run, in "
                f"order, so the last revision is still checked."
            )
            nxt = i + 1
        i = nxt

    # A stage that failed mid-run broke out of the loop above without restoring,
    # and the run still writes its log and hands back what it has — so the
    # artifacts directory must be whole by the time anyone reads it.
    _restore_artifacts(withheld, log)

    elapsed = time.monotonic() - started
    path = write_run_log(run_dir, task, pipeline.name, results, elapsed, log_name=log_name)
    log(f"\nRun log written to {path}")
    log(f"Stage artifacts are in {artifacts}")
    log(f"Total time: {format_duration(elapsed)}")

    # A run driven in the background ends into a log, and the escalation record
    # is the one artifact whose whole point is that a human sees it. Reported
    # last and in full: the stage that raised it may have been five stages ago,
    # and nothing else in this summary would mention it.
    raised = _read_escalations(artifacts).strip()
    if raised:
        quoted = "\n".join(f"  | {line}" for line in raised.splitlines())
        log(
            f"\n===== {ESCALATIONS_FILE}: raised during this run, unresolved =====\n"
            f"{quoted}\n"
            f"Full record: {artifacts / ESCALATIONS_FILE}"
        )
    channel.emit("run_complete", run_dir=str(run_dir), escalations=bool(raised))

    return results
