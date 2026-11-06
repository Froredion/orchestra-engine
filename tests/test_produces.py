"""produces: a document a stage must leave, or the run stops there."""

import os
from pathlib import Path

import pytest

from orchestra_engine import channel
from orchestra_engine.engine import executor, run_loop
from orchestra_engine.model import Pipeline, Stage, StageResult
from orchestra_engine.pipeline import _parse_stage, stage_signature

SOURCE = Path("01-place.md")


def make_pipeline(*stages):
    return Pipeline(name="t", stages=tuple(stages), handoff_template="{summary}")


def stage(name, **kwargs):
    return Stage(name=name, prompt_template="do {task}\n{handoff}", **kwargs)


def writes(name, document, text="contents"):
    def hook(stage_name, artifacts):
        if stage_name == name:
            (artifacts / document).write_text(text, encoding="utf-8")

    return hook


def test_parses_one_or_several_documents():
    parsed = _parse_stage("place", "---\nproduces: PLACE.md, notes/plan.txt\n---\nbody", SOURCE)
    assert parsed.produces == ("PLACE.md", "notes/plan.txt")
    assert _parse_stage("place", "body", SOURCE).produces == ()


@pytest.mark.parametrize(
    "value",
    [
        "../PLACE.md",
        "/etc/passwd",
        "ok.md, ../bad.md",
        pytest.param(
            "C:x",
            marks=pytest.mark.skipif(os.name != "nt", reason="only a rooted path on Windows"),
        ),
    ],
)
def test_a_path_out_of_the_artifacts_dir_is_refused_at_load(value):
    with pytest.raises(ValueError, match="produces must be a relative path"):
        _parse_stage("place", f"---\nproduces: {value}\n---\nbody", SOURCE)


def test_an_empty_list_is_refused_at_load():
    with pytest.raises(ValueError, match="at least one document"):
        _parse_stage("place", "---\nproduces: ,\n---\nbody", SOURCE)


def test_the_listing_says_what_a_stage_must_leave():
    assert "[must leave PLACE.md, x.txt]" in stage_signature(stage("place", produces=("PLACE.md", "x.txt")))


def test_a_missing_document_stops_the_run(run, tmp_path, stub, logs):
    """The trial's stage: its session reported success with nothing written, and the
    next stage started without its input."""
    calls, results = stub(
        make_pipeline(stage("place", produces=("PLACE.md",)), stage("system")),
        tmp_path / "ws",
        run,
        log=logs.append,
    )
    assert [c["stage"] for c in calls] == ["place"], "the next stage must not start"
    assert results[-1].is_error
    assert "PLACE.md" in results[-1].summary
    assert any("STAGE FAILED" in line and "PLACE.md" in line for line in logs)


def test_leaving_the_document_advances(run, tmp_path, stub):
    calls, results = stub(
        make_pipeline(stage("place", produces=("PLACE.md",)), stage("system")),
        tmp_path / "ws",
        run,
        on_stage=writes("place", "PLACE.md"),
    )
    assert [c["stage"] for c in calls] == ["place", "system"]
    assert not any(r.is_error for r in results)


def test_an_empty_document_counts_as_missing(run, tmp_path, stub):
    calls, results = stub(
        make_pipeline(stage("place", produces=("PLACE.md",)), stage("system")),
        tmp_path / "ws",
        run,
        on_stage=writes("place", "PLACE.md", text=""),
    )
    assert [c["stage"] for c in calls] == ["place"]
    assert results[-1].is_error


def test_every_named_document_is_required(run, tmp_path, stub):
    calls, results = stub(
        make_pipeline(stage("place", produces=("PLACE.md", "plan.txt")), stage("system")),
        tmp_path / "ws",
        run,
        on_stage=writes("place", "PLACE.md"),
    )
    assert [c["stage"] for c in calls] == ["place"]
    assert "plan.txt" in results[-1].summary and "PLACE.md" not in results[-1].summary.split("in ")[0]


def test_a_symlink_out_of_the_artifacts_dir_is_not_leaving_one(run, tmp_path, stub, symlink):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("contents")

    def plant(name, artifacts):
        if name == "place":
            symlink(artifacts / "PLACE.md", outside)

    calls, results = stub(
        make_pipeline(stage("place", produces=("PLACE.md",)), stage("system")),
        tmp_path / "ws",
        run,
        on_stage=plant,
    )
    assert [c["stage"] for c in calls] == ["place"]
    assert results[-1].is_error


def test_a_stage_that_already_failed_is_not_also_blamed_for_the_document(run, monkeypatch, tmp_path, logs):
    async def fake_chain(the_stage, *args, **kwargs):
        return [StageResult(stage=the_stage.name, session_id="s", summary="it broke", is_error=True)]

    monkeypatch.setattr(executor, "run_chain", fake_chain)
    results = run(
        run_loop(
            task="T", workspace=tmp_path / "ws",
            pipeline=make_pipeline(stage("place", produces=("PLACE.md",)), stage("system")),
            runs_dir=tmp_path / "runs", log=logs.append,
        )
    )
    assert [r.summary for r in results] == ["it broke"]
    assert any("stage errored" in line for line in logs)


def test_a_fan_out_stage_is_checked_once_all_its_items_are_done(run, monkeypatch, tmp_path, logs):
    async def fake_fan_out(the_stage, *args, **kwargs):
        item = StageResult(stage=the_stage.name, session_id="s", summary="item", is_error=False, item="a")
        return [item], StageResult(stage=the_stage.name, session_id="(multiple)", summary="merged", is_error=False)

    monkeypatch.setattr(executor, "run_fan_out", fake_fan_out)
    results = run(
        run_loop(
            task="T", workspace=tmp_path / "ws",
            pipeline=make_pipeline(stage("dive", fan_out="leads.txt", produces=("REPORT.md",)), stage("system")),
            runs_dir=tmp_path / "runs", log=logs.append,
        )
    )
    assert [r.stage for r in results] == ["dive", "dive"]
    assert results[-1].is_error and "REPORT.md" in results[-1].summary
    assert not any("every item failed" in line for line in logs), "that would be untrue"


def test_the_failure_reaches_the_channel(run, tmp_path, stub):
    chan = channel.open_channel(tmp_path / "chan", forbidden={})
    try:
        stub(make_pipeline(stage("place", produces=("PLACE.md",)), stage("system")), tmp_path / "ws", run)
        events = channel.read_events(chan.dir)
    finally:
        channel.close_channel()
    finished = next(e for e in events if e["event"] == "stage_finished")
    assert finished["is_error"] is True and "PLACE.md" in finished["summary"]
    assert next(e for e in events if e["event"] == "run_complete")["failed_stage"] == "place"
