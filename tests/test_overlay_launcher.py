"""overlay-template/orchestra.sh — the launcher a copied overlay repo runs on.

The engine is never actually started here. Every test builds a *stub* engine —
a directory that satisfies the launcher's validation and whose ./orchestra just prints
what it was handed — so these assert the launcher's contract (which engine it
picked, what $ORCHESTRA_PIPELINES_PATH became, which argv survived) without a venv,
an API key, or a second of agent time.

The stub is the point of the exercise: the launcher's whole job is to resolve an
engine and hand over cleanly, and a stub can say exactly what arrived.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "overlay-template"
LAUNCHER = TEMPLATE / "orchestra.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="orchestra.sh is the POSIX launcher; cmd.exe has no sh"
)


def make_engine(path: Path, with_venv: bool = True) -> Path:
    """A directory the launcher accepts as the engine.

    Both markers are required by design — the launcher refuses a candidate with
    only ./orchestra, because a sibling *overlay* checkout has one too.
    """
    (path / "orchestra_engine").mkdir(parents=True)
    (path / "orchestra_engine" / "pipeline.py").write_text("# stub\n")
    launcher = path / "orchestra"
    # Reports the two things the launcher is responsible for setting up.
    launcher.write_text(
        '#!/bin/sh\necho "ENGINE=$(cd "$(dirname "$0")" && pwd)"\n'
        'echo "PIPELINES_PATH=$ORCHESTRA_PIPELINES_PATH"\n'
        'echo "ARGS=$*"\n'
    )
    launcher.chmod(0o755)
    if with_venv:
        (path / ".venv" / "bin").mkdir(parents=True)
        py = path / ".venv" / "bin" / "python"
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
    return path


def make_overlay(path: Path, pipeline: str | None = "demo") -> Path:
    """A pipelines-only repo with orchestra.sh copied in, exactly as a user would."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "orchestra.sh").write_bytes(LAUNCHER.read_bytes())
    (path / "orchestra.sh").chmod(0o755)
    if pipeline:
        stage_dir = path / "pipelines" / pipeline
        stage_dir.mkdir(parents=True)
        (stage_dir / "01-plan.md").write_text("Plan {task}.\n")
    return path


def launch(overlay: Path, *args: str, env: dict | None = None, cwd: Path | None = None):
    """Run the overlay's orchestra.sh with a deliberately clean environment.

    The engine vars are cleared rather than inherited so a developer's own
    ORCHESTRA_ENGINE_DIR can't make a discovery test pass for the wrong reason.
    """
    base = {k: v for k, v in os.environ.items() if not k.startswith(("LOOP_", "ORCHESTRA_"))}
    # An empty PATH would break `command -v`; keep it minimal but real.
    base["PATH"] = "/usr/bin:/bin"
    return subprocess.run(
        [str(overlay / "orchestra.sh"), *args],
        capture_output=True,
        text=True,
        env={**base, **(env or {})},
        cwd=str(cwd or overlay),
    )


def test_exposes_own_pipelines_and_passes_args_through(tmp_path):
    """The core contract: our pipelines/ reaches the engine, argv is untouched."""
    engine = make_engine(tmp_path / "loop-engineering")
    overlay = make_overlay(tmp_path / "my-pipelines")

    result = launch(overlay, "a task", "--pipeline", "demo")

    assert result.returncode == 0, result.stderr
    assert f"ENGINE={engine}" in result.stdout
    assert f"PIPELINES_PATH={overlay / 'pipelines'}" in result.stdout
    assert "ARGS=a task --pipeline demo" in result.stdout


def test_leads_an_existing_pipelines_path(tmp_path):
    """Ours goes first so a bare --pipeline <name> resolves here before elsewhere,
    but an already-configured overlay is preserved rather than replaced."""
    make_engine(tmp_path / "orchestra-engine")
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay, env={"ORCHESTRA_PIPELINES_PATH": "/somewhere/else"})

    assert f"PIPELINES_PATH={overlay / 'pipelines'}:/somewhere/else" in result.stdout


def test_explicit_override_beats_a_sibling(tmp_path):
    """An operator's explicit choice wins over anything discovery would find."""
    make_engine(tmp_path / "loop-engineering")  # the sibling that would be picked
    chosen = make_engine(tmp_path / "elsewhere" / "engine")
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay, env={"ORCHESTRA_ENGINE_DIR": str(chosen)})

    assert f"ENGINE={chosen}" in result.stdout


def test_legacy_override_name_still_honored(tmp_path):
    """The existing overlay in the wild sets LOOP_ENGINEERING_DIR; keep it working."""
    chosen = make_engine(tmp_path / "elsewhere" / "engine")
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay, env={"LOOP_ENGINEERING_DIR": str(chosen)})

    assert f"ENGINE={chosen}" in result.stdout


def test_bad_override_fails_loudly_instead_of_falling_back(tmp_path):
    """A wrong override must be reported, not silently masked by a sibling that
    happens to validate — otherwise the operator debugs the wrong engine."""
    make_engine(tmp_path / "loop-engineering")
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay, env={"ORCHESTRA_ENGINE_DIR": str(tmp_path / "nope")})

    assert result.returncode == 1
    assert "not an Orchestra Engine checkout" in result.stderr


def test_finds_a_sibling_under_an_unknown_name(tmp_path):
    """Neither repo's directory name is assumed: a clone under a third name is
    found by validating candidates rather than matching names."""
    engine = make_engine(tmp_path / "some-other-name")
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay)

    assert f"ENGINE={engine}" in result.stdout


def test_another_overlay_sibling_is_not_mistaken_for_the_engine(tmp_path):
    """The reason validation needs both markers. A sibling overlay also has an
    executable ./orchestra, so a launcher testing only for that would resolve to it
    and fail later, deep inside the handoff, far from the cause."""
    decoy = make_overlay(tmp_path / "other-overlay")
    (decoy / "orchestra").write_text("#!/bin/sh\necho wrong\n")
    (decoy / "orchestra").chmod(0o755)
    engine = make_engine(tmp_path / "zzz-real-engine")  # sorts after the decoy
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay)

    assert f"ENGINE={engine}" in result.stdout


def test_resolves_from_its_own_location_not_the_caller_cwd(tmp_path):
    """Invoked by absolute path from anywhere, it must still find its own
    pipelines/ — the paths come from $0, never $PWD."""
    make_engine(tmp_path / "loop-engineering")
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay, cwd=tmp_path.parent)

    assert f"PIPELINES_PATH={overlay / 'pipelines'}" in result.stdout


def test_missing_pipelines_dir_explains_the_expected_structure(tmp_path):
    """The mistake a new overlay actually makes. Caught before engine discovery,
    with the layout spelled out, rather than running with an empty overlay."""
    make_engine(tmp_path / "loop-engineering")
    overlay = make_overlay(tmp_path / "mine", pipeline=None)

    result = launch(overlay)

    assert result.returncode == 1
    assert "No pipelines/ directory" in result.stderr
    assert "01-plan.md" in result.stderr


def test_empty_pipelines_dir_warns_but_still_runs(tmp_path):
    """Mid-authoring is a legitimate state: --list and the built-ins still work,
    so this warns instead of blocking."""
    make_engine(tmp_path / "loop-engineering")
    overlay = make_overlay(tmp_path / "mine", pipeline=None)
    (overlay / "pipelines").mkdir()

    result = launch(overlay, "--list")

    assert result.returncode == 0
    assert "no pipelines found" in result.stderr


def test_group_folder_counts_as_pipelines(tmp_path):
    """A folder holding pipelines rather than stage files is a group. The
    structure check looks one level in, matching the engine's own discovery, so a
    grouped-only overlay isn't wrongly reported as empty."""
    make_engine(tmp_path / "loop-engineering")
    overlay = make_overlay(tmp_path / "mine", pipeline=None)
    nested = overlay / "pipelines" / "family" / "nested"
    nested.mkdir(parents=True)
    (nested / "01-go.md").write_text("go\n")

    result = launch(overlay, "--list")

    assert result.returncode == 0
    assert "no pipelines found" not in result.stderr


def test_refuses_to_run_inside_the_engine_checkout(tmp_path):
    """Copied into the engine repo by mistake, it should name the right launcher
    instead of reporting a missing pipelines/ directory the engine never has."""
    engine = make_engine(tmp_path / "loop-engineering")
    (engine / "orchestra.sh").write_bytes(LAUNCHER.read_bytes())
    (engine / "orchestra.sh").chmod(0o755)

    result = launch(engine, "--list")

    assert result.returncode == 1
    assert "checkout itself" in result.stderr
    assert "./orchestra --list" in result.stderr


def test_engine_without_a_venv_says_where_to_bootstrap(tmp_path):
    """The engine's own message names `python bootstrap.py` without saying which
    directory to run it in — from an overlay that's the one thing you need."""
    engine = make_engine(tmp_path / "loop-engineering", with_venv=False)
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay)

    assert result.returncode == 1
    assert "no virtualenv yet" in result.stderr
    assert str(engine) in result.stderr


def test_finds_the_engine_it_is_nested_inside(tmp_path):
    """overlay-template/ lives inside the engine repo, so the directory holding it
    is the engine. Resolving that lets the template be smoke-tested in place —
    without it, the shipped template is the one overlay that can't run."""
    engine = make_engine(tmp_path / "loop-engineering")
    nested = make_overlay(engine / "overlay-template")

    result = launch(nested, "--list")

    assert result.returncode == 0, result.stderr
    assert f"ENGINE={engine}" in result.stdout


# --------------------------------------------------------------------------
# The template folder itself. These are the "copy-paste a folder" guarantee:
# whatever else is true, that folder must already be a working overlay repo.
# --------------------------------------------------------------------------


def test_template_ships_everything_a_new_repo_needs(tmp_path):
    """Each of these files is why the unit of copying is a folder and not just
    orchestra.sh: the new repo needs run output ignored and orchestra.sh pinned to LF."""
    assert LAUNCHER.is_file(), "the template has no launcher"
    assert os.access(LAUNCHER, os.X_OK), "orchestra.sh must be committed executable"
    for name in (".gitignore", ".gitattributes", "README.md"):
        assert (TEMPLATE / name).is_file(), f"template is missing {name}"

    # CRLF would break the #! line on the machine it's copied to.
    assert b"\r\n" not in LAUNCHER.read_bytes()
    assert "*.sh text eol=lf" in (TEMPLATE / ".gitattributes").read_text()
    assert "orchestra-docs/" in (TEMPLATE / ".gitignore").read_text()


def test_template_example_pipeline_parses(monkeypatch):
    """The example is the thing people copy to write their own, so it has to load
    through the engine's real parser — not just look plausible."""
    monkeypatch.setenv("ORCHESTRA_PIPELINES_PATH", str(TEMPLATE / "pipelines"))
    from orchestra_engine.pipeline import load_pipeline, resolve_pipeline_dir

    pipeline = load_pipeline("example")

    assert [stage.name for stage in pipeline.stages] == ["plan", "build"]
    assert resolve_pipeline_dir("example").is_relative_to(TEMPLATE)
    # The placeholders the stages advertise must actually reach the prompt.
    prompt = pipeline.build_prompt(
        pipeline.stages[0], "fix the parser", handoffs=[], artifacts="/tmp/a"
    )
    assert "fix the parser" in prompt


def test_copying_the_template_folder_yields_a_runnable_repo(tmp_path):
    """The documented recipe, executed: copy the folder, and it runs. No edits,
    no install step, nothing to wire up."""
    engine = make_engine(tmp_path / "loop-engineering")
    overlay = tmp_path / "my-pipelines"
    shutil.copytree(TEMPLATE, overlay)

    result = launch(overlay, "--list", "--pipeline", "example")

    assert result.returncode == 0, result.stderr
    assert f"ENGINE={engine}" in result.stdout
    assert f"PIPELINES_PATH={overlay / 'pipelines'}" in result.stdout


def test_no_engine_anywhere_gives_a_recipe(tmp_path):
    """The first-run failure. It must say how to fix itself, not just what failed."""
    overlay = make_overlay(tmp_path / "mine")

    result = launch(overlay)

    assert result.returncode == 1
    assert "Could not find the Orchestra Engine" in result.stderr
    assert "git clone" in result.stderr
    assert "ORCHESTRA_ENGINE_DIR" in result.stderr
