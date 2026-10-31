"""`redirect:` — a stage file that points at the prompt instead of holding it.

The stage file stays discoverable and small (a number, a name, its shape); the
prose can live wherever it is already maintained, most usefully an existing
SKILL.md. Its body becomes the fallback, so a pipeline whose target isn't on
this machine still runs.
"""

from pathlib import Path

import pytest

from orchestra_engine.model import PLACEHOLDERS, Pipeline, Stage
from orchestra_engine.pipeline import _parse_stage, load_pipeline, stage_signature

SKILL = """---
name: deep-review
description: Review a diff for correctness.
allowed-tools:
  - Read
  - Grep
---

Read every changed line and report what breaks.
"""


def stage(tmp_path: Path, text: str, name: str = "review") -> Stage:
    """Parse a stage file that really exists on disk, so paths resolve."""
    source = tmp_path / f"01-{name}.md"
    source.write_text(text)
    return _parse_stage(name, text, source)


def test_prompt_comes_from_the_target(tmp_path):
    (tmp_path / "SKILL.md").write_text(SKILL)
    s = stage(tmp_path, "---\nredirect: SKILL.md\n---\nfallback body\n")

    assert "Read every changed line" in s.prompt_template
    assert "fallback body" not in s.prompt_template
    assert s.redirect == "SKILL.md"
    assert s.redirect_source == str(tmp_path / "SKILL.md")


def test_the_targets_own_frontmatter_is_ignored_not_parsed(tmp_path):
    """The point of the feature: a SKILL.md loads as-is.

    Its YAML is a different dialect — `name:`, a list under `allowed-tools:` —
    and reading it as stage options would fail the load on every skill file.
    It is cut on its fences, keys unread, and never reaches the prompt.
    """
    (tmp_path / "SKILL.md").write_text(SKILL)
    s = stage(tmp_path, "---\nredirect: SKILL.md\n---\nfallback body\n")

    assert "description:" not in s.prompt_template
    assert "allowed-tools" not in s.prompt_template
    assert s.prompt_template.strip().startswith("Read every changed line")
    assert s.repeat == 1 and s.fan_out is None  # shape stayed with the stage file


def test_missing_target_falls_back_to_the_body(tmp_path):
    s = stage(tmp_path, "---\nredirect: skills/gone/SKILL.md\n---\nReview {task} yourself.\n")

    assert s.prompt_template.strip() == "Review {task} yourself."
    assert s.redirect == "skills/gone/SKILL.md"
    assert s.redirect_source is None  # the tell: nothing was read


@pytest.mark.parametrize("plant", [
    lambda d: (d / "SKILL.md").mkdir(),                    # a directory
    lambda d: (d / "SKILL.md").write_text(""),             # empty
    lambda d: (d / "SKILL.md").write_text("---\nname: x\n---\n\n  \n"),  # nothing but frontmatter
    lambda d: (d / "SKILL.md").write_bytes(b"\xff\xfe\x00bad"),  # not decodable text
])
def test_a_target_that_cannot_be_a_prompt_falls_back(tmp_path, plant):
    plant(tmp_path)
    s = stage(tmp_path, "---\nredirect: SKILL.md\n---\nfallback body\n")

    assert s.prompt_template.strip() == "fallback body"
    assert s.redirect_source is None


def test_redirect_without_a_body_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="still needs a body of its own"):
        stage(tmp_path, "---\nredirect: SKILL.md\n---\n\n")


def test_foreign_braces_survive_rendering(tmp_path):
    """A file written for humans, not for str.format.

    Braces the author never meant as template syntax must come out as the
    literal text they wrote — while the engine's own placeholders still fill in.
    """
    (tmp_path / "SKILL.md").write_text(
        'Post {"status": "ok", "n": 1} to the API, with ${HOME} in the path,\n'
        "typed Result<{ id: string }>. Task: {task}\n"
    )
    s = stage(tmp_path, "---\nredirect: SKILL.md\n---\nfallback body\n")
    prompt = Pipeline("p", (s,), "").build_prompt(s, task="ship it")

    assert '{"status": "ok", "n": 1}' in prompt
    assert "${HOME}" in prompt
    assert "Result<{ id: string }>" in prompt
    assert "Task: ship it" in prompt


def test_every_placeholder_still_interpolates_through_a_redirect(tmp_path):
    """Guards the escape list against drift.

    `_escape_foreign_braces` spares exactly `PLACEHOLDERS`. If `build_prompt`
    ever supplies one that isn't in that tuple, a redirected prompt would hand
    the agent the literal `{name}` instead of the value — silently. Rendering
    all of them through a redirect is what notices.
    """
    (tmp_path / "SKILL.md").write_text(" ".join("{%s}" % n for n in PLACEHOLDERS))
    s = stage(tmp_path, "---\nrepeat: 2\nredirect: SKILL.md\n---\nfallback body\n")
    prompt = Pipeline("p", (s,), "").build_prompt(
        s, task="T", item="I", iteration=1, artifacts="/tmp/a"
    )

    assert "{" not in prompt and "}" not in prompt


def test_shape_still_comes_from_the_stage_file(tmp_path):
    (tmp_path / "SKILL.md").write_text("do the thing")
    s = stage(tmp_path, "---\nrepeat: 3\nhandoff: all\nredirect: SKILL.md\n---\nfallback\n")

    assert s.repeat == 3 and s.handoff == "all"
    assert s.prompt_template == "do the thing"


def test_the_path_resolves_from_the_stage_files_own_directory(tmp_path):
    """`..` and absolute paths are both allowed — unlike fan_out and skip_if,
    this names authored content, not a file an agent wrote mid-run."""
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "review.md").write_text("shared prose")
    (tmp_path / "pipe").mkdir()
    source = tmp_path / "pipe" / "01-review.md"
    text = "---\nredirect: ../shared/review.md\n---\nfallback\n"
    source.write_text(text)

    assert _parse_stage("review", text, source).prompt_template == "shared prose"

    absolute = f"---\nredirect: {tmp_path / 'shared' / 'review.md'}\n---\nfallback\n"
    source.write_text(absolute)
    assert _parse_stage("review", absolute, source).prompt_template == "shared prose"


def test_a_redirect_inside_the_target_is_not_followed(tmp_path):
    """One hop, so no chains and no cycles: the target's frontmatter is dropped
    whole, and a `redirect:` sitting in it goes with it."""
    (tmp_path / "b.md").write_text("second hop")
    (tmp_path / "a.md").write_text("---\nredirect: b.md\n---\nfirst hop\n")
    s = stage(tmp_path, "---\nredirect: a.md\n---\nfallback\n")

    assert s.prompt_template.strip() == "first hop"
    assert s.redirect_source == str(tmp_path / "a.md")


def test_signature_says_where_the_prompt_came_from(tmp_path):
    (tmp_path / "SKILL.md").write_text("prose")
    found = stage(tmp_path, "---\nredirect: SKILL.md\n---\nfallback\n")
    assert stage_signature(found) == "review [prompt from SKILL.md]"

    missing = stage(tmp_path, "---\nredirect: gone.md\n---\nfallback\n")
    assert stage_signature(missing) == "review [gone.md not found — running this file's own body]"


def test_a_pipeline_of_redirects_loads(tmp_path):
    """End to end through load_pipeline: the stage files are pointers, and the
    prompts live one directory over."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "PLAN.md").write_text("---\nname: plan\n---\nPlan {task}.\n")
    (tmp_path / "pipe").mkdir()
    (tmp_path / "pipe" / "01-plan.md").write_text(
        "---\nredirect: ../skills/PLAN.md\n---\nPlan it from first principles.\n"
    )
    (tmp_path / "pipe" / "02-build.md").write_text("Build it. {task}")

    pipeline = load_pipeline(str(tmp_path / "pipe"))

    assert [s.name for s in pipeline.stages] == ["plan", "build"]
    assert pipeline.stages[0].prompt_template.strip() == "Plan {task}."
    assert pipeline.build_prompt(pipeline.stages[0], "X").strip() == "Plan X."
