"""A session that ends its turn and is woken again.

The frames here are what a real session produced (SDK 0.2.178, CLI 2.1.318), so a
test failing means the engine's reading of that stream changed. The sequence that
matters: a stage backgrounds a command and ends its turn, the SDK closes the
session's stdin, the command finishes, and the CLI starts another turn in the same
session. In an interactive stage every tool call of that turn is then refused in
milliseconds, with no prompt shown, and the CLI reports success.
"""

import json
import mmap
from pathlib import Path

import claude_agent_sdk
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from orchestra_engine import channel
from orchestra_engine.engine import run_loop, session
from orchestra_engine.model import Pipeline, Stage

SID = "93b6e014-507a-4e01-8701-813d048334bb"
REFUSAL = (
    "The user doesn't want to take this action right now. STOP what you are doing "
    "and wait for the user to tell you how to proceed."
)


def init():
    return SystemMessage(subtype="init", data={"session_id": SID})


def calls(tool_id, name, **tool_input):
    block = ToolUseBlock(id=tool_id, name=name, input=tool_input)
    return AssistantMessage(content=[block], model="claude-haiku-5-5")


def says(text):
    return AssistantMessage(content=[TextBlock(text=text)], model="claude-haiku-5-5")


def answered(tool_id, text, is_error=False, parent=None):
    block = ToolResultBlock(tool_use_id=tool_id, content=text, is_error=is_error)
    return UserMessage(content=[block], parent_tool_use_id=parent)


def result(text, turns, ms, cost):
    return ResultMessage(
        subtype="success", duration_ms=ms, duration_api_ms=ms, is_error=False,
        num_turns=turns, session_id=SID, total_cost_usd=cost, result=text,
    )


def background_wait():
    """Start a background command and end the turn on it."""
    return [
        init(),
        calls("t1", "Bash", command="sleep 25", run_in_background=True),
        answered("t1", "Command running in background with ID: b1."),
        says("Waiting for Studio to start."),
        result("Waiting for Studio to start.", turns=2, ms=120_000, cost=0.70),
    ]


def scripted(*frames):
    def fake_query(prompt, options):
        async def stream():
            for frame in frames:
                yield frame

        return stream()

    return fake_query


@pytest.fixture
def stage(run, monkeypatch, tmp_path):
    """Run one session over these frames; returns (result, log lines)."""

    def _run(*frames):
        monkeypatch.setattr(session, "query", scripted(*frames))
        lines = []
        outcome = run(
            session.run_stage(
                "place", "p", tmp_path / "ws", tmp_path / "a", interactive=True, log=lines.append
            )
        )
        return outcome, lines

    return _run


def test_tool_calls_refused_after_waking_fail_the_stage(stage):
    outcome, lines = stage(
        *background_wait(),
        init(),
        calls("t2", "Read", file_path="note.txt"),
        answered("t2", REFUSAL, is_error=True),
        calls("t3", "mcp__superbullet__run_lua_code", code="return 1"),
        answered("t3", REFUSAL, is_error=True),
        says("It was rejected, so I've stopped."),
        result("It was rejected, so I've stopped.", turns=3, ms=9_500, cost=0.75),
    )
    assert outcome.is_error, "the CLI said success, and it was not"
    assert "2 tool call(s) (Read, mcp__superbullet__run_lua_code) were refused" in outcome.summary
    assert "woken" in outcome.summary
    assert any("STAGE FAILED" in line for line in lines)


def test_accounting_covers_the_whole_session(stage):
    """run.json used to record only the last turn: 9.5 s and 3 turns for a stage of
    over two minutes. Turns and duration restart each turn; cost does not."""
    outcome, _ = stage(
        *background_wait(),
        init(),
        says("Done."),
        result("Done.", turns=3, ms=9_500, cost=0.75),
    )
    assert outcome.num_turns == 5
    assert outcome.duration_ms == 129_500
    assert outcome.cost_usd == 0.75, "the CLI's cost total is already cumulative"
    assert outcome.segments == 2


def test_every_turns_report_reaches_the_summary(stage):
    """The turn that did the work isn't always the one that ended the session."""
    outcome, _ = stage(
        init(),
        result("The whole feature is built and tested.", turns=40, ms=60_000, cost=1.0),
        init(),
        result("The build I left running finished.", turns=1, ms=2_000, cost=1.1),
    )
    assert "The whole feature is built and tested." in outcome.summary
    assert "The build I left running finished." in outcome.summary


def test_a_background_agent_waking_the_session_is_not_a_failure(stage):
    """The SDK holds stdin open for a delegated agent, so this follow-up turn can use
    its tools. Failing on the second session start alone would break it."""
    outcome, lines = stage(
        init(),
        calls("t1", "Agent", prompt="walk the repo", run_in_background=True),
        answered("t1", "Async agent launched successfully."),
        result("Waiting for the agent to finish.", turns=2, ms=2_000, cost=0.01),
        init(),
        calls("t2", "Write", file_path="REPORT.md"),
        answered("t2", "File created successfully."),
        result("Report written.", turns=2, ms=2_000, cost=0.02),
    )
    assert not outcome.is_error
    assert outcome.segments == 2
    assert any("WOKEN" in line for line in lines), "still worth saying it happened"


def test_a_human_refusing_is_not_an_unasked_refusal(stage):
    """can_use_tool has its own wording, and the run going on from a real no is the
    agent's call."""
    outcome, _ = stage(
        init(),
        calls("t1", "Bash", command="rm -rf build"),
        answered("t1", "The human operator denied this action.", is_error=True),
        result("Left the build directory alone.", turns=2, ms=1_000, cost=0.01),
    )
    assert not outcome.is_error


def test_an_unattended_denial_is_not_an_unasked_refusal(stage):
    """What a -y stage gets for a call its permission mode won't allow: the same in
    any turn, and the agent can work around it."""
    outcome, _ = stage(
        *background_wait(),
        init(),
        calls("t2", "Bash", command="mkdir out"),
        answered("t2", "Claude requested permissions to write to out, but you haven't granted it yet.", is_error=True),
        result("Couldn't make the directory.", turns=2, ms=1_000, cost=0.75),
    )
    assert not outcome.is_error


def test_a_refusal_inside_a_sub_agent_does_not_fail_the_stage(stage):
    """The parent still has its tools and gets to decide what the failure means."""
    outcome, _ = stage(
        init(),
        calls("t1", "Agent", prompt="look around"),
        answered("t2", REFUSAL, is_error=True, parent="t1"),
        answered("t1", "The sub-agent couldn't read it."),
        result("Read it myself instead.", turns=3, ms=1_000, cost=0.01),
    )
    assert not outcome.is_error


@pytest.mark.parametrize(
    "text, is_error",
    [
        (f"grep found: {REFUSAL}", True),  # a failing command printing the sentence
        (REFUSAL, False),  # a stage reading a transcript that holds it
    ],
)
def test_output_that_only_quotes_the_refusal_does_not_fail_the_stage(stage, text, is_error):
    outcome, _ = stage(
        init(),
        calls("t1", "Bash", command="grep -r STOP logs"),
        answered("t1", text, is_error=is_error),
        result("Looked at the logs.", turns=2, ms=1_000, cost=0.01),
    )
    assert not outcome.is_error


def test_a_session_of_one_turn_reports_as_it_always_did(stage):
    outcome, lines = stage(
        init(),
        says("All done."),
        result("All done.", turns=4, ms=5_000, cost=0.25),
    )
    assert (outcome.summary, outcome.is_error) == ("All done.", False)
    assert (outcome.num_turns, outcome.duration_ms, outcome.cost_usd) == (4, 5_000, 0.25)
    assert outcome.segments == 1
    assert not any("WOKEN" in line for line in lines)


def test_every_stage_is_told_not_to_end_its_turn_to_wait():
    text = session.WORKSPACE_PREAMBLE
    assert "Ending your turn ends this stage" in text
    assert "never end it to wait" in text
    # The CLI blocks a long foreground sleep and suggests a background command or
    # Monitor, which is how a stage ends up here. So it needs a wait that works.
    assert "until <check>" in text


def test_the_bundled_cli_still_says_what_the_engine_listens_for():
    """The refusal is matched on the CLI's text, and the CLI ships inside the SDK. If
    an SDK upgrade rewords it this fails, rather than stages going back to reporting
    ok without anyone noticing."""
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
    binaries = [p for p in bundled.glob("claude*") if p.is_file()] if bundled.is_dir() else []
    if not binaries:
        pytest.skip("this install of the SDK bundles no CLI")
    with binaries[0].open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        assert data.find(session.UNASKED_REFUSAL.encode()) != -1


def test_the_run_stops_at_a_refused_stage_and_says_so(run, monkeypatch, tmp_path):
    """The whole path from the trial: nothing after the stage runs, and everything a
    driver reads says it failed."""
    frames = [
        *background_wait(),
        init(),
        calls("t2", "Bash", command="mkdir out"),
        answered("t2", REFUSAL, is_error=True),
        result("It was rejected.", turns=2, ms=9_500, cost=0.75),
    ]
    prompts = []

    def fake_query(prompt, options):
        prompts.append(prompt)
        return scripted(*frames)(prompt, options)

    monkeypatch.setattr(session, "query", fake_query)
    pipeline = Pipeline(
        name="t",
        stages=(Stage(name="place", prompt_template="{task}"), Stage(name="system", prompt_template="{task}")),
        handoff_template="{summary}",
    )
    chan = channel.open_channel(tmp_path / "chan", forbidden={})
    try:
        results = run(
            run_loop(
                task="T", workspace=tmp_path / "ws", pipeline=pipeline,
                runs_dir=tmp_path / "runs", log=lambda _: None,
            )
        )
        events = channel.read_events(chan.dir)
    finally:
        channel.close_channel()

    assert len(prompts) == 1, "the next stage must not start"
    assert [r.stage for r in results] == ["place"] and results[0].is_error

    kinds = [e["event"] for e in events]
    assert "stage_woken" in kinds
    finished = next(e for e in events if e["event"] == "stage_finished")
    assert finished["is_error"] is True
    assert finished["num_turns"] == 4
    assert finished["duration_seconds"] >= 0 and finished["duration_hms"]
    assert "refused" in finished["summary"]
    assert next(e for e in events if e["event"] == "run_complete")["failed_stage"] == "place"

    (log_file,) = (tmp_path / "runs").glob("*/run.json")
    logged = json.loads(log_file.read_text(encoding="utf-8"))["stages"][0]
    assert logged["is_error"] is True
    assert (logged["num_turns"], logged["duration_ms"], logged["segments"]) == (4, 129_500, 2)
