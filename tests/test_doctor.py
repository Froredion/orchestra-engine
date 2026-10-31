"""--doctor: each check reports the gap and its fix, and only required ones fail."""

import subprocess

from orchestra_engine import doctor, guidance


def test_guidance_missing_warns_with_the_install_command(tmp_path):
    check = doctor.check_guidance(tmp_path / "CLAUDE.md")

    assert not check.ok and not check.required
    assert "not installed" in check.detail
    assert "--install-guidance" in check.fix


def test_guidance_current_passes(tmp_path):
    target = tmp_path / "CLAUDE.md"
    guidance.install(target)

    assert doctor.check_guidance(target).ok


def test_guidance_stale_says_out_of_date(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("<orchestra-engine>\n\nold\n\n</orchestra-engine>\n")

    check = doctor.check_guidance(target)

    assert not check.ok and "out of date" in check.detail
    # Checking never writes.
    assert "old" in target.read_text()


def test_api_key_counts_as_auth(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    assert doctor.check_auth().ok


def test_logged_out_cli_fails_auth(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(doctor, "_claude_cli", lambda: "claude")
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, stdout='{"loggedIn": false, "authMethod": "none"}', stderr=""))

    check = doctor.check_auth()

    assert not check.ok and check.required
    assert "not logged in" in check.detail


def test_builtin_pipelines_all_load():
    assert doctor.check_pipelines().ok


def test_only_required_failures_block_ready(capsys):
    warn = doctor.Check("guidance", False, "missing", "fix it", required=False)
    fail = doctor.Check("auth", False, "missing", "log in")

    assert doctor.report([doctor.Check("python", True, "3.12"), warn])
    assert not doctor.report([warn, fail])
    assert "FAIL" in capsys.readouterr().out
