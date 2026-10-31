"""bypassPermissions auto-approves every tool call, so it can't be reached by
a single typed flag: it has to be confirmed, or pre-confirmed on purpose."""

import argparse

import pytest

from orchestra_engine import ui
from orchestra_engine.main import confirm_bypass, resolve_permission_mode


def args(**kwargs):
    base = {"permission_mode": None, "dangerously_skip_permissions": False}
    return argparse.Namespace(**{**base, **kwargs})


# --- resolution ----------------------------------------------------------


def test_default_is_auto():
    assert resolve_permission_mode(args()) == "auto"


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", "dontAsk", "auto"])
def test_explicit_modes_pass_through(mode):
    assert resolve_permission_mode(args(permission_mode=mode)) == mode


def test_bypass_flag_selects_bypass():
    assert resolve_permission_mode(args(dangerously_skip_permissions=True)) == "bypassPermissions"


def test_bypass_flag_agrees_with_explicit_bypass():
    resolved = resolve_permission_mode(
        args(permission_mode="bypassPermissions", dangerously_skip_permissions=True)
    )
    assert resolved == "bypassPermissions"


def test_bypass_flag_conflicting_with_another_mode_exits():
    """--dangerously-skip-permissions only confirms bypass; it must never
    silently override a mode the human explicitly asked for."""
    with pytest.raises(SystemExit) as exc:
        resolve_permission_mode(args(permission_mode="auto", dangerously_skip_permissions=True))
    assert "conflicts" in str(exc.value)


# --- the confirmation gate -----------------------------------------------


@pytest.fixture
def answer(monkeypatch):
    """Stub ui.select; returns the list of prompts it was asked."""

    def _answer(reply):
        asked = []

        async def fake_select(message, choices, allow_back=False, kind=None):
            asked.append((message, choices))
            if isinstance(reply, Exception):
                raise reply
            return reply

        monkeypatch.setattr(ui, "select", fake_select)
        return asked

    return _answer


def test_yes_proceeds(run, answer, tmp_path):
    answer("yes")
    run(confirm_bypass(tmp_path))  # returns without exiting


def test_no_cancels(run, answer, tmp_path):
    answer("no")
    with pytest.raises(SystemExit) as exc:
        run(confirm_bypass(tmp_path))
    assert "cancelled" in str(exc.value)


def test_cancel_is_the_first_choice(run, answer, tmp_path):
    """questionary highlights the first choice, so a stray Enter must cancel
    rather than hand over unreviewed access."""
    asked = answer("no")
    with pytest.raises(SystemExit):
        run(confirm_bypass(tmp_path))
    _, choices = asked[0]
    assert choices[0][1] == "no"


def test_no_input_fails_closed(run, answer, tmp_path):
    """Nothing to ask on (stdin closed) must never mean 'proceed unreviewed'."""
    answer(EOFError())
    with pytest.raises(SystemExit) as exc:
        run(confirm_bypass(tmp_path))
    assert "--dangerously-skip-permissions" in str(exc.value)


def test_warning_names_the_workspace(run, answer, tmp_path, capsys):
    """The blast radius is the workspace, so the warning has to show which one."""
    answer("yes")
    run(confirm_bypass(tmp_path))
    assert str(tmp_path) in capsys.readouterr().out
