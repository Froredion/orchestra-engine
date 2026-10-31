"""`--doctor`: is this machine ready to run a pipeline?

A fresh checkout can install cleanly and still fail on its first real run —
no Claude login, guidance never installed, a pipeline that doesn't parse — and
that failure lands minutes in, inside a stage. This checks each of those up
front, runs no agents and spends nothing, and prints the one command that fixes
whatever it finds. bootstrap.py runs it last, so setup ends on a verdict.

Required checks fail the exit code; recommended ones only warn, since the
engine runs without them.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import guidance
from .pipeline import available_pipelines, load_pipeline

MIN_PYTHON = (3, 10)  # keep in sync with pyproject.toml requires-python


@dataclasses.dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def _launcher() -> str:
    return "orchestra" if sys.platform == "win32" else "./orchestra"


def check_python() -> Check:
    have = ".".join(map(str, sys.version_info[:3]))
    need = ".".join(map(str, MIN_PYTHON))
    if sys.version_info >= MIN_PYTHON:
        return Check("python", True, have)
    return Check("python", False, f"{have}, need {need}+",
                 f"install Python {need}+, then: python bootstrap.py")


def check_dependencies() -> list[Check]:
    """The installed dependencies, by import name. pywebview only gives the
    live view its own window — the browser is the fallback — so it only warns."""
    checks = []
    for module in ("claude_agent_sdk", "questionary"):
        found = importlib.util.find_spec(module) is not None
        checks.append(Check(module, found, "installed" if found else "missing",
                            "python bootstrap.py"))
    if sys.platform in ("darwin", "win32"):
        found = importlib.util.find_spec("webview") is not None
        checks.append(Check("pywebview", found,
                            "installed" if found else "missing; the live view opens in the browser",
                            "python bootstrap.py", required=False))
    return checks


def _claude_cli() -> str | None:
    """The CLI the SDK will actually run: its bundled copy first, then PATH."""
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec and spec.origin:
        name = "claude.exe" if sys.platform == "win32" else "claude"
        bundled = Path(spec.origin).parent / "_bundled" / name
        if bundled.is_file():
            return str(bundled)
    return shutil.which("claude")


def check_auth() -> Check:
    """Asks the CLI whether it's logged in; makes no API call."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return Check("claude auth", True, "ANTHROPIC_API_KEY is set")
    fix = "run `claude` and log in, or set ANTHROPIC_API_KEY"
    cli = _claude_cli()
    if cli is None:
        return Check("claude auth", False, "no Claude Code CLI found",
                     "python bootstrap.py (the SDK bundles one), or install Claude Code")
    try:
        result = subprocess.run([cli, "auth", "status", "--json"],
                                capture_output=True, text=True, timeout=30)
        status = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return Check("claude auth", False, f"couldn't ask the CLI: {exc}", fix)
    if status.get("loggedIn"):
        return Check("claude auth", True, f"logged in ({status.get('authMethod', 'unknown')})")
    return Check("claude auth", False, "not logged in", fix)


def check_guidance(target: Path | None = None) -> Check:
    """Whether an agent on this machine knows how to drive the engine."""
    target = target or guidance.default_target()
    fix = f"{_launcher()} --install-guidance"
    try:
        status = guidance.install(target, dry_run=True)
    except OSError as exc:
        return Check("agent guidance", False, f"couldn't read {target}: {exc}", fix, required=False)
    if status == "unchanged":
        return Check("agent guidance", True, f"current in {target}", required=False)
    detail = "out of date" if status == "updated" else "not installed"
    return Check("agent guidance", False, f"{detail} in {target}", fix, required=False)


def check_pipelines() -> Check:
    names = available_pipelines()
    broken = []
    for name in names:
        try:
            load_pipeline(name)
        except ValueError as exc:
            broken.append(f"{name}: {exc}")
    if broken:
        return Check("pipelines", False, "; ".join(broken),
                     "fix the pipeline named above, or drop its overlay from ORCHESTRA_PIPELINES_PATH")
    return Check("pipelines", True, f"{len(names)} load")


def run_checks(guidance_target: Path | None = None) -> list[Check]:
    return [check_python(), *check_dependencies(), check_auth(),
            check_guidance(guidance_target), check_pipelines()]


def report(checks: list[Check]) -> bool:
    """Print one line per check; True when nothing required failed."""
    width = max(len(c.name) for c in checks)
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        print(f"  {mark}  {c.name:<{width}}  {c.detail}")
        if not c.ok and c.fix:
            print(f"        {'':<{width}}  fix: {c.fix}")
    ready = all(c.ok or not c.required for c in checks)
    print("\nReady to run a pipeline." if ready else "\nNot ready yet: fix the FAIL lines above.")
    return ready
