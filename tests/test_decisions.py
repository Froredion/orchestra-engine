"""The decision-replay harness: case loading, prompt rendering, grading.

No API key and no network — every test here works on files. The one thing worth
covering hardest is that a broken replay is never read as a failing rule, since
that is the mistake that would make the tool worse than useless.
"""

import pytest

from eval.feature.decisions.runner import (
    Case,
    CaseResult,
    Replay,
    build_prompt,
    load_case,
    parse_decision,
    report,
)
from pathlib import Path


CASES = Path(__file__).resolve().parents[1] / "eval" / "feature" / "decisions" / "cases"


@pytest.fixture
def case_dir(tmp_path):
    """A minimal, self-contained case, with its own one-line stage prompt."""
    pipelines = tmp_path / "pipelines" / "demo"
    pipelines.mkdir(parents=True)
    (pipelines / "01-decide.md").write_text(
        "---\ndecide: build\n---\nYou are DECIDE.\n"
        "Request: {task}\nArtifacts: {artifacts}\n{handoff}\n",
        encoding="utf-8",
    )
    directory = tmp_path / "cases" / "demo-case"
    directory.mkdir(parents=True)
    (directory / "request.md").write_text("Make the thing.\n", encoding="utf-8")
    (directory / "NOTES.md").write_text("What the last stage measured.\n", encoding="utf-8")
    (directory / "handoff.md").write_text("--- HANDOFF: build ---\nbuilt it\n", encoding="utf-8")
    (directory / "case.md").write_text(
        "---\n"
        "pipeline: demo\n"
        "rules: 01-decide.md\n"
        "task: request.md\n"
        "documents: NOTES.md\n"
        "handoff: handoff.md\n"
        "answers: hold, route-back\n"
        "correct: hold\n"
        "observed: route-back\n"
        "---\n"
        "Do you route this back?\n",
        encoding="utf-8",
    )
    return directory, tmp_path / "pipelines"


def test_load_case_resolves_every_path(case_dir):
    directory, pipelines = case_dir
    case = load_case(directory, pipelines_dir=pipelines)
    assert case.name == "demo-case"
    assert case.rules[0].name == "01-decide.md"
    assert case.task.name == "request.md"
    assert [d.name for d in case.documents] == ["NOTES.md"]
    assert case.answers == ["hold", "route-back"]
    assert case.correct == "hold"
    assert case.observed == "route-back"
    assert case.question == "Do you route this back?"


def test_load_case_rejects_a_correct_answer_not_in_answers(case_dir):
    directory, pipelines = case_dir
    text = (directory / "case.md").read_text(encoding="utf-8")
    (directory / "case.md").write_text(text.replace("correct: hold", "correct: nope"), "utf-8")
    with pytest.raises(ValueError, match="not one of answers"):
        load_case(directory, pipelines_dir=pipelines)


def test_load_case_rejects_a_missing_file_before_anything_is_paid_for(case_dir):
    directory, pipelines = case_dir
    text = (directory / "case.md").read_text(encoding="utf-8")
    (directory / "case.md").write_text(text.replace("documents: NOTES.md", "documents: GONE.md"), "utf-8")
    with pytest.raises(FileNotFoundError, match="GONE.md"):
        load_case(directory, pipelines_dir=pipelines)


def test_build_prompt_renders_the_stage_file_the_way_the_engine_does(case_dir):
    directory, pipelines = case_dir
    case = load_case(directory, pipelines_dir=pipelines)
    artifacts = Path("/runs/artifacts")
    prompt = build_prompt(case, artifacts)

    # The stage file's own frontmatter is the engine's, not the agent's.
    assert "decide: build" not in prompt
    assert "You are DECIDE." in prompt
    # Placeholders filled, exactly as Pipeline.build_prompt fills them.
    assert "Request: Make the thing." in prompt
    assert f"Artifacts: {artifacts}" in prompt
    # The handoff arrives as a pointer to the tool, never as its contents.
    assert "read_handoff" in prompt
    assert "built it" not in prompt
    # Documents are read off disk, so their text is not in the prompt either.
    assert "What the last stage measured." not in prompt
    assert "Do you route this back?" in prompt
    assert "DECISION: <hold | route-back>" in prompt


def test_build_prompt_omits_the_handoff_pointer_when_a_case_has_none(case_dir):
    directory, pipelines = case_dir
    text = (directory / "case.md").read_text(encoding="utf-8")
    (directory / "case.md").write_text(text.replace("handoff: handoff.md\n", ""), "utf-8")
    case = load_case(directory, pipelines_dir=pipelines)
    assert "read_handoff" not in build_prompt(case, Path("/a"))


@pytest.mark.parametrize(
    "summary, expected",
    [
        ("blah\nDECISION: hold\nBECAUSE: x", "hold"),
        ("DECISION: route-back", "route-back"),
        ("  DECISION:   HOLD  ", "hold"),  # case-insensitive, normalised to the case's spelling
        ("I will end with DECISION: hold\n...\nDECISION: route-back", "route-back"),  # last wins
        ("no verdict here", None),
        ("DECISION: maybe", None),  # not one of the answers
    ],
)
def test_parse_decision(summary, expected):
    assert parse_decision(summary, ["hold", "route-back"]) == expected


def _result(decisions):
    case = Case(
        name="c",
        directory=Path("."),
        question="q",
        rules=[],
        documents=[],
        answers=["hold", "route-back"],
        correct="hold",
        observed="route-back",
    )
    replays = [Replay(index=i, decision=d, summary="") for i, d in enumerate(decisions, 1)]
    return CaseResult(case=case, replays=replays)


def test_report_reads_a_dead_batch_as_nothing_measured():
    """The mistake that would matter: scoring a broken harness as a failing rule."""
    text = report(_result([None, None, None]))
    assert "nothing measured" in text
    assert "reproduces the live failure" not in text


def test_report_flags_a_partial_batch_rather_than_averaging_it_away():
    text = report(_result(["hold", "hold", None]))
    assert "partial" in text
    assert "1 replay(s) lost" in text


def test_report_readings():
    assert "reproduces the live failure" in report(_result(["route-back", "route-back"]))
    assert "flipped every time" in report(_result(["hold", "hold"]))
    assert "unstable" in report(_result(["hold", "route-back"]))


def test_the_shipped_case_loads():
    """The seeded case is data, and data rots. This is the check that it hasn't."""
    case = load_case(CASES / "using-bracket")
    assert case.correct == "hold"
    assert case.observed == "route-back"
    assert case.commit and len(case.commit) == 40
    names = [d.name for d in case.documents]
    # The blind reading is the input whose absence made the first draft of this
    # case measure an easier decision than the one that actually failed.
    assert ".verify-independent-reading.md" in names
    assert build_prompt(case, Path("/a")).count("DECISION:") == 1


def test_a_control_case_is_read_as_a_control():
    """observed == correct means the live run got it right: the case exists to
    catch a rule that breaks it, not to be flipped."""
    case = Case(
        name="c", directory=Path("."), question="q", rules=[], documents=[],
        answers=["hold", "route-back"], correct="route-back", observed="route-back",
    )
    assert case.is_control
    held = CaseResult(case=case, replays=[Replay(1, "route-back", "")])
    assert "control held" in report(held)
    broken = CaseResult(case=case, replays=[Replay(1, "hold", "")])
    assert "CONTROL BROKEN" in report(broken)
    # Never the reading meant for a case the live run got wrong.
    assert "reproduces the live failure" not in report(broken)


def test_the_shipped_control_case_loads():
    case = load_case(CASES / "await-using-fallthrough")
    assert case.correct == "route-back"
    assert case.is_control
    assert not load_case(CASES / "using-bracket").is_control


def test_ablations_change_exactly_one_thing_each(case_dir):
    """A harness self-check is only worth running if it varies one variable."""
    directory, pipelines = case_dir
    case = load_case(directory, pipelines_dir=pipelines)
    base = build_prompt(case, Path("/a"))
    measure = build_prompt(case, Path("/a"), "may-measure")
    flipped = build_prompt(case, Path("/a"), "flip-answers")

    assert "and you cannot" in base and "and you cannot" not in measure
    assert "DECISION: <hold | route-back>" in base
    assert "DECISION: <hold | route-back>" in measure  # order untouched
    assert "DECISION: <route-back | hold>" in flipped
    assert "and you cannot" in flipped  # clause untouched


def test_unknown_ablation_is_refused(case_dir):
    directory, pipelines = case_dir
    case = load_case(directory, pipelines_dir=pipelines)
    with pytest.raises(ValueError, match="unknown ablation"):
        build_prompt(case, Path("/a"), "wishful-thinking")
