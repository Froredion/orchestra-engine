"""The run directory: one folder per run, holding the stage artifacts the
agents write and the JSON log the engine writes at the end.

    orchestra-docs/runs/20260716-054829/
    ├── run.json          <- rewritten each stage: summaries + accounting
    ├── run-2.json        <- only if --run-dir resumed this run: the next pass
    ├── artifacts/        <- written throughout: REPRO.md, verdict.txt, ...
    │   └── prior/        <- only if a stage ran twice: its earlier documents
    ├── superseded/       <- only if a `handoff: none` stage ran twice
    └── withheld/         <- transient: holds the other stages' documents while
                             a `handoff: none` stage runs, then empties back

The directory is minted before the first session starts, because the agents
write into it while the run is still going. That is the whole point of it:
the target repo receives the deliverable (the fix, the test), and the run
receives the reasoning that produced it.
"""

from __future__ import annotations

import re

import dataclasses
import json
import os
import time
from pathlib import Path

from ..model import StageResult

ARTIFACTS_DIRNAME = "artifacts"

# Where a `handoff: none` stage's earlier documents go when a route-back makes
# it run again. Deliberately a sibling of artifacts/ rather than a subdirectory:
# the stage is granted read access to artifacts/ and nothing else under the run
# directory, so moving a file here puts it out of reach instead of merely
# labelling it stale.
SUPERSEDED_DIRNAME = "superseded"

# Where any stage's earlier documents are kept when a route-back makes it run
# again. Inside artifacts/ rather than beside it, because the stages that need
# to read it are granted that directory and nothing else — the opposite of
# SUPERSEDED_DIRNAME above, which exists to put a file out of reach.
PRIOR_DIRNAME = "prior"

# The one artifact a stage may add to and never rewrite: where a stage records
# that the run now promises less than it did — a behaviour withdrawn, an
# acceptance criterion narrowed, a case no longer asserted.
#
# It is a separate file, and engine-enforced, because a route-back means the
# stage that raised the concern is not the last stage to write. A later pass
# that disagrees can otherwise resolve the concern by deleting the sentence
# recording it, and the run then ends with an artifacts directory that looks
# like nothing was ever given up. Appending a rebuttal is always available;
# removing the original is not.
ESCALATIONS_FILE = "ESCALATIONS.md"

# Marks where the engine put back content a stage removed from ESCALATIONS.md.
RESTORED_MARKER = "<!-- restored by the engine: the text above was removed -->"

# Where a pipeline records the stated criteria it has made executable, one row
# per input form, each with a stable PIN-n id.
#
# Watched by id, not by text. Every stage can measure what a decision costs —
# run a differential and the programs that break are countable. Nothing measures
# a criterion quietly narrowed to a convenient subset of the inputs it named:
# that breaks no test and moves no number. The evidence is one-sided, so the
# side with evidence wins. An id that stops appearing is the missing
# measurement, and the engine says so rather than leaving it to a reader to
# diff two documents nobody kept.
PINS_FILE = "PINS.md"
PIN_ID_RE = re.compile(r"\bPIN-\d+\b")

# The claim line a pin stage writes for each id: `PIN-7: <the outcome asserted>`.
#
# Watching ids alone is not enough, and the gap is not hypothetical. A run that
# decided against a pinned input kept every id, rewrote what two of them
# asserted, and reported 41 declared / 41 present / 41 pass — a green wall over
# a withdrawn requirement. Deleting a pin and inverting one are the same event:
# what the run promised on that input is no longer what it promised.
PIN_CLAIM_RE = re.compile(r"^[ \t]*(PIN-\d+):[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)

# Where every other stage's documents wait while a `handoff: none` stage runs.
# Emptied back into artifacts/ as soon as it finishes: these are withheld from
# one stage, not superseded, and every stage after it is meant to read them.
WITHHELD_DIRNAME = "withheld"

# The engine's log for a run's first pass. A run resumed with --run-dir writes
# run-2.json beside it rather than over it: the earlier pass is what says which
# stages produced the artifacts now on disk, and a resumed run that overwrote it
# would leave a directory full of documents with nothing recording their origin.
RUN_LOG_NAME = "run.json"


def format_duration(seconds: float) -> str:
    """Render an elapsed time as compact h/m/s, e.g. 3725 -> '1h 2m 5s'.

    Seconds are always shown; larger units appear only once they're nonzero, so
    a quick run reads '4s' rather than '0h 0m 4s'. Negative or NaN inputs can't
    arise from a monotonic clock, so this doesn't guard against them.
    """
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or hours:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def new_run_dir(runs_dir: Path) -> Path:
    """Create and return a fresh run directory, with its artifacts/ subdir.

    Named for the start time, which makes runs sort chronologically. Two runs
    launched in the same second would otherwise land on the same name and the
    second would write its artifacts over the first — the exact clobbering
    this layout exists to stop — so a same-second collision takes a suffix
    rather than an existing directory.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = runs_dir / stamp
    suffix = 2
    while path.exists():
        path = runs_dir / f"{stamp}-{suffix}"
        suffix += 1
    (path / ARTIFACTS_DIRNAME).mkdir(parents=True)
    return path


def reuse_run_dir(run_dir: Path, runs_dir: Path) -> Path:
    """Validate an existing run directory the caller wants to continue in.

    The counterpart to new_run_dir: instead of minting a folder, this adopts
    one, so a second invocation can carry on with the artifacts the first left
    — the map, the baseline, the design — rather than paying to derive them
    again. It only ever reads and returns the path; the caller does the rest.

    Three things are checked, and each is a way the flag goes wrong quietly.
    A path that doesn't exist would otherwise be created empty by the first
    stage that writes, so a typo would read as a run that simply found nothing.
    A directory without artifacts/ is not a run directory at all. And the path
    must sit inside runs_dir, because every stage agent is granted read access
    to the artifacts directory: an unchecked --run-dir is an operator handing
    the agents a folder nobody scoped, one path traversal away from anywhere.
    Runs are minted under runs_dir, so a genuine one always passes; a run dir
    kept elsewhere is reachable by pointing --runs-dir at its parent, which
    keeps the widening deliberate and visible in the command line.
    """
    resolved = run_dir.resolve()
    if not resolved.is_dir():
        raise ValueError(f"--run-dir {run_dir} does not exist (or is not a directory)")
    if not (resolved / ARTIFACTS_DIRNAME).is_dir():
        raise ValueError(
            f"--run-dir {run_dir} has no {ARTIFACTS_DIRNAME}/ — it is not a run directory"
        )
    if not resolved.is_relative_to(runs_dir.resolve()):
        raise ValueError(
            f"--run-dir {run_dir} is outside --runs-dir {runs_dir}. Stage agents are "
            f"granted the run's artifacts directory, so it has to be somewhere the "
            f"command names: point --runs-dir at its parent to resume it."
        )
    return resolved


def next_log_name(run_dir: Path) -> str:
    """The log filename this pass should write, leaving earlier passes intact.

    Chosen once, before the first stage, and then written repeatedly by the
    boundary checkpoint — so a pass keeps rewriting its own log and never
    creates a second one mid-run.
    """
    if not (run_dir / RUN_LOG_NAME).exists():
        return RUN_LOG_NAME
    attempt = 2
    while (run_dir / f"run-{attempt}.json").exists():
        attempt += 1
    return f"run-{attempt}.json"


def write_run_log(
    run_dir: Path,
    task: str,
    pipeline_name: str,
    results: list[StageResult],
    elapsed_seconds: float,
    complete: bool = True,
    log_name: str = RUN_LOG_NAME,
) -> Path:
    """Write run.json: the run's summaries plus its accounting.

    elapsed_seconds is the wall-clock time the engine has measured so far. It's
    recorded twice — the precise number for machines to sum across runs, and a
    human 'Xh Ym Zs' string — so a reader gets both without re-deriving one
    from the other.

    Called once per stage boundary, not just at the end, because the handoff a
    stage hands the next one — its summary and its session ID — lives nowhere
    but this file. Left to the end, a run that dies mid-pipeline keeps the
    artifacts its agents wrote and loses every summary, session ID and dollar
    that produced them. complete distinguishes the two kinds of log this
    produces: True once the engine has come out of its loop and written the
    final accounting — including when it stopped early on an operator quit or a
    stage error, both of which are visible in the stages themselves — and False
    on a boundary write, which is the newest state a run gets to keep if the
    process dies before reaching that end.

    The write is atomic — a full temp file swapped into place — because it now
    happens repeatedly and a process killed mid-write would otherwise leave
    truncated JSON, which reads as corrupt rather than as partial.

    log_name is which file in the run directory this pass owns. It defaults to
    run.json and is next_log_name()'s answer for a resumed run, so continuing a
    run adds a log rather than replacing the one that explains what is already
    on disk.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / log_name
    payload = {
        "task": task,
        "pipeline": pipeline_name,
        "complete": complete,
        "duration_seconds": round(elapsed_seconds, 3),
        "duration_hms": format_duration(elapsed_seconds),
        "stages": [dataclasses.asdict(r) for r in results],
        "total_cost_usd": sum(r.cost_usd or 0 for r in results),
    }
    tmp = path.with_name(f"{log_name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
