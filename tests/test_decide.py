"""decide: an agent-routed stage that can loop the run back to an earlier one,
bounded by a hop budget so it always halts."""

import json

import pytest

from orchestra_engine.model import Pipeline, SkipIf, Stage
from orchestra_engine.pipeline import _parse_stage, load_pipeline, stage_signature


def make_pipeline(*stages):
    return Pipeline(name="t", stages=tuple(stages), handoff_template="{summary}")


def stage(name, **kwargs):
    return Stage(name=name, prompt_template="do {task}\n{handoff}", **kwargs)


def decides(plan, for_stage="decide"):
    """An on_stage hook: each time `for_stage` runs, write the next decision.json
    from `plan` (payloads consumed in order; the last repeats if the stage runs
    more often than planned)."""
    state = {"i": 0}

    def hook(name, artifacts):
        if name == for_stage:
            payload = plan[min(state["i"], len(plan) - 1)]
            (artifacts / "decision.json").write_text(json.dumps(payload))
            state["i"] += 1

    return hook


def writes_decision(stage_name, payload):
    """An on_stage hook: when `stage_name` runs, drop a fixed decision.json."""

    def hook(name, artifacts):
        if name == stage_name:
            (artifacts / "decision.json").write_text(json.dumps(payload))

    return hook


def write_pipeline(tmp_path, files):
    d = tmp_path / "pipe"
    d.mkdir()
    for filename, text in files.items():
        (d / filename).write_text(text)
    return d


# --- parsing / validation ------------------------------------------------


def test_parses_targets(tmp_path):
    s = _parse_stage("decide", "---\ndecide: reproduce, diagnose\n---\nbody", tmp_path)
    assert s.decide_targets == ("reproduce", "diagnose")


def test_rejects_empty_decide(tmp_path):
    with pytest.raises(ValueError, match="decide must list"):
        _parse_stage("decide", "---\ndecide: ,\n---\nbody", tmp_path)


def test_rejects_fan_out_with_decide(tmp_path):
    with pytest.raises(ValueError, match="can't both fan_out and decide"):
        _parse_stage("d", "---\nfan_out: x.txt\ndecide: a\n---\nbody", tmp_path)


def test_rejects_repeat_with_decide(tmp_path):
    with pytest.raises(ValueError, match="can't both repeat and decide"):
        _parse_stage("d", "---\nrepeat: 2\ndecide: a\n---\nbody", tmp_path)


def test_load_rejects_target_that_names_no_stage(tmp_path):
    d = write_pipeline(tmp_path, {"01-a.md": "---\ndecide: nope\n---\nb", "02-b.md": "b"})
    with pytest.raises(ValueError, match="name no stage"):
        load_pipeline(str(d))


def test_load_rejects_decider_as_last_stage(tmp_path):
    d = write_pipeline(tmp_path, {"01-a.md": "b", "02-b.md": "---\ndecide: a\n---\nb"})
    with pytest.raises(ValueError, match="cannot be a decider"):
        load_pipeline(str(d))


def test_signature_shows_targets():
    s = stage("decide", decide_targets=("reproduce", "diagnose"))
    assert "may loop back to reproduce, diagnose" in stage_signature(s)


# --- the built-in bugfix pipeline ----------------------------------------


def test_bugfix_pipeline_is_self_routing():
    p = load_pipeline("bugfix")
    assert [s.name for s in p.stages] == [
        "reproduce", "localize", "diagnose", "fix", "verify", "prevent",
    ]
    routes = {s.name: s.decide_targets for s in p.stages}
    assert routes["localize"] == ("reproduce",)
    assert routes["fix"] == ("diagnose",)
    assert routes["verify"] == ("reproduce", "localize", "diagnose", "fix")
    # reproduce advances/agains without declaring named targets
    assert routes["reproduce"] is None
    # the fixed repeat counts are gone — every stage is single-pass now
    assert all(s.repeat == 1 for s in p.stages)
    # terminal stage never routes
    assert p.stages[-1].name == "prevent" and not p.stages[-1].decide_targets


def test_investigation_pipeline_is_self_routing():
    p = load_pipeline("investigation")
    assert [s.name for s in p.stages] == [
        "frame", "recon", "hypotheses", "investigate", "assess", "answer",
    ]
    routes = {s.name: s.decide_targets for s in p.stages}
    # frame advances/agains without declaring named targets
    assert routes["frame"] is None
    # hypotheses can widen the map again; assess turns the whole inquiry loop
    assert routes["hypotheses"] == ("frame",)
    assert routes["assess"] == ("frame", "hypotheses")
    # the parallel investigate stage fans out and must NOT route (agents would
    # race to write the one decision.json — the loader forbids fan_out + decide)
    investigate = next(s for s in p.stages if s.name == "investigate")
    assert investigate.fan_out == "hypotheses.txt" and investigate.max_items == 3
    assert investigate.decide_targets is None
    # terminal stage never routes
    assert p.stages[-1].name == "answer" and not p.stages[-1].decide_targets
    # every stage is single-pass; the loop comes from decide, not fixed repeats
    assert all(s.repeat == 1 for s in p.stages)
    # the multi-target decider's JSON literals survive str.format (braces doubled)
    out = p.build_prompt(
        next(s for s in p.stages if s.name == "assess"),
        task="T", artifacts="/run/artifacts",
    )
    assert '{"next": "done"}' in out and '{"next": "hypotheses"}' in out


def test_code_review_pipeline_is_self_routing():
    p = load_pipeline("code-review")
    assert [s.name for s in p.stages] == [
        "scope", "review", "consolidate", "verify", "report",
    ]
    routes = {s.name: s.decide_targets for s in p.stages}
    # scope advances/agains without declaring named targets
    assert routes["scope"] is None
    # consolidate is the one decider: a misfired lens re-runs review, a misread
    # change re-runs scope
    assert routes["consolidate"] == ("scope", "review")
    # both parallel stages fan out and must NOT route (agents would race to write
    # the one decision.json — the loader forbids fan_out + decide)
    review = next(s for s in p.stages if s.name == "review")
    verify = next(s for s in p.stages if s.name == "verify")
    assert review.fan_out == "lenses.txt" and review.max_items == 6
    assert verify.fan_out == "findings.txt" and verify.max_items == 10
    assert review.decide_targets is None and verify.decide_targets is None
    # the clean/nothing-to-review gate skips every stage between scope and report
    # but never the report — the run must always end with a review document
    gated = {s.name for s in p.stages if s.skip_if == SkipIf("verdict.txt", "STOP")}
    assert gated == {"review", "consolidate", "verify"}
    assert p.stages[-1].name == "report" and p.stages[-1].skip_if is None
    # terminal stage never routes; every stage is single-pass (loop via decide)
    assert not p.stages[-1].decide_targets
    assert all(s.repeat == 1 for s in p.stages)
    # the multi-target decider's JSON literals survive str.format (braces doubled)
    out = p.build_prompt(
        next(s for s in p.stages if s.name == "consolidate"),
        task="T", artifacts="/run/artifacts",
    )
    assert '{"next": "review"}' in out and '{"next": "scope"}' in out


def test_router_prompts_render_their_json_examples():
    """The prompts embed JSON literals, which only survive str.format if their
    braces are doubled. Render them and prove the examples came out intact."""
    p = load_pipeline("bugfix")
    verify = next(s for s in p.stages if s.name == "verify")
    out = p.build_prompt(verify, task="T", artifacts="/run/artifacts")
    assert '{"next": "done"}' in out
    assert '{"next": "diagnose"}' in out
    reproduce = next(s for s in p.stages if s.name == "reproduce")
    assert '{"next": "again"}' in p.build_prompt(reproduce, task="T", artifacts="/x")


# --- routing behavior ----------------------------------------------------


def test_every_plain_stage_advances_by_default(run, tmp_path, stub):
    """A plain stage that writes no decision just advances — so pipelines that
    never heard of routing (research) behave exactly as before."""
    calls, _ = stub(
        make_pipeline(stage("a"), stage("b"), stage("prevent")),
        tmp_path / "ws",
        run,
        on_stage=None,
    )
    assert [c["stage"] for c in calls] == ["a", "b", "prevent"]


def test_advance_is_explicit(run, tmp_path, stub):
    calls, _ = stub(
        make_pipeline(stage("a"), stage("b"), stage("prevent")),
        tmp_path / "ws",
        run,
        on_stage=writes_decision("a", {"next": "advance"}),
    )
    assert [c["stage"] for c in calls] == ["a", "b", "prevent"]


def test_again_reruns_the_same_stage(run, tmp_path, stub):
    """'a' asks to run again once, then advances — this is what replaces a fixed
    repeat: 2, except the stage itself decides it needs the second pass."""
    calls, _ = stub(
        make_pipeline(stage("a"), stage("b"), stage("prevent")),
        tmp_path / "ws",
        run,
        on_stage=decides([{"next": "again"}, {"next": "advance"}], for_stage="a"),
    )
    assert [c["stage"] for c in calls] == ["a", "a", "b", "prevent"]


def test_done_from_the_middle_jumps_to_terminal(run, tmp_path, stub):
    calls, _ = stub(
        make_pipeline(stage("a"), stage("b"), stage("c"), stage("prevent")),
        tmp_path / "ws",
        run,
        on_stage=writes_decision("a", {"done": True}),
    )
    assert [c["stage"] for c in calls] == ["a", "prevent"], "done skips b and c"


def test_loops_back_then_finishes(run, tmp_path, stub):
    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("verify"),
            stage("decide", decide_targets=("reproduce",)),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        on_stage=decides([{"next": "reproduce"}, {"done": True}]),
    )
    assert [c["stage"] for c in calls] == [
        "reproduce", "verify", "decide",   # first pass: decide routes back
        "reproduce", "verify", "decide",   # second pass: decide is done
        "prevent",                          # terminal always runs last
    ]


def test_done_advances_straight_to_terminal(run, tmp_path, stub):
    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("verify"),
            stage("decide", decide_targets=("reproduce",)),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        on_stage=decides([{"done": True}]),
    )
    assert [c["stage"] for c in calls] == ["reproduce", "verify", "decide", "prevent"]


def test_hop_budget_stops_an_endless_loop_at_the_terminal(run, tmp_path, stub, logs):
    """A decider that always routes back must still halt — and land on prevent."""
    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("verify"),
            stage("decide", decide_targets=("reproduce",)),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        log=logs.append,
        on_stage=decides([{"next": "reproduce"}]),  # never done
        max_hops=3,
    )
    stages_run = [c["stage"] for c in calls]
    assert stages_run[-1] == "prevent", "the run must end on the terminal stage"
    assert len(stages_run) <= 4, "the budget must cap the loop, not merely slow it"
    assert any("hop budget" in line for line in logs)


def test_missing_decision_reads_as_done(run, tmp_path, stub, logs):
    calls, _ = stub(
        make_pipeline(
            stage("verify"),
            stage("decide", decide_targets=("verify",)),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        log=logs.append,
        on_stage=None,  # decide writes nothing
    )
    assert [c["stage"] for c in calls] == ["verify", "decide", "prevent"]
    assert any("no decision.json written" in line for line in logs)


def test_malformed_decision_fails_safe(run, tmp_path, stub, logs):
    def garbage(name, artifacts):
        if name == "decide":
            (artifacts / "decision.json").write_text("not json{")

    calls, _ = stub(
        make_pipeline(
            stage("verify"),
            stage("decide", decide_targets=("verify",)),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        log=logs.append,
        on_stage=garbage,
    )
    assert [c["stage"] for c in calls] == ["verify", "decide", "prevent"], "no loop on garbage"
    assert any("DECISION IGNORED" in line and "unreadable" in line for line in logs)


def test_undeclared_target_is_refused(run, tmp_path, stub, logs):
    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("verify"),
            stage("decide", decide_targets=("reproduce",)),  # may NOT jump to verify
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        log=logs.append,
        on_stage=decides([{"next": "verify"}]),
    )
    assert [c["stage"] for c in calls] == ["reproduce", "verify", "decide", "prevent"]
    assert any("DECISION IGNORED" in line and "declared targets" in line for line in logs)


def test_skip_if_on_a_decider_skips_it_without_routing(run, tmp_path, stub):
    """bugfix's decider carries skip_if: verdict.txt contains STOP — when the bug
    never reproduced, the gate wins and the run advances to the terminal handback,
    the decider never running (and so never routing)."""
    plant_stop_then_route = decides([{"next": "reproduce"}])

    def hooks(name, artifacts):
        if name == "reproduce":
            (artifacts / "verdict.txt").write_text("STOP: never reproduced\n")
        plant_stop_then_route(name, artifacts)

    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage(
                "decide",
                decide_targets=("reproduce",),
                skip_if=SkipIf("verdict.txt", "STOP"),
            ),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        on_stage=hooks,
    )
    assert [c["stage"] for c in calls] == ["reproduce", "prevent"], "decide gated out"


def test_symlinked_decision_outside_artifacts_is_refused(run, tmp_path, stub, logs):
    """resolve() follows the link, so a decision.json an agent points outside the
    artifacts dir can't smuggle in a jump target from elsewhere."""
    outside = tmp_path / "planted.json"
    outside.write_text('{"next": "reproduce"}')

    def plant_symlink(name, artifacts):
        if name == "decide":
            (artifacts / "decision.json").symlink_to(outside)

    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("verify"),
            stage("decide", decide_targets=("reproduce",)),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        log=logs.append,
        on_stage=plant_symlink,
    )
    assert [c["stage"] for c in calls] == ["reproduce", "verify", "decide", "prevent"]
    assert any("DECISION IGNORED" in line and "outside" in line for line in logs)
