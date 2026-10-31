"""--run-dir: continuing a run in the directory an earlier pass left behind.

A run that ends on a question — a STOP verdict, a conflict only a human can
settle — has already paid for its map, its baseline and its design. Resuming
means the answer costs the stages it changes and nothing else. These tests pin
what the second pass must not destroy on its way in.
"""

import pytest

from orchestra_engine.engine import run_loop
from orchestra_engine.engine.runlog import reuse_run_dir
from orchestra_engine.model import Pipeline, SkipIf, Stage, StageResult

from orchestra_engine.engine import shapes


def _pipeline(*stages: Stage) -> Pipeline:
    return Pipeline(name="t", stages=stages, handoff_template="{summary}")


def _writer(writes: dict[str, tuple[str, str]]):
    """A stage double that plants {stage: (filename, contents)} as it runs."""

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, **kwargs):
        if stage_name in writes:
            name, body = writes[stage_name]
            (artifacts / name).write_text(body)
        return StageResult(stage=stage_name, session_id=None,
                           summary=f"summary of {stage_name}", is_error=False)

    return fake


QUIET = {"log": lambda *a, **k: None}


def test_run_dir_continues_the_same_directory(run, tmp_path, monkeypatch):
    """The second pass adopts the first pass's directory instead of minting one."""
    monkeypatch.setattr(shapes, "run_stage", _writer({"design": ("DESIGN.md", "v1")}))
    pipeline = _pipeline(
        Stage(name="design", prompt_template="do {task}\n{handoff}"),
        Stage(name="report", prompt_template="do {task}\n{handoff}"),
    )
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, pipeline, runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()

    run(run_loop("T", tmp_path, pipeline, runs_dir, run_dir=run_dir, **QUIET))

    # One directory, not two: the resumed pass wrote into the run it was given.
    assert [p.name for p in runs_dir.iterdir()] == [run_dir.name]


def test_resuming_keeps_the_earlier_pass_log(run, tmp_path, monkeypatch):
    """run.json explains the artifacts already on disk, so a resume writes beside it.

    Overwriting it would leave a directory full of documents with nothing
    recording which stages produced them, what they handed on, or what they
    cost — the exact loss the per-stage checkpoint exists to prevent.
    """
    monkeypatch.setattr(shapes, "run_stage", _writer({}))
    pipeline = _pipeline(Stage(name="only", prompt_template="do {task}\n{handoff}"))
    runs_dir = tmp_path / "runs"
    run(run_loop("first task", tmp_path, pipeline, runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()
    first = (run_dir / "run.json").read_text()

    run(run_loop("second task", tmp_path, pipeline, runs_dir, run_dir=run_dir, **QUIET))

    assert (run_dir / "run.json").read_text() == first, "the first pass's log was rewritten"
    assert "second task" in (run_dir / "run-2.json").read_text()


def test_resume_refuses_while_the_gate_still_says_stop(run, tmp_path, monkeypatch):
    """A stale STOP verdict would skip every gated stage and do nothing, quietly.

    The run would still reach its terminal and write a second handback saying
    what the first one said, which reads as an answer that didn't take rather
    than an answer that was never read.
    """
    monkeypatch.setattr(shapes, "run_stage", _writer({"scope": ("verdict.txt", "STOP")}))
    gated = Stage(
        name="build",
        prompt_template="do {task}\n{handoff}",
        skip_if=SkipIf(file="verdict.txt", needle="STOP"),
    )
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path,
                 _pipeline(Stage(name="scope", prompt_template="do {task}\n{handoff}"), gated),
                 runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()

    with pytest.raises(ValueError) as exc:
        run(run_loop("T", tmp_path, _pipeline(gated), runs_dir, run_dir=run_dir, **QUIET))

    # Names the file that decides it, so the operator knows what to resolve.
    assert "verdict.txt" in str(exc.value)
    assert "STOP" in str(exc.value)


def test_resume_snapshots_what_the_previous_pass_left(run, tmp_path, monkeypatch):
    """A stage re-entered across passes must not silently replace its own document.

    Within a run the engine snapshots it from in-memory attribution, which the
    process ending destroys. Without this the comparison that shows whether the
    second version delivered *less* than the first is simply gone — and that
    comparison is what a route-back is checked against.
    """
    monkeypatch.setattr(shapes, "run_stage", _writer({"design": ("DESIGN.md", "v1")}))
    pipeline = _pipeline(Stage(name="design", prompt_template="do {task}\n{handoff}"))
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, pipeline, runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()

    monkeypatch.setattr(shapes, "run_stage", _writer({"design": ("DESIGN.md", "v2")}))
    run(run_loop("T", tmp_path, pipeline, runs_dir, run_dir=run_dir, **QUIET))

    artifacts = run_dir / "artifacts"
    assert (artifacts / "DESIGN.md").read_text() == "v2"
    assert (artifacts / "prior" / "resumed-1" / "DESIGN.md").read_text() == "v1"


def test_escalations_survive_a_resume(run, tmp_path, monkeypatch):
    """The append-only record of what the run gave up carries into the next pass.

    It is engine-enforced within a run precisely so a later pass cannot resolve
    a concern by deleting the sentence recording it. A resume that started with
    a clean conscience would hand that erasure back for free.
    """
    monkeypatch.setattr(
        shapes, "run_stage", _writer({"design": ("ESCALATIONS.md", "AC-3 narrowed\n")})
    )
    pipeline = _pipeline(Stage(name="design", prompt_template="do {task}\n{handoff}"))
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, pipeline, runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()

    monkeypatch.setattr(shapes, "run_stage", _writer({}))
    run(run_loop("T", tmp_path, pipeline, runs_dir, run_dir=run_dir, **QUIET))

    assert "AC-3 narrowed" in (run_dir / "artifacts" / "ESCALATIONS.md").read_text()


@pytest.mark.parametrize(
    "make, expected",
    [
        (lambda base: base / "nope", "does not exist"),
        (lambda base: base, "not a run directory"),
    ],
)
def test_run_dir_must_name_a_run_directory(tmp_path, make, expected):
    """A typo would otherwise read as a run that found nothing to build on."""
    runs_dir = tmp_path / "runs"
    (runs_dir / "20260814-000000").mkdir(parents=True)
    with pytest.raises(ValueError, match=expected):
        reuse_run_dir(make(runs_dir / "20260814-000000"), runs_dir)


def test_run_dir_outside_runs_dir_is_refused(tmp_path):
    """Stage agents are granted the artifacts directory, so it has to be scoped.

    An unchecked --run-dir hands them a folder nobody named on the command line.
    """
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "artifacts").mkdir(parents=True)
    with pytest.raises(ValueError, match="outside --runs-dir"):
        reuse_run_dir(elsewhere, runs_dir)
