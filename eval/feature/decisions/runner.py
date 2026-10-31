"""Replay one frozen decision against the current prompts, N times.

A pipeline prompt is the load-bearing logic of this engine and the only part
with no test suite. Today the way to learn what a rule change did is to run a
full trial — hours, and tens of dollars, and the answer arrives days later mixed
in with everything else that moved.

This runs the *decision* instead of the run. A case freezes the inputs a stage
actually had at the moment it made a call — the request, the scope entry, what
the earlier passes reported, the source tree it could read — and asks the
current prompts to make that call again, in one short session with no build and
no suite. The stage sees what it saw; it does not see how it answered.

What that buys and what it does not:

- A rule that cannot flip a decision we already know the prompts get wrong is
  not worth a live trial. That filter is the whole point, and it costs cents.
- Passing here is *not* passing. A frozen decision is easier than a live one:
  the real stage builds its own context over an hour and can wander somewhere
  this replay never goes. Treat a flip as permission to spend the $50, never as
  the result.

Cases live in ``cases/<name>/``; see README.md for the format.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from orchestra_engine import handoff_tool
from orchestra_engine.engine.session import run_stage

# The same `--- key: value ---` block stage files use. Reused rather than
# re-implemented so a case file and a stage file never drift into two dialects
# of the same format.
from orchestra_engine.pipeline import BUILTIN_PIPELINES_DIR, _split_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = Path(__file__).parent / "cases"

# Checkouts are keyed by commit and reused across replays and across runs of
# this tool. Kept outside the repo: it is fetched third-party source, it is
# large next to anything else here, and nothing about it belongs in a diff.
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "loop-decision-cache"

DECISION_RE = re.compile(r"^\s*DECISION:\s*([A-Za-z0-9_-]+)", re.MULTILINE)

# The closing instruction, and the clause suspected of biasing the answer.
# A stage told it cannot check anything has a reason to prefer whichever option
# demands no action — which would show up as exactly the result this harness
# produced on its first two cases, and would have nothing to do with the prompts.
_CLOSING = (
    "Everything this run has built is on the branch and already measured; the "
    "stage documents in the artifacts directory carry those measurements. "
    "{clause}Decide on the record you have, plus whatever you read in the source tree."
)
_MEASURE_CLAUSE = "You are not being asked to rebuild or re-measure it, and you cannot — "

# Harness ablations: one-variable changes to the *replay*, holding the case
# fixed, to tell "the prompts decided this" from "the harness did". A tool that
# grades prompts has to be able to rule itself out first, so these are a shipped
# feature rather than something reconstructed by hand each time.
ABLATIONS = ("none", "may-measure", "flip-answers")


@dataclass
class _ReplayHandoff:
    """Stands in for the engine's Handoff when rendering `{handoff}`.

    The replay serves its handoff through the same in-process tool a live stage
    reads, so the previous stage's account arrives marked as somebody's account
    rather than spliced into this stage's instructions. That distinction is the
    reason the engine has the tool at all, and a harness that flattened it would
    be testing a prompt nobody runs.
    """

    label: str = "previous stage"


@dataclass
class Case:
    """One frozen decision."""

    name: str
    directory: Path
    question: str
    rules: list[Path]
    documents: list[Path]
    answers: list[str]
    correct: str
    task: Path | None = None
    handoff: Path | None = None
    observed: str | None = None
    repo: str | None = None
    commit: str | None = None
    sparse: str | None = None

    @property
    def is_control(self) -> bool:
        """True when the live run already answered this one correctly.

        Such a case is not evidence that a rule works — it is the guard against a
        rule that gets one decision right by answering every decision the same
        way. A suite made only of known failures scores that rule perfectly.
        """
        return self.observed is not None and self.observed == self.correct


@dataclass
class Replay:
    """One session's answer to the case."""

    index: int
    decision: str | None  # None when the session never emitted a parseable line
    summary: str
    cost_usd: float | None = None
    num_turns: int | None = None
    is_error: bool = False


@dataclass
class CaseResult:
    case: Case
    replays: list[Replay] = field(default_factory=list)

    @property
    def tally(self) -> Counter:
        return Counter(r.decision or "(unparsed)" for r in self.replays)

    @property
    def correct_count(self) -> int:
        return sum(1 for r in self.replays if r.decision == self.case.correct)

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.replays)


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def make_log(path: Path | None = None):
    """A logger that cannot kill the session it is reporting on.

    The engine streams thinking blocks prefixed with an emoji. On a console
    whose encoding cannot represent it — cp1252, which is the Windows default
    below Python 3.15 — a bare ``print`` raises UnicodeEncodeError *inside* the
    message loop, so the stage dies mid-answer and all three retries die at the
    same block. Console output is replaced lossily; the file copy, if asked for,
    keeps the real characters.
    """
    handle = path.open("a", encoding="utf-8") if path else None
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"

    def log(line: str) -> None:
        if handle:
            handle.write(line + "\n")
            handle.flush()
        safe = line.encode(encoding, "replace").decode(encoding, "replace")
        print(safe, flush=True)

    return log


def load_case(directory: Path, pipelines_dir: Path = BUILTIN_PIPELINES_DIR) -> Case:
    """Read ``<directory>/case.md`` into a Case, resolving every path it names.

    Paths are resolved at load time rather than at run time so a typo in a case
    fails immediately and free, instead of after the first session has been paid
    for.
    """
    case_file = directory / "case.md"
    if not case_file.is_file():
        raise FileNotFoundError(f"{directory}: no case.md")

    options, question = _split_frontmatter(case_file.read_text(encoding="utf-8"))
    missing = {"rules", "answers", "correct"} - options.keys()
    if missing:
        raise ValueError(f"{case_file}: missing frontmatter key(s): {', '.join(sorted(missing))}")

    pipeline = options.get("pipeline", "feature")
    rules: list[Path] = []
    for name in _split_list(options["rules"]):
        path = pipelines_dir / pipeline / name
        if not path.is_file():
            raise FileNotFoundError(f"{case_file}: rules names {path}, which does not exist")
        rules.append(path)

    def resolve(key: str) -> list[Path]:
        found = []
        for name in _split_list(options.get(key, "")):
            path = directory / name
            if not path.is_file():
                raise FileNotFoundError(f"{case_file}: {key} names {path}, which does not exist")
            found.append(path)
        return found

    documents = resolve("documents")
    task_files = resolve("task")
    handoff_files = resolve("handoff")
    if len(task_files) > 1 or len(handoff_files) > 1:
        raise ValueError(f"{case_file}: task and handoff take at most one file each")

    answers = _split_list(options["answers"])
    correct = options["correct"].strip()
    if correct not in answers:
        raise ValueError(f"{case_file}: correct={correct!r} is not one of answers={answers}")
    observed = options.get("observed", "").strip() or None
    if observed and observed not in answers:
        raise ValueError(f"{case_file}: observed={observed!r} is not one of answers={answers}")

    return Case(
        name=options.get("name", directory.name),
        directory=directory,
        question=question.strip(),
        rules=rules,
        documents=documents,
        answers=answers,
        correct=correct,
        task=task_files[0] if task_files else None,
        handoff=handoff_files[0] if handoff_files else None,
        observed=observed,
        repo=options.get("repo"),
        commit=options.get("commit"),
        sparse=options.get("sparse"),
    )


def materialize_workspace(case: Case, cache_root: Path, log) -> Path | None:
    """Fetch the case's source tree once, and return the cached checkout.

    Returns None for a case that declares no repo — a decision that needs no
    code to look at still runs, with an empty workspace.

    The checkout is the reason a replay can test a rule like "look for how this
    codebase already resolved this". Hand the stage a curated list of facts
    instead and every such rule passes trivially, because the finding it was
    supposed to make has already been made for it.
    """
    if not case.repo:
        return None
    if not case.commit:
        raise ValueError(f"{case.name}: repo is set but commit is not — a replay must be pinned")

    dest = cache_root / f"{case.name}-{case.commit[:12]}"
    if (dest / ".git").is_dir():
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"[{case.name}] fetching {case.repo} @ {case.commit[:12]} -> {dest}")
    tmp = dest.with_suffix(".partial")
    shutil.rmtree(tmp, ignore_errors=True)

    def git(*args: str, cwd: Path | None = None) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    # blob:none + sparse keeps this to the source tree. Some of these repos
    # carry a test suite in five figures, and none of it is what the stage reads.
    git("clone", "--quiet", "--filter=blob:none", "--no-checkout", case.repo, str(tmp))
    # Cloning on Windows with git's default autocrlf rewrites every line ending,
    # which changes the bytes the stage reads and, on a shell script, what runs.
    git("config", "core.autocrlf", "false", cwd=tmp)
    if case.sparse:
        git("sparse-checkout", "set", *_split_list(case.sparse), cwd=tmp)
    git("checkout", case.commit, cwd=tmp)
    tmp.rename(dest)
    return dest


def build_prompt(case: Case, artifacts: Path, ablation: str = "none") -> str:
    """Assemble the one prompt a replay session gets.

    The stage file is rendered exactly the way ``Pipeline.build_prompt`` renders
    it — frontmatter split off, the same placeholders filled — so what arrives is
    the prompt the engine would have sent, not a paraphrase of it. A rule that
    only reads correctly after a harness reformats it is not the rule under test.

    Deliberately not framed as a test. The stage is told it is the stage, given
    its own instructions and pointed at its documents — because an agent told it
    is being measured on whether it follows a rule is a different agent from the
    one that writes the run.
    """
    task = case.task.read_text(encoding="utf-8").strip() if case.task else ""
    handoff = handoff_tool.pointer([_ReplayHandoff()]) if case.handoff else ""

    parts: list[str] = []
    for path in case.rules:
        _options, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        parts.append(
            body.format(
                task=task,
                handoff=handoff,
                item="",
                iteration=1,
                repeat=1,
                artifacts=str(artifacts),
            )
        )

    if ablation not in ABLATIONS:
        raise ValueError(f"unknown ablation {ablation!r} — one of {', '.join(ABLATIONS)}")

    order = list(reversed(case.answers)) if ablation == "flip-answers" else list(case.answers)
    closing = _CLOSING.format(clause="" if ablation == "may-measure" else _MEASURE_CLAUSE)

    parts.append(
        "---\n\n# The decision in front of you\n\n"
        f"{case.question}\n\n"
        f"{closing}\n\n"
        "Write your reasoning out fully. Do not write any stage document for this. "
        "End your final message with exactly these two lines and nothing after them:\n\n"
        f"DECISION: <{' | '.join(order)}>\n"
        "BECAUSE: <one sentence>"
    )
    return "\n\n".join(parts)


def parse_decision(summary: str, answers: list[str]) -> str | None:
    """The last DECISION line the session emitted, if it names a known answer.

    Last rather than first: a session that quotes the output contract while
    thinking out loud, then answers, should be graded on the answer.
    """
    lowered = {a.lower(): a for a in answers}
    for match in reversed(DECISION_RE.findall(summary)):
        if match.lower() in lowered:
            return lowered[match.lower()]
    return None


async def _one_replay(
    case: Case,
    index: int,
    source: Path | None,
    scratch: Path,
    model: str | None,
    max_turns: int | None,
    permission_mode: str,
    ablation: str,
    semaphore: asyncio.Semaphore,
    log,
) -> Replay:
    async with semaphore:
        workspace = scratch / f"replay-{index}" / "workspace"
        artifacts = scratch / f"replay-{index}" / "artifacts"
        workspace.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)
        # The stage documents go where a live stage would find them: on disk, in
        # the artifacts directory, under their own names. Reading them is part of
        # the decision, and a stage handed their contents in its prompt has
        # already been told what they say.
        for document in case.documents:
            shutil.copy2(document, artifacts / document.name)
        prompt = build_prompt(case, artifacts, ablation)
        (artifacts.parent / "prompt.txt").write_text(prompt, encoding="utf-8")
        if source is not None:
            # A per-replay copy, so a session that writes cannot reach the cache
            # or the sibling replays. `.git` is skipped: it is the bulk of the
            # tree and a pinned checkout has no history worth reading.
            shutil.copytree(
                source, workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git")
            )

        label = f"{case.name}#{index}"
        try:
            result = await run_stage(
                stage_name=case.name,
                prompt=prompt,
                workspace=workspace,
                artifacts=artifacts,
                permission_mode=permission_mode,
                max_turns=max_turns,
                model=model,
                log=log,
                label=label,
                handoff_body=(
                    case.handoff.read_text(encoding="utf-8") if case.handoff else None
                ),
            )
        except Exception as exc:  # a dead session is a lost sample, not a lost run
            log(f"[{label}] failed: {type(exc).__name__}: {exc}")
            return Replay(index=index, decision=None, summary=f"{type(exc).__name__}: {exc}", is_error=True)

        decision = parse_decision(result.summary, case.answers)
        log(f"[{label}] decision: {decision or '(unparsed)'}")
        return Replay(
            index=index,
            decision=decision,
            summary=result.summary,
            cost_usd=result.cost_usd,
            num_turns=result.num_turns,
            is_error=result.is_error,
        )


async def run_case(
    case: Case,
    trials: int,
    scratch: Path,
    cache_root: Path = DEFAULT_CACHE,
    model: str | None = None,
    max_turns: int | None = None,
    permission_mode: str = "acceptEdits",
    ablation: str = "none",
    concurrency: int = 3,
    log=None,
) -> CaseResult:
    log = log or make_log()
    source = materialize_workspace(case, cache_root, log)
    scratch.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    replays = await asyncio.gather(
        *(
            _one_replay(
                case, i, source, scratch, model, max_turns, permission_mode, ablation, semaphore, log
            )
            for i in range(1, trials + 1)
        )
    )
    return CaseResult(case=case, replays=list(replays))


def report(result: CaseResult) -> str:
    case = result.case
    lines = [
        "",
        f"=== {case.name} ===",
        f"correct answer : {case.correct}",
        f"observed live  : {case.observed or '(not recorded)'}",
        "",
    ]
    for answer, count in result.tally.most_common():
        marks = []
        if answer == case.correct:
            marks.append("correct")
        if answer == case.observed:
            marks.append("what live runs did")
        suffix = f"   <- {', '.join(marks)}" if marks else ""
        lines.append(f"  {count:>2} / {len(result.replays)}  {answer}{suffix}")

    n = len(result.replays)
    answered = [r for r in result.replays if r.decision is not None]
    lines += [
        "",
        f"score: {result.correct_count}/{n} correct  ({len(answered)}/{n} produced a decision)",
        f"cost : ${result.cost_usd:.2f} for {n} replays",
    ]
    # A replay that died or never answered is a lost sample, not evidence about
    # the prompts. Reading it as a wrong answer would score a broken harness as
    # a failing rule, which is the one mistake this tool must not make.
    if not answered:
        lines.append("reads as: nothing measured — every replay failed or went unparsed.")
    elif len(answered) < n:
        lines.append(f"reads as: partial — {n - len(answered)} replay(s) lost; treat with care.")
    elif case.is_control:
        # A control freezes a decision the live run got *right*. Its job is not to
        # be flipped — it is to fail loudly when a rule written to fix some other
        # case takes this one down with it. A suite of nothing but failures a rule
        # is supposed to fix can be passed by a rule that always answers the same
        # way, which is exactly the rule you must not ship.
        if result.correct_count == n:
            lines.append("reads as: control held — a rule under test has not broken this one.")
        else:
            lines.append(
                f"reads as: CONTROL BROKEN — {n - result.correct_count}/{n} stopped reaching the "
                "answer the live run got right."
            )
    elif result.correct_count == 0 and case.observed:
        lines.append("reads as: reproduces the live failure — the rules do not bind here.")
    elif result.correct_count == n:
        lines.append("reads as: flipped every time. Worth a live trial; not a result on its own.")
    else:
        lines.append("reads as: unstable — the decision is a coin-flip, not a rule.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a frozen pipeline decision against the current prompts."
    )
    parser.add_argument("case", help="case directory, or a bare name under cases/")
    parser.add_argument("-n", "--trials", type=int, default=5, help="replays to run (default 5)")
    parser.add_argument("--model", default=None, help="model override, e.g. claude-opus-5")
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--ablate",
        default="none",
        choices=ABLATIONS,
        help="one-variable change to the replay itself, to rule the harness out",
    )
    parser.add_argument(
        "--permission-mode",
        default="acceptEdits",
        help="a replay reads; it writes only to a scratch copy (default acceptEdits)",
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="checkout cache dir")
    parser.add_argument("-o", "--out", type=Path, default=None, help="where to keep replay output")
    parser.add_argument("--dry-run", action="store_true", help="print the assembled prompt and stop")
    args = parser.parse_args(argv)

    directory = Path(args.case)
    if not directory.is_dir():
        directory = CASES_DIR / args.case
    case = load_case(directory)

    if args.dry_run:
        print(build_prompt(case, Path("<artifacts>"), args.ablate))
        return 0

    scratch = args.out or (Path(tempfile.mkdtemp(prefix=f"decisions-{case.name}-")))
    scratch.mkdir(parents=True, exist_ok=True)
    print(f"[{case.name}] {args.trials} replays, ablation={args.ablate}, output in {scratch}")

    result = asyncio.run(
        run_case(
            case,
            trials=args.trials,
            scratch=scratch,
            cache_root=args.cache,
            model=args.model,
            max_turns=args.max_turns,
            permission_mode=args.permission_mode,
            ablation=args.ablate,
            concurrency=args.concurrency,
            log=make_log(scratch / "run.log"),
        )
    )

    text = report(result)
    print(text)
    (scratch / "result.json").write_text(
        json.dumps(
            {
                "case": case.name,
                "ablation": args.ablate,
                "correct": case.correct,
                "observed": case.observed,
                "tally": dict(result.tally),
                "correct_count": result.correct_count,
                "cost_usd": result.cost_usd,
                "replays": [
                    {
                        "index": r.index,
                        "decision": r.decision,
                        "cost_usd": r.cost_usd,
                        "num_turns": r.num_turns,
                        "is_error": r.is_error,
                        "summary": r.summary,
                    }
                    for r in result.replays
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (scratch / "report.txt").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
