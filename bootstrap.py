#!/usr/bin/env python3
"""One-command setup, identical on every OS: `python bootstrap.py`.

Creates the repo's .venv with the interpreter you ran this with, installs the
engine into it (editable), and tells you the launcher for your platform. No
shell, no activation, no remembering whether pip lives in bin/ or Scripts/ —
that difference is exactly what this script exists to paper over.

Stdlib only, so it runs before anything is installed. Re-running is safe: it
reuses an existing .venv and just reinstalls.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MIN_PYTHON = (3, 10)  # keep in sync with pyproject.toml requires-python


def venv_python(venv: Path) -> Path:
    """Where the venv keeps its interpreter — the one cross-OS fork that matters.

    Windows puts it in Scripts\\python.exe; every POSIX venv uses bin/python."""
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def run(cmd: list[str]) -> None:
    """Run a command as an argument list (never a shell string) and stop on failure."""
    printable = " ".join(cmd)
    print(f"\n$ {printable}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"\nSetup failed at: {printable}")


def main() -> None:
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        have = ".".join(map(str, sys.version_info[:3]))
        sys.exit(
            f"Python {need}+ is required; you ran this with {have}. "
            f"Install a newer Python and run: python bootstrap.py"
        )

    # Line-buffered, so this script's headers land in order with the output of
    # the commands it runs, even when piped to a log.
    sys.stdout.reconfigure(line_buffering=True)

    py = venv_python(VENV)
    if py.exists():
        print(f"Reusing existing virtualenv at {VENV}")
    else:
        print(f"Creating virtualenv at {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])

    # Upgrade pip best-effort — a stale pip can choke on editable installs,
    # but a machine that can't reach an index shouldn't fail setup over it.
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    # The actual install. An absolute path so pyproject.toml resolves here
    # regardless of where you invoked bootstrap from. The dev extra is only
    # pytest, and with it the repo's own `.venv/bin/pytest` works straight away.
    run([str(py), "-m", "pip", "install", "-e", f"{ROOT}[dev]"])

    launcher = "orchestra" if sys.platform == "win32" else "./orchestra"
    print(
        "\n===== engine installed =====\n"
        "Auth comes from your existing Claude Code login or ANTHROPIC_API_KEY.\n\n"
        "One more thing, once per machine: teach your coding agent how to drive\n"
        "this engine. That writes one <orchestra-engine> block to ~/.claude/CLAUDE.md\n"
        "and leaves the rest of that file alone."
    )
    # Ask rather than just do it: it writes outside this repo. With no terminal
    # (CI, a script) nobody can answer, so print the command instead.
    if sys.stdin.isatty():
        answer = input("Install the agent guidance now? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            subprocess.run([str(py), "-m", "orchestra_engine", "--install-guidance"])
        else:
            print(f"Skipped. Run it later with: {launcher} --install-guidance")
    else:
        print(f"Run it with: {launcher} --install-guidance")

    # End on a verdict, not a wall of pip output: is this machine ready?
    print("\n===== checking this machine =====")
    ready = subprocess.run([str(py), "-m", "orchestra_engine", "--doctor"]).returncode == 0
    print(
        f"\nRun the orchestra from this folder with:\n"
        f"  {launcher}                 # setup wizard\n"
        f'  {launcher} "your task" -y  # scripted, autonomous\n'
        f"  {launcher} --doctor        # re-check setup any time"
    )
    if not ready:
        sys.exit(1)


if __name__ == "__main__":
    main()
