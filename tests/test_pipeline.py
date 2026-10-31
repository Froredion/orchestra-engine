"""Pipeline description layer: loading, frontmatter, placeholders, overrides."""

from pathlib import Path

import pytest

from orchestra_engine import handoff_tool
from orchestra_engine.model import Handoff
from orchestra_engine.pipeline import (
    _parse_stage,
    apply_overrides,
    load_pipeline,
    parse_stage_counts,
    stage_signature,
)


def test_research_stage_order(research):
    assert [s.name for s in research.stages] == [
        "search", "findings", "deep-dive", "report", "understand",
    ]


def test_research_frontmatter(research):
    search, findings, deep, report, understand = research.stages
    assert search.repeat == 3 and search.fan_out is None and search.handoff == "all"
    assert deep.fan_out == "leads.txt" and deep.repeat == 2 and deep.max_items == 5
    assert deep.handoff == "last"  # the default
    assert findings.repeat == 1 and report.repeat == 1 and understand.repeat == 1


def test_frontmatter_stripped_from_template(research):
    assert not research.stages[0].prompt_template.startswith("---")


def test_a_folder_of_plain_files_is_a_working_pipeline(tmp_path):
    """The floor of the format: no frontmatter anywhere still loads and renders,
    so a pipeline author owes the engine nothing but numbered files."""
    (tmp_path / "01-plan.md").write_text("plan {task}{handoff}")
    (tmp_path / "02-build.md").write_text("build {task}{handoff}")

    plain = load_pipeline(str(tmp_path))

    assert [s.name for s in plain.stages] == ["plan", "build"]
    assert all(s.repeat == 1 and s.fan_out is None for s in plain.stages)
    prompt = plain.build_prompt(plain.stages[0], "build X")
    assert "build X" in prompt and "{handoff}" not in prompt


def test_iteration_and_item_placeholders(research):
    search, _, deep, _, _ = research.stages
    prompt = research.build_prompt(search, "topic T", iteration=2)
    assert "pass 2 of 3" in prompt and "search-findings-2.md" in prompt

    prompt = research.build_prompt(
        deep,
        "topic T",
        handoffs=[Handoff("findings", "sess-1", "the leads")],
        item="https://example.com",
        iteration=1,
    )
    assert "https://example.com" in prompt
    assert "pass 1 of 2" in prompt
    # The prompt names who reported; what they said is served by read_handoff.
    assert "findings" in prompt
    assert "the leads" not in prompt and "sess-1" not in prompt


def test_build_prompt_points_at_the_tool_and_carries_no_report(research):
    """The prompt says who reported. It must not say what they reported: that
    text is another agent's account, and putting it here puts it in the same
    channel as this stage's own instructions."""
    search = research.stages[0]
    handoffs = [
        Handoff("search (pass 1/3)", "sess-a", "found A"),
        Handoff("search (pass 2/3)", "sess-b", "found B"),
    ]
    prompt = research.build_prompt(search, "topic T", handoffs=handoffs, iteration=3)

    assert "search (pass 1/3)" in prompt and "search (pass 2/3)" in prompt
    assert handoff_tool.TOOL_NAME in prompt
    for leaked in ("sess-a", "found A", "sess-b", "found B"):
        assert leaked not in prompt


def test_render_serves_one_block_per_handoff(research):
    """What read_handoff returns: the same blocks the prompt used to carry."""
    body = handoff_tool.render(
        [
            Handoff("search (pass 1/3)", "sess-a", "found A"),
            Handoff("search (pass 2/3)", "sess-b", "found B"),
        ],
        research.handoff_template,
    )
    assert body.count("--- END HANDOFF ---") == 2
    for token in ("sess-a", "found A", "sess-b", "found B"):
        assert token in body
    # oldest first
    assert body.index("sess-a") < body.index("sess-b")


def test_build_prompt_with_no_handoffs_is_clean(research):
    prompt = research.build_prompt(research.stages[0], "topic T", iteration=1)
    assert "HANDOFF" not in prompt


def test_handoff_from_result_defaults():
    from orchestra_engine.model import StageResult

    result = StageResult(stage="search", session_id="s1", summary="", is_error=False)
    handoff = Handoff.from_result(result)
    assert handoff.label == "search"
    assert handoff.summary == "(no summary produced)"


def test_apply_overrides(research):
    out = apply_overrides(research, {"search": 5}, {"deep-dive": 2})
    assert out.stages[0].repeat == 5
    assert out.stages[2].max_items == 2
    # original untouched (frozen dataclasses)
    assert research.stages[0].repeat == 3


@pytest.mark.parametrize(
    "repeats,caps",
    [({"nope": 2}, {}), ({}, {"nope": 2}), ({"search": 0}, {}), ({}, {"search": 2})],
)
def test_apply_overrides_rejects(research, repeats, caps):
    with pytest.raises(ValueError):
        apply_overrides(research, repeats, caps)


def test_parse_stage_counts():
    assert parse_stage_counts(" search=5, deep-dive=3 ") == {"search": 5, "deep-dive": 3}
    assert parse_stage_counts(None) == {}
    assert parse_stage_counts("") == {}


@pytest.mark.parametrize("bad", ["search", "search=0", "search=x", "=3", "search=-1"])
def test_parse_stage_counts_rejects(bad):
    with pytest.raises(ValueError):
        parse_stage_counts(bad)


def test_stage_signature(research):
    search, findings, deep, _, _ = research.stages
    assert stage_signature(search) == "search x3 cumulative"
    assert stage_signature(findings) == "findings"
    assert stage_signature(deep) == "deep-dive x2 [parallel per leads.txt, top 5]"


@pytest.mark.parametrize(
    "text",
    [
        "---\nfan_out: ../../etc/passwd\n---\nbody",  # escapes the workspace
        "---\nfan_out: /etc/passwd\n---\nbody",  # absolute path
        "---\nbogus: 1\n---\nbody",  # unknown option
        "---\nrepeat: zero\n---\nbody",  # non-integer
        "---\nrepeat: 0\n---\nbody",  # < 1
        "---\nmax_items: 5\n---\nbody",  # max_items without fan_out
        "---\nrepeat: 2\nbody",  # unclosed frontmatter
        "---\nrepeat: 2\nhandoff: every\n---\nbody",  # not a handoff scope
        "---\nhandoff: all\n---\nbody",  # cumulative handoff without repeat: no-op
        "---\nhandoff: none\ndecide: a\n---\nbody",  # nothing to route on
    ],
)
def test_bad_frontmatter_rejected_at_load(text):
    with pytest.raises(ValueError):
        _parse_stage("x", text, Path("x.md"))


def test_no_frontmatter_is_plain_stage():
    stage = _parse_stage("x", "just a prompt with {task}", Path("x.md"))
    assert stage.repeat == 1 and stage.fan_out is None and stage.max_items is None
    assert stage.handoff == "last"


def test_handoff_none_parses_without_repeat():
    """Unlike `all`, `none` is meaningful on a plain one-pass stage — it is how a
    stage declares that it must not inherit the previous reading."""
    stage = _parse_stage("x", "---\nhandoff: none\n---\nbody", Path("x.md"))
    assert stage.handoff == "none" and stage.repeat == 1
