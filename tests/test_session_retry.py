"""Retry behavior at the SDK boundary: a dropped connection shouldn't cost a run."""

import pytest

import orchestra_engine.engine.session as session


@pytest.fixture
def no_sleep(monkeypatch):
    """Backoff without the wall-clock cost."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(session.asyncio, "sleep", fake_sleep)
    return slept


def make_flaky_session(failures, exc=None):
    """A _run_session stand-in that raises `failures` times, then succeeds."""
    state = {"attempts": 0}
    exc = exc or Exception("Claude Code returned an error result: success")

    async def fake(stage_name, prompt, workspace, artifacts, **kwargs):
        state["attempts"] += 1
        if state["attempts"] <= failures:
            raise exc
        return session.StageResult(
            stage=stage_name, session_id="sess-1", summary="done", is_error=False
        )

    return fake, state


def test_transient_error_is_retried_and_recovers(run, monkeypatch, no_sleep, tmp_path):
    fake, state = make_flaky_session(failures=2)
    monkeypatch.setattr(session, "_run_session", fake)
    logs = []

    result = run(session.run_stage("localize", "p", tmp_path, tmp_path / "artifacts", log=logs.append))

    assert state["attempts"] == 3, "should retry twice, then succeed"
    assert result.summary == "done"
    assert not result.is_error
    assert no_sleep == [2, 4], "backoff should grow between attempts"
    assert any("retrying in 2s" in line for line in logs), "retries must be visible, not silent"


def test_gives_up_after_max_attempts_and_reraises(run, monkeypatch, no_sleep, tmp_path):
    fake, state = make_flaky_session(failures=99)
    monkeypatch.setattr(session, "_run_session", fake)
    logs = []

    with pytest.raises(Exception, match="error result"):
        run(session.run_stage("localize", "p", tmp_path, tmp_path / "artifacts", log=logs.append))

    assert state["attempts"] == session.MAX_ATTEMPTS
    assert any("session failed" in line for line in logs)


def test_permanent_error_is_not_retried(run, monkeypatch, no_sleep, tmp_path):
    """Retrying a 1MB-buffer overflow just re-runs an expensive stage into the
    same wall — the whole point of naming permanent errors."""
    boom = Exception("Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes")
    fake, state = make_flaky_session(failures=99, exc=boom)
    monkeypatch.setattr(session, "_run_session", fake)

    with pytest.raises(Exception, match="buffer size"):
        run(session.run_stage("deep-dive", "p", tmp_path, tmp_path / "artifacts", log=lambda _: None))

    assert state["attempts"] == 1, "permanent errors should fail on the first attempt"
    assert no_sleep == []


def test_turn_cap_exhaustion_is_not_retried(run, monkeypatch, no_sleep, tmp_path):
    """A stage that ran out of turns walks into the same cap on the next
    attempt, and the one after — three full-price runs to fail identically.
    The cap is the problem, so the fix is a bigger --max-turns, not a retry."""
    boom = Exception("Claude Code returned an error result: Reached maximum number of turns (12)")
    fake, state = make_flaky_session(failures=99, exc=boom)
    monkeypatch.setattr(session, "_run_session", fake)

    with pytest.raises(Exception, match="maximum number of turns"):
        run(session.run_stage("build", "p", tmp_path, tmp_path / "artifacts", log=lambda _: None))

    assert state["attempts"] == 1, "a retry would just hit the same cap"
    assert no_sleep == []


def test_agent_error_result_is_not_retried(run, monkeypatch, no_sleep, tmp_path):
    """An is_error result is the agent failing semantically, not a transport
    fault — retrying it would just re-run a stage that legitimately failed."""
    state = {"attempts": 0}

    async def fake(stage_name, prompt, workspace, artifacts, **kwargs):
        state["attempts"] += 1
        return session.StageResult(
            stage=stage_name, session_id="s", summary="agent gave up", is_error=True
        )

    monkeypatch.setattr(session, "_run_session", fake)
    result = run(session.run_stage("fix", "p", tmp_path, tmp_path / "artifacts", log=lambda _: None))

    assert state["attempts"] == 1
    assert result.is_error


def test_retry_rebuilds_the_prompt_each_attempt(run, monkeypatch, no_sleep, tmp_path):
    """The interactive path wraps the prompt in a single-use async generator;
    a retry that reused it would send an exhausted stream."""
    seen = []
    state = {"attempts": 0}

    async def fake(stage_name, prompt, workspace, artifacts, **kwargs):
        state["attempts"] += 1
        seen.append(prompt)
        if state["attempts"] == 1:
            raise Exception("Claude Code returned an error result: success")
        return session.StageResult(
            stage=stage_name, session_id="s", summary="ok", is_error=False
        )

    monkeypatch.setattr(session, "_run_session", fake)
    run(session.run_stage("plan", "the prompt", tmp_path, tmp_path / "artifacts", log=lambda _: None))

    assert seen == ["the prompt", "the prompt"], "each attempt gets the prompt fresh"
