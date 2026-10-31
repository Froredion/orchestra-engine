"""There is no default pipeline. A run either names one or is asked which one
it meant — it never inherits a workflow nobody chose."""

import argparse

import pytest

from orchestra_engine.main import resolve_pipeline


def args(**kwargs):
    base = {"pipeline": None, "repeat_map": {}, "max_items_map": {}, "stages": None}
    return argparse.Namespace(**{**base, **kwargs})


def test_wizard_run_with_no_flag_defers_to_the_wizard():
    assert resolve_pipeline(args(), wizard_mode=True) is None


def test_named_pipeline_loads():
    pipeline = resolve_pipeline(args(pipeline="bugfix"), wizard_mode=False)
    assert [s.name for s in pipeline.stages][0] == "reproduce"


def test_scripted_run_with_no_flag_exits_rather_than_guessing():
    """The failure mode this guards: a scripted run silently walking a whole
    pipeline the caller never asked for. It stops, and says what it can run."""
    with pytest.raises(SystemExit) as exc:
        resolve_pipeline(args(), wizard_mode=False)
    assert "--pipeline is required" in str(exc.value)
    assert "bugfix" in str(exc.value)  # names the real choices


def test_unknown_pipeline_exits_with_the_loader_message():
    """`sdlc` is the removed built-in — a name a stale script or doc may still
    pass. It must fail as a name that isn't there, not resolve to anything."""
    with pytest.raises(SystemExit) as exc:
        resolve_pipeline(args(pipeline="sdlc"), wizard_mode=False)
    assert "not found" in str(exc.value)


def test_flag_overrides_apply_to_the_named_pipeline():
    pipeline = resolve_pipeline(
        args(pipeline="bugfix", stages="reproduce,localize"), wizard_mode=False
    )
    assert [s.name for s in pipeline.stages] == ["reproduce", "localize"]
