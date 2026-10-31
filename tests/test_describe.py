"""--list: what an agent with no context learns before running anything."""

from pathlib import Path

import pytest

from orchestra_engine.main import describe_pipelines, graph_pipelines
from orchestra_engine.pipeline import builtin_pipelines, load_pipeline, pipeline_mermaid


def test_describes_every_builtin():
    out = describe_pipelines(builtin_pipelines())
    for name in ("bugfix", "research", "feature"):
        # header is "<name>  (<resolved dir>)"
        assert f"\n{name}  (" in out


def test_shows_each_stage_and_its_shape():
    out = describe_pipelines(["bugfix"])
    # The shape is the explanation: a reader learns a stage fans out over a file
    # an earlier stage writes, is gated, and can route the run back — without
    # running it.
    assert "parallel per hypotheses.txt" in out
    assert "skipped if verdict.txt contains 'STOP'" in out
    assert "decides: may loop back to reproduce, localize, diagnose, fix" in out


def test_points_at_the_prompt_files():
    """The signature is a summary; the prompts are the ground truth."""
    assert str(Path("orchestra_engine", "pipelines", "feature")) in describe_pipelines(["feature"])


def test_describes_a_custom_pipeline_directory(tmp_path):
    (tmp_path / "01-draft.md").write_text("---\nrepeat: 2\n---\ndraft {task}{handoff}")
    (tmp_path / "02-polish.md").write_text("polish {task}{handoff}")

    out = describe_pipelines([str(tmp_path)])

    assert "draft x2" in out
    assert "polish" in out


def test_unknown_pipeline_raises_rather_than_printing_nonsense():
    with pytest.raises(ValueError, match="not found"):
        describe_pipelines(["no-such-pipeline"])


# --- --graph -------------------------------------------------------------


def test_graph_emits_a_fenced_mermaid_flowchart():
    out = graph_pipelines(["bugfix"])
    assert "```mermaid" in out and "flowchart TD" in out
    assert "prevent · terminal" in out  # terminal annotated
    assert "fan-out" in out and "skip if STOP" in out  # diagnose / fix+verify
    assert "-->|back|" in out  # at least one loop-back edge


def test_graph_back_edges_match_the_decide_targets():
    """The picture is generated from the stages, so every declared jump target
    is one back-edge and no edge is invented."""
    p = load_pipeline("bugfix")
    mer = pipeline_mermaid(p)
    expected = sum(len(s.decide_targets or ()) for s in p.stages)
    assert mer.count("-->|back|") == expected == 6  # localize 1 + fix 1 + verify 4


def test_graph_unknown_pipeline_raises():
    with pytest.raises(ValueError, match="not found"):
        graph_pipelines(["no-such-pipeline"])
