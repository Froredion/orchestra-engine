"""The documents directory: where a pipeline publishes for a *human*.

Everything in a run directory is one agent talking to the next, under a
timestamped name, and often in a scratch directory that gets deleted. A
pipeline whose deliverable is a document needs somewhere else, so the operator
names one and stages reach it as {docs}. The write grant is the interesting
part: a stage runs with cwd=workspace and add_dirs=[artifacts], so reaching the
operator's folder takes an explicit grant, and only the stages that ask for it
by name get one.
"""

from pathlib import Path

import pytest

from orchestra_engine.engine import run_loop
from orchestra_engine.model import PLACEHOLDERS, Pipeline, Stage, StageResult


def _stage(name, body):
    return Stage(name=name, prompt_template=body)


def _pipeline(*stages):
    return Pipeline("p", stages, "")


@pytest.fixture
def record(monkeypatch):
    """Run a pipeline, capturing what each stage's chain was handed."""
    import orchestra_engine.engine.executor as executor

    def _record(pipeline, workspace, run, docs=None, runs_dir=None):
        calls = []

        async def fake_chain(stage, pl, task, ws, artifacts, prev, **kwargs):
            calls.append({
                "stage": stage.name,
                "docs": kwargs.get("docs"),
                "add_dirs": list(kwargs.get("extra_add_dirs") or []),
                "prompt": pl.build_prompt(
                    stage, task, artifacts=str(artifacts), docs=kwargs.get("docs") or ""
                ),
            })
            return [
                StageResult(
                    stage=stage.name,
                    session_id=f"sess-{len(calls)}",
                    summary="done",
                    is_error=False,
                )
            ]

        monkeypatch.setattr(executor, "run_chain", fake_chain)
        run(
            run_loop(
                task="T",
                workspace=workspace,
                pipeline=pipeline,
                runs_dir=runs_dir or workspace.parent / "runs",
                docs=docs,
                log=lambda _: None,
            )
        )
        return calls

    return _record


# --- the placeholder -----------------------------------------------------


def test_docs_is_a_placeholder():
    """Named in PLACEHOLDERS, so the loader spares it when escaping the braces
    of a file pulled in by `redirect:`."""
    assert "docs" in PLACEHOLDERS


def test_prompt_carries_the_docs_path():
    stage = _stage("spec", "Publish to {docs}/x/SPEC.md")
    prompt = _pipeline(stage).build_prompt(stage, "T", docs="/home/e/plans")
    assert "/home/e/plans/x/SPEC.md" in prompt


def test_docs_renders_empty_when_no_directory_was_named():
    """A pipeline that names {docs} still runs with none configured, the way
    {item} and {handoff} render empty rather than raising."""
    stage = _stage("spec", "Publish to {docs}.")
    assert _pipeline(stage).build_prompt(stage, "T") == "Publish to ."


# --- the write grant -----------------------------------------------------


def test_the_directory_is_created(tmp_path, run, record):
    docs = tmp_path / "plans" / "nested"
    record(_pipeline(_stage("s", "Write {docs}/f.md")), tmp_path / "ws", run, docs=docs)
    assert docs.is_dir()


def test_a_stage_that_names_docs_is_granted_it(tmp_path, run, record):
    docs = tmp_path / "plans"
    calls = record(
        _pipeline(_stage("spec", "Publish to {docs}/x/SPEC.md")),
        tmp_path / "ws",
        run,
        docs=docs,
    )
    assert calls[0]["docs"] == str(docs)
    assert str(docs) in calls[0]["add_dirs"]


def test_a_stage_that_does_not_name_docs_is_not_granted_it(tmp_path, run, record):
    """Least privilege, and the reason the grant is per stage rather than per
    run: the ten stages before the publishing one have no business writing in
    the operator's own folder, and a run of a pipeline that publishes nothing
    should not be able to touch it at all."""
    docs = tmp_path / "plans"
    calls = record(
        _pipeline(
            _stage("ground", "Read the code in {artifacts}."),
            _stage("spec", "Publish to {docs}/x/SPEC.md"),
        ),
        tmp_path / "ws",
        run,
        docs=docs,
    )
    ground, spec = calls[0], calls[1]
    assert ground["docs"] == ""
    assert str(docs) not in ground["add_dirs"]
    assert str(docs) in spec["add_dirs"]


def test_no_docs_directory_grants_nothing(tmp_path, run, record):
    calls = record(
        _pipeline(_stage("spec", "Publish to {docs}/x/SPEC.md")),
        tmp_path / "ws",
        run,
        docs=None,
    )
    assert calls[0]["docs"] == ""
    assert calls[0]["add_dirs"] == []


def test_the_grant_keeps_the_transcript_access_a_stage_already_had(tmp_path, run, record):
    """extra_add_dirs already carries the prior sessions' transcript paths; the
    docs grant is appended to those, never in place of them."""
    docs = tmp_path / "plans"
    calls = record(
        _pipeline(
            _stage("first", "Do the thing."),
            _stage("spec", "Publish to {docs}/x/SPEC.md"),
        ),
        tmp_path / "ws",
        run,
        docs=docs,
    )
    assert calls[1]["add_dirs"][-1] == str(docs)
    assert len(calls[1]["add_dirs"]) >= 1


# --- the flag ------------------------------------------------------------


def test_flag_defaults_to_a_folder_beside_the_runs(monkeypatch):
    from orchestra_engine.main import parse_args

    monkeypatch.setattr("sys.argv", ["loop", "task", "--pipeline", "approach"])
    args = parse_args()
    assert Path(args.docs_dir) == Path("orchestra-docs/plans")
    assert Path(args.runs_dir) == Path("orchestra-docs/runs")


def test_flag_is_overridable(monkeypatch):
    from orchestra_engine.main import parse_args

    monkeypatch.setattr(
        "sys.argv", ["loop", "task", "--pipeline", "approach", "--docs-dir", "/tmp/d"]
    )
    assert Path(parse_args().docs_dir) == Path("/tmp/d")


def test_a_pipeline_that_publishes_nothing_leaves_no_folder(tmp_path, run, record):
    """`feature` and `bugfix` leave their deliverable in the workspace as code.
    A run of one should not mint a folder in the operator's documents directory
    just to leave it empty."""
    docs = tmp_path / "plans"
    record(_pipeline(_stage("build", "Change the code in the workspace.")),
           tmp_path / "ws", run, docs=docs)
    assert not docs.exists()
