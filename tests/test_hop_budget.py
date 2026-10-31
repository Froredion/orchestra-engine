"""The hop budget: what a run does once it runs out of room to route.

Exhausting the budget used to jump straight to the terminal stage, which skipped
every checking stage on the way. That is the worst possible skip: the last
revision a run makes is the one most likely to have quietly reduced scope,
because it was made to satisfy a route-back.
"""

from orchestra_engine.model import Pipeline, Stage

def _routing_pipeline(*stages):


    return Pipeline(name="p", stages=tuple(stages), handoff_template="{summary}")


def test_spent_budget_still_walks_the_remaining_stages(stub, run, tmp_path):
    """The last revision a run makes must still meet the stages that check it.

    Jumping straight to the terminal on exhaustion skipped exactly those, and the
    revision it skipped them for is the one most likely to have reduced scope —
    it was made to satisfy a route-back.
    """


    pipeline = _routing_pipeline(
        Stage(name="plan", prompt_template="p"),
        Stage(name="build", prompt_template="b", decide_targets=("plan",)),
        Stage(name="check", prompt_template="c"),
        Stage(name="handback", prompt_template="h"),
    )

    def route_build_back_to_plan(stage_name, artifacts):
        if stage_name == "build":
            (artifacts / "decision.json").write_text(
                '{"next": "plan"}', encoding="utf-8"
            )

    calls, _ = stub(pipeline, tmp_path / "ws", run, on_stage=route_build_back_to_plan, max_hops=4)
    ran = [c["stage"] for c in calls]

    assert ran[-1] == "handback", "a run must always end with the terminal stage"
    assert "check" in ran, "the checking stage must not be skipped on exhaustion"
    assert ran.index("check") < ran.index("handback")
    assert ran.count("build") == 2, "the budget still stops the loop"


def test_the_override_is_recorded_and_the_stage_is_never_told(stub, run, tmp_path):
    """A stage that knew its remaining budget could plan around it — hold a
    finding back, or file one because it is the last chance. What it decides
    should turn on the work, so the accounting stays on the engine's side."""
    pipeline = _routing_pipeline(
        Stage(name="build", prompt_template="b", decide_targets=("build",)),
        Stage(name="check", prompt_template="c"),
        Stage(name="handback", prompt_template="h"),
    )

    def route_build_to_itself(stage_name, artifacts):
        if stage_name == "build":
            (artifacts / "decision.json").write_text('{"next": "again"}', encoding="utf-8")

    lines: list[str] = []
    calls, _ = stub(
        pipeline,
        tmp_path / "ws",
        run,
        on_stage=route_build_to_itself,
        max_hops=2,
        log=lines.append,
    )

    # The override is on the record, naming what was asked for.
    assert any("DECISION OVERRIDDEN" in ln and "hop budget" in ln for ln in lines)
    # And nothing about the budget reached any stage.
    for call in calls:
        blob = call["prompt"] + call["extra_context"] + call["handoff"]
        assert "hop budget" not in blob.lower()
        assert "routing is closed" not in blob.lower()


def test_routing_stays_open_while_the_budget_lasts(stub, run, tmp_path):


    pipeline = _routing_pipeline(
        Stage(name="build", prompt_template="b"),
        Stage(name="handback", prompt_template="h"),
    )
    calls, _ = stub(pipeline, tmp_path / "ws", run)
    assert [c["stage"] for c in calls] == ["build", "handback"]
