"""bootstrap.py with nobody at the terminal: it must still reach its own verdict."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import bootstrap

ROOT = Path(bootstrap.__file__).resolve().parent


class FakeStdin:
    """A stdin claiming to be a terminal, as Windows' null device does."""

    def __init__(self, line="", tty=True):
        self.line = line
        self.tty = tty

    def isatty(self):
        return self.tty

    def readline(self):
        return self.line


def test_a_terminal_gets_asked(monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin("y\n"))
    assert bootstrap.ask("q? ") == "y"


def test_a_stdin_that_says_it_is_a_terminal_and_then_ends_is_nobody(monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin(""))
    assert bootstrap.ask("q? ") is None


def test_a_pipe_is_nobody(monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin("y\n", tty=False))
    assert bootstrap.ask("q? ") is None


def test_no_stdin_at_all_is_nobody(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    assert bootstrap.ask("q? ") is None


def test_the_real_null_device_is_nobody():
    """Not a stand-in: on Windows this is the stdin that crashed setup, since
    isatty() is true for it and input() then raises EOFError."""
    done = subprocess.run(
        [sys.executable, "-c", "import bootstrap; print(repr(bootstrap.ask('q? ')))"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("None")


@pytest.mark.parametrize("stdin", [FakeStdin(""), FakeStdin("y\n", tty=False), None])
def test_setup_still_runs_its_doctor_check_with_nobody_there(monkeypatch, tmp_path, capsys, stdin):
    """The install had finished when the crash came, so the machine was fine; what
    was lost is the check that says so, and the guidance must not be installed
    without anyone agreeing to it."""
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap, "VENV", tmp_path / ".venv")
    monkeypatch.setattr(bootstrap, "run", lambda cmd: commands.append(cmd))
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "stdin", stdin)

    bootstrap.main()

    assert any("--doctor" in cmd for cmd in commands)
    assert not any("--install-guidance" in cmd for cmd in commands)
    assert "--install-guidance" in capsys.readouterr().out, "still says how to do it later"


@pytest.mark.parametrize("line, installs", [("\n", True), ("y\n", True), ("n\n", False)])
def test_someone_at_the_terminal_still_decides(monkeypatch, tmp_path, line, installs):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap, "VENV", tmp_path / ".venv")
    monkeypatch.setattr(bootstrap, "run", lambda cmd: commands.append(cmd))
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "stdin", FakeStdin(line))

    bootstrap.main()

    assert any("--install-guidance" in cmd for cmd in commands) is installs
    assert any("--doctor" in cmd for cmd in commands)
