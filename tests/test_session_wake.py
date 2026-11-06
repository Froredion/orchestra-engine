"""A session that ends its turn and is woken again.

The frames here are what a real session produced (SDK 0.2.178, CLI 2.1.318), so a
test failing means the engine's reading of that stream changed. The sequence that
matters: a stage backgrounds a command and ends its turn, the SDK closes the
session's stdin, the command finishes, and the CLI starts another turn in the same
session. In an interactive stage every tool call of that turn is then refused in
milliseconds, with no prompt shown, and the CLI reports success.
"""

import asyncio
import json
import mmap
from pathlib import Path

import claude_agent_sdk
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
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


@pytest.fixture
def play(run, script_sessions, tmp_path):
    """Run one session over these frames; returns (result, log lines, its client). The
    timeout turns a session the engine never let go of into a failure, not a hung suite."""

    def _play(*frames, interactive=True):
        clients = script_sessions(*frames)
        lines = []
        outcome = run(
            asyncio.wait_for(
                session.run_stage(
                    "place", "p", tmp_path / "ws", tmp_path / "a", interactive=interactive, log=lines.append
                ),
                timeout=10,
            )
        )
        return outcome, lines, clients[0]

    return _play


@pytest.fixture
def stage(play):
    def _run(*frames):
        outcome, lines, _ = play(*frames)
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


def test_the_run_stops_at_a_refused_stage_and_says_so(run, script_sessions, tmp_path):
    """The whole path from the trial: nothing after the stage runs, and everything a
    driver reads says it failed."""
    clients = script_sessions(
        *background_wait(),
        init(),
        calls("t2", "Bash", command="mkdir out"),
        answered("t2", REFUSAL, is_error=True),
        result("It was rejected.", turns=2, ms=9_500, cost=0.75),
    )
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

    assert len(clients) == 1, "the next stage must not start"
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


# A session that ends its turn with a background command still running. The CLI sends
# nothing at all while it holds the session, so these frames are all there is to go on.
# They're what a real session produced, like the ones above.

SERVER = "python -m http.server 8000"
TICK = 0.05


def background_task(task_id, tool_id, task_type="local_bash"):
    return TaskStartedMessage(
        subtype="task_started", data={}, task_id=task_id, description=f"the {task_id} task",
        uuid="u", session_id=SID, tool_use_id=tool_id, task_type=task_type,
    )


def task_ended(task_id, status="completed"):
    return TaskNotificationMessage(
        subtype="task_notification", data={}, task_id=task_id, status=status,
        output_file="out", summary="s", uuid="u", session_id=SID,
    )


def task_updated(task_id, status):
    return TaskUpdatedMessage(
        subtype="task_updated", data={}, task_id=task_id, patch={"status": status}, status=status,
    )


def left_a_server():
    return [
        init(),
        calls("t1", "Bash", command=SERVER, run_in_background=True),
        background_task("b1", "t1"),
        answered("t1", "Command running in background with ID: b1."),
        says("Dev server started."),
        result("Dev server started.", turns=2, ms=5_000, cost=0.01),
    ]


async def until_stopped(client):
    await client.stop_requested.wait()


async def until_stdin_closes(client):
    await asyncio.wait_for(client.stdin_closed.wait(), timeout=1)


async def stdin_still_open(client):
    assert not client.stdin_closed.is_set()


async def longer_than_the_limit(client):
    await asyncio.sleep(TICK * 4)


async def never(client):
    await asyncio.Event().wait()


@pytest.fixture
def short_limit(monkeypatch):
    monkeypatch.setenv(session.SHELL_WAIT_ENV, str(TICK))


def test_a_command_left_running_is_stopped_and_fails_the_stage(play, short_limit):
    outcome, lines, client = play(
        *left_a_server(),
        until_stopped,
        task_updated("b1", "killed"),
        task_ended("b1", "stopped"),
    )
    assert client.stopped == ["b1"], "stopped through the CLI, which ending the session doesn't do"
    assert outcome.is_error, "the CLI said success, and the stage's command is gone"
    assert outcome.summary.startswith("STAGE FAILED")
    assert f"`{SERVER}`" in outcome.summary, "it has to say which command"
    assert session.SHELL_WAIT_ENV in outcome.summary, "and how to give it longer"
    assert "Dev server started." in outcome.summary, "the session's own account stays"
    assert "It stopped them first" in outcome.summary
    assert "couldn't stop" not in outcome.summary
    assert client.stdin_closed.is_set() and client.disconnected
    assert any(line.startswith("[place] STOPPING") and SERVER in line for line in lines)


def test_the_wait_is_announced_when_it_starts_not_when_it_ends(play, short_limit, tmp_path):
    """A stalled run looks like a slow one unless something says otherwise."""
    chan = channel.open_channel(tmp_path / "chan", forbidden={})
    try:
        _, lines, _ = play(*left_a_server(), until_stopped, task_ended("b1", "stopped"))
        events = channel.read_events(chan.dir)
    finally:
        channel.close_channel()

    waiting = next(e for e in events if e["event"] == "stage_waiting")
    assert waiting["stage"] == "place"
    assert waiting["commands"] == [SERVER]
    assert waiting["limit_seconds"] == TICK
    said = [line for line in lines if "WAITING" in line or "STOPPING" in line]
    assert "WAITING" in said[0] and SERVER in said[0]
    assert "STOPPING" in said[1]


@pytest.mark.parametrize(
    "tool, tool_input",
    [
        ("Bash", {"command": SERVER, "description": "Start the server"}),
        ("PowerShell", {"command": SERVER, "description": "Start the server"}),
        ("Monitor", {"command": SERVER, "description": "Watch the server"}),
    ],
)
def test_the_report_names_the_command_whichever_tool_started_it(play, short_limit, tool, tool_input):
    outcome, _, _ = play(
        init(),
        calls("t1", tool, **tool_input),
        background_task("b1", "t1"),
        answered("t1", "Running in the background."),
        result("Started.", turns=2, ms=1_000, cost=0.01),
        until_stopped,
        task_ended("b1", "stopped"),
    )
    assert f"`{SERVER}`" in outcome.summary


def test_a_command_with_no_tool_call_to_go_on_is_named_by_its_description(play, short_limit):
    outcome, _, _ = play(
        init(),
        background_task("b1", "t1"),
        result("Started.", turns=2, ms=1_000, cost=0.01),
        until_stopped,
        task_ended("b1", "stopped"),
    )
    assert "`the b1 task`" in outcome.summary


def test_a_command_that_finishes_in_time_wakes_a_turn_that_can_still_answer(play):
    """stdin stays open across the wait. A closed one is what got every tool call of the
    woken turn refused, with no prompt shown."""
    outcome, lines, client = play(
        *left_a_server(),
        stdin_still_open,
        task_updated("b1", "completed"),
        task_ended("b1"),
        stdin_still_open,
        init(),
        calls("t2", "Read", file_path="out.log"),
        stdin_still_open,
        answered("t2", "all good"),
        result("The server exited cleanly.", turns=2, ms=2_000, cost=0.02),
        until_stdin_closes,
    )
    assert not outcome.is_error
    assert outcome.segments == 2
    assert client.stopped == []
    assert any("WOKEN" in line for line in lines)


def test_nothing_holds_the_session_past_a_turn_with_nothing_running(play):
    outcome, lines, client = play(
        init(),
        says("All done."),
        result("All done.", turns=2, ms=1_000, cost=0.01),
        until_stdin_closes,
    )
    assert not outcome.is_error
    assert client.stopped == []
    assert not any("WAITING" in line for line in lines)


@pytest.mark.parametrize(
    "ending",
    [
        pytest.param([task_ended("b1")], id="finished by itself"),
        pytest.param([task_updated("b1", "killed")], id="killed, with no notification"),
        pytest.param([task_updated("b1", "killed"), task_ended("b1", "stopped")], id="both frames"),
    ],
)
def test_a_command_over_before_the_turn_ends_is_no_reason_to_wait(play, ending):
    """Foreground commands report like this too, and so does one the agent stopped."""
    outcome, lines, client = play(
        init(),
        calls("t1", "Bash", command="sleep 2", run_in_background=True),
        background_task("b1", "t1"),
        answered("t1", "Command running in background with ID: b1."),
        *ending,
        result("Done.", turns=2, ms=1_000, cost=0.01),
        until_stdin_closes,
    )
    assert not outcome.is_error
    assert client.stopped == []
    assert not any("WAITING" in line for line in lines)


def test_a_status_that_is_not_an_ending_changes_nothing(play, short_limit):
    outcome, _, client = play(
        *left_a_server()[:3],
        task_updated("b1", "running"),
        answered("t1", "Command running in background with ID: b1."),
        result("Dev server started.", turns=2, ms=5_000, cost=0.01),
        until_stopped,
        task_ended("b1", "stopped"),
    )
    assert client.stopped == ["b1"]
    assert outcome.is_error


def test_a_command_that_ends_with_no_wake_lets_the_session_go(play, monkeypatch):
    """Not every ending wakes the session, and nothing else would release stdin."""
    monkeypatch.setattr(session, "SHELL_SETTLE_SECONDS", TICK)
    outcome, _, client = play(*left_a_server(), task_ended("b1"))
    assert not outcome.is_error
    assert client.stopped == []
    assert client.stdin_closed.is_set()


def test_a_turn_under_way_is_not_cut_off_and_being_woken_buys_no_more_time(play, short_limit):
    """The CLI can wake a session just to check on a command. The command's clock keeps
    running from the turn that left it, and only takes effect between turns."""
    seen = {}

    async def not_stopped_yet(client):
        seen["stopped_during_the_turn"] = list(client.stopped)

    outcome, _, client = play(
        *left_a_server(),
        init(),
        longer_than_the_limit,
        not_stopped_yet,
        says("It's a server, so I'll leave it."),
        result("It's a server, so I'll leave it.", turns=1, ms=1_000, cost=0.02),
        until_stopped,
        task_ended("b1", "stopped"),
    )
    assert seen["stopped_during_the_turn"] == []
    assert client.stopped == ["b1"]
    assert outcome.is_error


def test_a_background_agent_is_left_to_the_sdk(play, short_limit):
    """The SDK and the CLI hold the session for it, and the agent may be using the
    server, so neither gets touched however long the agent takes. This is what
    BG_WAIT_DEFAULT_MS is for."""
    outcome, lines, client = play(
        init(),
        calls("t1", "Bash", command=SERVER, run_in_background=True),
        background_task("b1", "t1"),
        answered("t1", "Command running in background with ID: b1."),
        calls("t2", "Agent", prompt="test the server", run_in_background=True),
        background_task("a1", "t2", task_type="local_agent"),
        answered("t2", "Async agent launched successfully."),
        result("Waiting for the agent.", turns=3, ms=2_000, cost=0.01),
        until_stdin_closes,
        longer_than_the_limit,
        task_ended("a1"),
        init(),
        calls("t3", "TaskStop", task_id="b1"),
        task_updated("b1", "killed"),
        answered("t3", "Successfully stopped task."),
        result("Report written.", turns=2, ms=2_000, cost=0.02),
    )
    assert not outcome.is_error
    assert client.stopped == []
    assert not any("WAITING" in line for line in lines)


def test_a_command_outlasting_the_agent_fails_the_stage_and_says_it_may_be_running(play, short_limit, monkeypatch):
    """stdin went with the agent's wait, so stop_task can't reach the CLI. The run still
    doesn't stall, and whoever reads the report knows to go and look."""
    monkeypatch.setattr(session, "SESSION_EXIT_GRACE_SECONDS", TICK)
    outcome, _, client = play(
        init(),
        calls("t1", "Bash", command=SERVER, run_in_background=True),
        background_task("b1", "t1"),
        answered("t1", "Command running in background with ID: b1."),
        calls("t2", "Agent", prompt="test the server", run_in_background=True),
        background_task("a1", "t2", task_type="local_agent"),
        answered("t2", "Async agent launched successfully."),
        result("Waiting for the agent.", turns=3, ms=2_000, cost=0.01),
        until_stdin_closes,
        task_ended("a1"),
        init(),
        says("The agent's done."),
        result("The agent's done.", turns=1, ms=2_000, cost=0.02),
        never,
    )
    assert outcome.is_error
    assert f"`{SERVER}`" in outcome.summary
    assert "couldn't stop" in outcome.summary and "still running" in outcome.summary
    assert "It stopped them first" not in outcome.summary, "it didn't"
    assert client.stopped == []
    assert client.disconnected


def test_an_agent_that_ends_with_no_wake_still_starts_the_clock(play, short_limit, monkeypatch):
    """Not every ending wakes the session, and there'd be no result to start it from."""
    monkeypatch.setattr(session, "SESSION_EXIT_GRACE_SECONDS", TICK)
    monkeypatch.setattr(session, "SHELL_SETTLE_SECONDS", TICK)
    outcome, lines, _ = play(
        init(),
        calls("t1", "Bash", command=SERVER, run_in_background=True),
        background_task("b1", "t1"),
        calls("t2", "Agent", prompt="test the server", run_in_background=True),
        background_task("a1", "t2", task_type="local_agent"),
        result("Waiting for the agent.", turns=3, ms=2_000, cost=0.01),
        until_stdin_closes,
        task_ended("a1"),
        never,
    )
    assert outcome.is_error
    assert any("WAITING" in line for line in lines)


@pytest.mark.parametrize("kind", ["post_turn_memory", "dream", "auto_mode_scan"])
def test_the_clis_own_housekeeping_neither_holds_a_command_nor_gets_stopped(play, short_limit, kind):
    """Only agents get the SDK's patience. Taking one of these for an agent would let go
    of stdin with no result coming to start the clock, and the stall would be back."""
    outcome, _, client = play(
        *left_a_server()[:3],
        background_task("h1", "t9", task_type=kind),
        result("Dev server started.", turns=2, ms=5_000, cost=0.01),
        stdin_still_open,
        until_stopped,
        task_ended("b1", "stopped"),
    )
    assert client.stopped == ["b1"]
    assert outcome.is_error


def test_one_of_two_commands_ending_does_not_let_the_other_off(play, short_limit, monkeypatch):
    monkeypatch.setattr(session, "SHELL_SETTLE_SECONDS", TICK)
    outcome, _, client = play(
        init(),
        calls("t1", "Bash", command=SERVER, run_in_background=True),
        background_task("b1", "t1"),
        calls("t2", "Bash", command="sleep 1", run_in_background=True),
        background_task("b2", "t2"),
        result("Started both.", turns=3, ms=1_000, cost=0.01),
        task_ended("b2"),
        until_stopped,
        task_ended("b1", "stopped"),
    )
    assert client.stopped == ["b1"]
    assert outcome.is_error
    assert f"`{SERVER}`" in outcome.summary and "sleep 1" not in outcome.summary


def test_a_cli_that_will_not_exit_is_ended_anyway(play, short_limit, monkeypatch):
    """stop_task is the clean way out, but nothing should depend on the CLI's cooperation."""
    monkeypatch.setattr(session, "SESSION_EXIT_GRACE_SECONDS", TICK)
    outcome, _, client = play(*left_a_server(), until_stopped, never)
    assert outcome.is_error
    assert client.disconnected


@pytest.mark.parametrize("interactive", [True, False])
def test_every_session_shape_can_be_stopped(play, short_limit, interactive):
    """-i and -y both hold the prompt stream open; the SDK closes stdin at once otherwise."""
    outcome, _, client = play(
        *left_a_server(), until_stopped, task_ended("b1", "stopped"), interactive=interactive
    )
    assert client.stopped == ["b1"]
    assert outcome.is_error


@pytest.mark.parametrize(
    "raw, seconds",
    [
        (None, session.SHELL_WAIT_DEFAULT_SECONDS),
        ("90", 90),
        ("1.5", 1.5),
        ("0", 0),
        ("ten minutes", session.SHELL_WAIT_DEFAULT_SECONDS),
        ("-1", session.SHELL_WAIT_DEFAULT_SECONDS),
        ("inf", session.SHELL_WAIT_DEFAULT_SECONDS),
        ("nan", session.SHELL_WAIT_DEFAULT_SECONDS),
    ],
)
def test_the_limit_comes_from_the_environment(monkeypatch, raw, seconds):
    if raw is None:
        monkeypatch.delenv(session.SHELL_WAIT_ENV, raising=False)
    else:
        monkeypatch.setenv(session.SHELL_WAIT_ENV, raw)
    assert session._shell_wait_seconds() == seconds


def test_the_run_stops_at_a_stage_that_left_a_command_running(run, script_sessions, short_limit, tmp_path):
    clients = script_sessions(*left_a_server(), until_stopped, task_ended("b1", "stopped"))
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

    assert len(clients) == 1, "the next stage must not start"
    assert [r.stage for r in results] == ["place"] and results[0].is_error
    assert "stage_waiting" in [e["event"] for e in events]
    finished = next(e for e in events if e["event"] == "stage_finished")
    assert finished["is_error"] is True and SERVER in finished["summary"]
    assert next(e for e in events if e["event"] == "run_complete")["failed_stage"] == "place"
