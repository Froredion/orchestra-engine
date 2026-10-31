"""The control channel: prompts answered by id, events a watcher can stream."""

import asyncio
import threading
import time

import pytest

from orchestra_engine import channel, interaction, ui


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


def test_no_channel_means_ui_is_untouched(monkeypatch):
    assert channel.active() is None
    channel.emit("anything")  # a no-op, not an error
    monkeypatch.setattr(ui, "_tty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    assert asyncio.run(ui.select("pick", [("a", "a"), ("b", "b")])) == "a"
