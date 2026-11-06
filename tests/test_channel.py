"""The control channel: prompts answered by id, events a watcher can stream."""

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest

from orchestra_engine import channel, interaction, main, ui


@pytest.fixture
def chan(tmp_path):
    ch = channel.open_channel(tmp_path / "chan", forbidden={})
    yield ch
    channel.close_channel()


def _events(ch):
    return channel.read_events(ch.dir)


def _answer_when_asked(ch, raw, prompt_id=None):
    """Answer the next prompt from another thread, as a driver process would."""
    def run():
        for _ in range(200):
            pending = channel.pending_prompt(_events(ch))
            if pending is not None:
                channel.answer(ch.dir, prompt_id or pending["id"], raw)
                return
            time.sleep(0.01)
    t = threading.Thread(target=run)
    t.start()
    return t


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(channel, "POLL_SECONDS", 0.01)


def test_gate_is_answered_over_the_channel(chan):
    t = _answer_when_asked(chan, "2")
    assert asyncio.run(interaction.stage_gate("plan")) == "skip"
    t.join()
    prompt = next(e for e in _events(chan) if e["event"] == "prompt")
    assert prompt["kind"] == channel.GATE
    assert [o["value"] for o in prompt["options"]] == ["run", "skip", "quit"]
    assert any(e["event"] == "answered" and e["id"] == prompt["id"] for e in _events(chan))


def test_option_can_be_named_by_value(chan):
    t = _answer_when_asked(chan, "quit")
    assert asyncio.run(interaction.stage_gate("plan")) == "quit"
    t.join()


def test_permission_prompt_is_tagged_for_the_human(chan):
    t = _answer_when_asked(chan, "deny")
    result = asyncio.run(interaction.can_use_tool("Bash", {"command": "rm -rf x"}, None))
    t.join()
    prompt = next(e for e in _events(chan) if e["event"] == "prompt")
    assert prompt["kind"] == channel.PERMISSION
    assert "rm -rf x" in prompt["message"]
    assert result.behavior == "deny"


def test_answer_for_the_wrong_prompt_is_refused(chan):
    async def go():
        task = asyncio.create_task(interaction.stage_gate("plan"))
        while channel.pending_prompt(_events(chan)) is None:
            await asyncio.sleep(0.01)
        with pytest.raises(channel.ChannelError, match="not the waiting prompt"):
            channel.answer(chan.dir, "p99", "1")
        channel.answer(chan.dir, "p1", "1")
        return await task
    assert asyncio.run(go()) == "run"


def test_out_of_range_answer_is_refused_at_the_command(chan):
    async def go():
        task = asyncio.create_task(interaction.stage_gate("plan"))
        while channel.pending_prompt(_events(chan)) is None:
            await asyncio.sleep(0.01)
        with pytest.raises(channel.ChannelError, match="not an option"):
            channel.answer(chan.dir, "p1", "7")
        channel.answer(chan.dir, "p1", "3")
        return await task
    assert asyncio.run(go()) == "quit"


def test_no_waiting_prompt_refuses(chan):
    with pytest.raises(channel.ChannelError, match="no prompt is waiting"):
        channel.answer(chan.dir, "p1", "1")


def test_bad_answer_file_is_rejected_and_prompt_stays_open(chan):
    async def go():
        task = asyncio.create_task(interaction.stage_gate("plan"))
        while channel.pending_prompt(_events(chan)) is None:
            await asyncio.sleep(0.01)
        # Written directly, around --answer's check: the engine still refuses it.
        (chan.answers / "p1").write_text("banana")
        while not any(e["event"] == "answer_rejected" for e in _events(chan)):
            await asyncio.sleep(0.01)
        assert channel.pending_prompt(_events(chan))["id"] == "p1"
        channel.answer(chan.dir, "p1", "1")
        return await task
    assert asyncio.run(go()) == "run"


def test_question_answers_and_checkbox_defaults(chan):
    q = {"questions": [
        {"question": "Name?", "header": "N", "options": [
            {"label": "alpha", "description": ""}, {"label": "beta", "description": ""}]},
        {"question": "Which?", "header": "W", "multiSelect": True, "options": [
            {"label": "x", "description": ""}, {"label": "y", "description": ""}]},
    ]}

    async def go():
        task = asyncio.create_task(interaction.handle_ask_user_question(q))
        for expected, raw in (("p1", "2"), ("p2", "1,2")):
            while (channel.pending_prompt(_events(chan)) or {}).get("id") != expected:
                await asyncio.sleep(0.01)
            channel.answer(chan.dir, expected, raw)
        return await asyncio.wait_for(task, 5)
    result = asyncio.run(go())
    assert result.updated_input["answers"] == {"Name?": "beta", "Which?": ["x", "y"]}


def test_back_token_only_where_back_is_allowed():
    prompt = {"mode": "text", "allow_back": False}
    assert channel.parse_answer(prompt, "<") == "<"
    prompt["allow_back"] = True
    assert channel.parse_answer(prompt, "<") is channel.STEP_BACK


def test_channel_inside_a_stage_writable_dir_is_refused(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(channel.ChannelError, match="overlaps --workspace"):
        channel.open_channel(ws / "chan", forbidden={"--workspace": ws})
    # The other way round too: a channel containing the workspace holds it.
    with pytest.raises(channel.ChannelError, match="overlaps --workspace"):
        channel.open_channel(tmp_path, forbidden={"--workspace": ws})
    assert channel.active() is None


def test_reused_channel_is_refused(tmp_path):
    channel.open_channel(tmp_path / "c", forbidden={})
    channel.close_channel()
    with pytest.raises(channel.ChannelError, match="fresh channel"):
        channel.open_channel(tmp_path / "c", forbidden={})


@pytest.mark.skipif(os.name == "nt", reason="Windows has ACLs, not POSIX mode bits")
def test_channel_dir_is_private(chan):
    assert chan.dir.stat().st_mode & 0o077 == 0


def test_watch_streams_and_stops_at_run_complete(chan):
    chan.emit("stage_started", stage="plan")
    chan.emit("escalation", text="dropped case A\nsee DESIGN.md")
    chan.emit("run_complete", run_dir="/r", escalations=True)
    chan.emit("stage_started", stage="never-shown")
    lines = list(channel.watch(chan.dir, poll=0.01))
    assert lines[0].startswith("ENGINE started")
    assert "STAGE plan started" in lines
    assert "  | dropped case A" in lines and "  | see DESIGN.md" in lines
    assert lines[-1].startswith("RUN complete: /r")
    assert not any("never-shown" in l for l in lines)


def test_watch_ends_when_the_engine_is_gone(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / channel.EVENTS_FILE).write_text('{"event": "engine_started", "pid": 999999999}\n')
    lines = list(channel.watch(d, poll=0.01))
    assert lines[-1].startswith("ENGINE gone")


def test_the_engines_own_pid_is_on_file_while_the_channel_is_open(tmp_path):
    """A launcher (orchestra.cmd, or the venv's python.exe) hands its caller its own
    PID, which is not the process running the stages."""
    ch = channel.open_channel(tmp_path / "c", forbidden={})
    pid_file = ch.dir / channel.PID_FILE
    assert pid_file.read_text(encoding="utf-8") == str(os.getpid())
    assert _events(ch)[0]["pid"] == os.getpid()
    channel.close_channel()
    assert not pid_file.exists(), "a stale PID could be some other process by now"


def _run_with_chatter(ch):
    ch.emit("run_started", run_dir="/r", pipeline="p", task="T")
    ch.emit("stage_started", stage="plan")
    ch.emit("session_started", stage="plan", label="plan", session_id="s1")
    ch.emit("message", type="text", text="thinking out loud", stage="plan", label="plan")
    ch.emit("stage_finished", stage="plan", is_error=False, duration_hms="1m 2s", num_turns=7, cost_usd=0.5)
    ch.emit("run_complete", run_dir="/r", escalations=False)


def test_watch_can_leave_out_the_message_stream(chan):
    _run_with_chatter(chan)
    full = list(channel.watch(chan.dir, poll=0.01))
    assert any(line.startswith("MESSAGE") for line in full)

    brief = list(channel.watch(chan.dir, poll=0.01, only_stages=True))
    assert not any(line.startswith(("MESSAGE", "SESSION_STARTED")) for line in brief)
    assert "STAGE plan started" in brief
    assert "STAGE plan finished [ok] in 1m 2s, 7 turns, $0.50" in brief
    assert brief[-1] == "RUN complete: /r"


def test_only_stages_keeps_everything_a_driver_acts_on(chan):
    chan.emit("prompt", id="p1", kind="permission", mode="select", message="Allow?", options=[], allow_back=False)
    chan.emit("escalation", text="dropped case A")
    chan.emit("stage_woken", stage="plan", label="plan", session_id="s1")
    chan.emit("stage_waiting", stage="plan", label="plan", commands=["npm run dev"], limit_seconds=600.0)
    chan.emit("run_complete", run_dir="/r", escalations=True)
    lines = list(channel.watch(chan.dir, poll=0.01, only_stages=True))
    assert any(line.startswith("PROMPT p1") for line in lines)
    assert any("permission prompt" in line for line in lines)
    assert "  | dropped case A" in lines
    assert any(line.startswith("STAGE plan WOKEN") for line in lines)
    assert any(line.startswith("STAGE plan WAITING") and "`npm run dev`" in line and "600 s" in line for line in lines)


def test_tail_starts_from_now(chan):
    chan.emit("stage_started", stage="plan")
    chan.emit("message", type="text", text="old news", stage="plan", label="plan")
    chan.emit("stage_finished", stage="plan", is_error=False)
    chan.emit("stage_started", stage="build")

    watcher = channel.watch(chan.dir, poll=0.01, tail=True)
    standing = [next(watcher), next(watcher)]
    assert standing == ["WATCH re-armed: skipped 5 earlier event(s)", "STAGE build is running"]

    chan.emit("stage_finished", stage="build", is_error=False)
    chan.emit("run_complete", run_dir="/r", escalations=False)
    assert list(watcher) == ["STAGE build finished [ok]", "RUN complete: /r"]


def test_tail_still_shows_a_prompt_that_is_waiting(chan):
    """Nothing repeats it, and the run is stopped until someone answers."""
    chan.emit("stage_started", stage="plan")
    chan.emit("prompt", id="p1", kind="question", mode="text", message="Which one?", options=None, allow_back=False)
    chan.emit("run_complete", run_dir="/r", escalations=False)  # lets the watch end
    lines = list(channel.watch(chan.dir, poll=0.01, tail=True))
    assert any(line.startswith("PROMPT p1 (question): Which one?") for line in lines)
    assert lines[-1] == "RUN complete: /r"


def test_tail_does_not_show_a_prompt_already_answered(chan):
    chan.emit("prompt", id="p1", kind="question", mode="text", message="Which one?", options=None, allow_back=False)
    chan.emit("answered", id="p1", answer="that one")
    chan.emit("run_complete", run_dir="/r", escalations=False)
    lines = list(channel.watch(chan.dir, poll=0.01, tail=True))
    assert not any("Which one?" in line for line in lines)


def test_tail_on_a_finished_run_says_how_it_ended_and_stops(chan):
    chan.emit("run_complete", run_dir="/r", escalations=False, failed_stage="plan")
    lines = list(channel.watch(chan.dir, poll=0.01, tail=True))
    assert lines[-1] == "RUN stopped, stage plan failed: /r"


def test_tail_armed_before_the_run_sees_all_of_it(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    launch = threading.Timer(
        0.05,
        (d / channel.EVENTS_FILE).write_text,
        args=['{"event": "stage_started", "stage": "plan"}\n{"event": "run_complete", "run_dir": "/r"}\n'],
        kwargs={"encoding": "utf-8"},
    )
    launch.start()
    lines = list(channel.watch(d, poll=0.01, tail=True))
    launch.join()
    assert lines == ["STAGE plan started", "RUN complete: /r"]


def test_tail_notices_an_engine_already_gone(tmp_path):
    """The PID comes from the events it skipped, or a crashed run would hold the
    watcher open for good."""
    d = tmp_path / "c"
    d.mkdir()
    (d / channel.EVENTS_FILE).write_text('{"event": "engine_started", "pid": 999999999}\n', encoding="utf-8")
    lines = list(channel.watch(d, poll=0.01, tail=True))
    assert lines[-1].startswith("ENGINE gone")


def test_a_finished_stage_shows_its_summary_quoted_on_one_line():
    """The summary is agent-written, so it can't get a line of its own that looks
    like the engine's."""
    lines = channel.render(
        {"event": "stage_finished", "stage": "plan", "is_error": True, "summary": "It broke.\nRUN complete: /fake"},
        Path("."),
    )
    assert lines == ["STAGE plan finished [ERROR]", "  | It broke. RUN complete: /fake"]


def test_a_long_summary_is_cut_for_the_watch_line_only():
    lines = channel.render({"event": "stage_finished", "stage": "plan", "summary": "x" * 5000}, Path("."))
    assert len(lines[1]) < 400


def test_an_event_from_before_these_fields_still_renders():
    assert channel.render({"event": "stage_finished", "stage": "plan"}, Path(".")) == ["STAGE plan finished [ok]"]


@pytest.mark.parametrize("flag", ["--only-stages", "--tail"])
def test_watch_modifiers_without_watch_are_refused(monkeypatch, flag):
    monkeypatch.setattr("sys.argv", ["orchestra-engine", flag, "--channel", "x"])
    with pytest.raises(SystemExit, match="only apply to --watch"):
        main.main()


def test_watch_flags_reach_the_watcher(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_watch(directory, **kwargs):
        seen.update(kwargs)
        return iter(["a line"])

    monkeypatch.setattr(channel, "watch", fake_watch)
    monkeypatch.setattr(
        "sys.argv", ["orchestra-engine", "--watch", "--channel", str(tmp_path), "--only-stages", "--tail"]
    )
    main.main()
    assert seen == {"only_stages": True, "tail": True}
    assert capsys.readouterr().out == "a line\n"


def test_no_channel_means_ui_is_untouched(monkeypatch):
    assert channel.active() is None
    channel.emit("anything")  # a no-op, not an error
    monkeypatch.setattr(ui, "_tty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    assert asyncio.run(ui.select("pick", [("a", "a"), ("b", "b")])) == "a"
