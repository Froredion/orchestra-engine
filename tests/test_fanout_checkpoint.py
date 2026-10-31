"""Fan-out checkpoints: an interrupted fan-out stage resumes where it stopped.

A usage limit or a dropped connection lands partway through a fan-out stage far
more often than at a stage boundary, because fan-out stages are where most of
the sessions are. Before this, one item's session raising discarded every other
item's work and nothing recorded which items had finished, so resuming re-ran
the whole stage. These tests pin what the engine now promises instead.
"""

import json

import pytest

from orchestra_engine.engine import run_loop
from orchestra_engine.engine import shapes
from orchestra_engine.engine.shapes import FanOutInterrupted, fan_out_checkpoint_path
from orchestra_engine.model import Pipeline, Stage, StageResult

QUIET = {"log": lambda *a, **k: None}


def _pipeline() -> Pipeline:
    return Pipeline(
        name="t",
        stages=(
            Stage(name="seed", prompt_template="seed {task}\n{handoff}"),
            Stage(name="fan", prompt_template="do {item}\n{handoff}", fan_out="items.txt"),
            Stage(name="report", prompt_template="report {task}\n{handoff}"),
        ),
        handoff_template="{summary}",
    )


def _fake(calls, items, fail_on=()):
    """A run_stage double: seed writes the item list, fan raises on fail_on."""

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, **kwargs):
        calls.append((stage_name, item))
        if stage_name == "seed":
            (artifacts / "items.txt").write_text("\n".join(items) + "\n")
        if stage_name == "fan" and item in fail_on:
            raise Exception("You've hit your session limit")
        return StageResult(stage=stage_name, session_id=f"s-{stage_name}-{item}",
                           summary=f"did {item}", is_error=False, item=item)

    return fake


def _fan_items(calls):
    return [item for stage, item in calls if stage == "fan"]


def test_one_failure_keeps_the_finished_items_and_stops_the_run(run, tmp_path, monkeypatch):
    monkeypatch.setattr(shapes, "FAN_OUT_CONCURRENCY", 1)
    calls = []
    items = ["a", "b", "c", "d"]
    monkeypatch.setattr(shapes, "run_stage", _fake(calls, items, fail_on={"b"}))
    runs_dir = tmp_path / "runs"

    with pytest.raises(FanOutInterrupted) as info:
        run(run_loop("T", tmp_path, _pipeline(), runs_dir, **QUIET))

    (run_dir,) = runs_dir.iterdir()
    # "a" finished before the failure; "c" and "d" were queued and never started,
    # because whatever failed "b" would fail them too.
    assert _fan_items(calls) == ["a", "b"]
    done = json.loads(fan_out_checkpoint_path(run_dir, "fan").read_text())["done"]
    assert list(done) == ["a"]
    assert set(info.value.failed) == {"b", "c", "d"}
    # The stage after it never ran on a half-finished fan-out.
    assert ("report", None) not in calls
    assert info.value.resume_hint == f"--run-dir {run_dir} --stages fan,report"


def test_resume_runs_only_the_items_that_did_not_finish(run, tmp_path, monkeypatch):
    monkeypatch.setattr(shapes, "FAN_OUT_CONCURRENCY", 1)
    items = ["a", "b", "c", "d"]
    runs_dir = tmp_path / "runs"
    first = []
    monkeypatch.setattr(shapes, "run_stage", _fake(first, items, fail_on={"c"}))
    with pytest.raises(FanOutInterrupted):
        run(run_loop("T", tmp_path, _pipeline(), runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()

    second = []
    monkeypatch.setattr(shapes, "run_stage", _fake(second, items))
    resumed = Pipeline(name="t", stages=_pipeline().stages[1:], handoff_template="{summary}")
    results = run(run_loop("T", tmp_path, resumed, runs_dir, run_dir=run_dir, **QUIET))

    assert _fan_items(second) == ["c", "d"]
    # The skipped items still reach the next stage's handoff, from the record.
    fan_items = {r.item for r in results if r.stage == "fan"}
    assert fan_items == {"a", "b", "c", "d"}
    # A completed stage leaves no record, so the next visit starts fresh.
    assert not fan_out_checkpoint_path(run_dir, "fan").exists()


def test_a_completed_stage_reruns_every_item_on_its_next_visit(run, tmp_path, monkeypatch):
    """A route-back sends a stage back to redo work; a stale record must not skip it."""
    items = ["a", "b"]
    calls = []
    monkeypatch.setattr(shapes, "run_stage", _fake(calls, items))
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, _pipeline(), runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()

    calls.clear()
    resumed = Pipeline(name="t", stages=_pipeline().stages[1:], handoff_template="{summary}")
    run(run_loop("T", tmp_path, resumed, runs_dir, run_dir=run_dir, **QUIET))
    assert sorted(_fan_items(calls)) == ["a", "b"]


def test_an_unreadable_checkpoint_runs_every_item(run, tmp_path, monkeypatch):
    items = ["a", "b"]
    calls = []
    monkeypatch.setattr(shapes, "run_stage", _fake(calls, items))
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, _pipeline(), runs_dir, **QUIET))
    (run_dir,) = runs_dir.iterdir()
    path = fan_out_checkpoint_path(run_dir, "fan")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")

    calls.clear()
    resumed = Pipeline(name="t", stages=_pipeline().stages[1:], handoff_template="{summary}")
    run(run_loop("T", tmp_path, resumed, runs_dir, run_dir=run_dir, **QUIET))
    assert sorted(_fan_items(calls)) == ["a", "b"]


def test_checkpoint_name_cannot_escape_the_run_dir(tmp_path):
    path = fan_out_checkpoint_path(tmp_path, "../../etc/passwd")
    assert path.parent == tmp_path / "checkpoints"
    assert "/" not in path.name
