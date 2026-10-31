"""Overlay discovery: pipelines found by name from $ORCHESTRA_PIPELINES_PATH, on top
of the built-ins. This is how a thin overlay repo adds its own
pipelines while reusing this engine, instead of copying it."""

import os

import pytest

from orchestra_engine.pipeline import (
    PIPELINES_PATH_ENV,
    available_pipelines,
    builtin_pipelines,
    discover_pipelines,
    load_pipeline,
    pipeline_group,
    resolve_pipeline_dir,
)


def make_overlay(tmp_path, name, files):
    """A parent dir holding one pipeline subfolder `name`; returns the parent."""
    d = tmp_path / "overlay" / name
    d.mkdir(parents=True)
    for filename, text in files.items():
        (d / filename).write_text(text)
    return tmp_path / "overlay"


def test_no_env_leaves_only_builtins(monkeypatch):
    monkeypatch.delenv(PIPELINES_PATH_ENV, raising=False)
    assert available_pipelines() == builtin_pipelines()


def test_overlay_pipeline_discovered_by_name(monkeypatch, tmp_path):
    parent = make_overlay(tmp_path, "delivery", {"01-plan.md": "do {task}\n{handoff}"})
    monkeypatch.setenv(PIPELINES_PATH_ENV, str(parent))

    assert "delivery" in available_pipelines()
    assert resolve_pipeline_dir("delivery") == parent / "delivery"
    # And it actually loads by that bare name — no path needed.
    assert load_pipeline("delivery").name == "delivery"


def test_overlay_adds_without_dropping_builtins(monkeypatch, tmp_path):
    parent = make_overlay(tmp_path, "delivery", {"01-plan.md": "x"})
    monkeypatch.setenv(PIPELINES_PATH_ENV, str(parent))
    names = available_pipelines()
    assert "delivery" in names
    assert set(builtin_pipelines()).issubset(names)


def test_overlay_shadows_builtin_of_same_name(monkeypatch, tmp_path):
    parent = make_overlay(tmp_path, "bugfix", {"01-only.md": "overlay bugfix"})
    monkeypatch.setenv(PIPELINES_PATH_ENV, str(parent))
    # Overlays lead the search order, so the overlay wins for a shared name.
    assert resolve_pipeline_dir("bugfix") == parent / "bugfix"


def test_folder_without_stage_files_is_not_a_pipeline(monkeypatch, tmp_path):
    parent = make_overlay(tmp_path, "notes", {"README.md": "not a stage"})
    monkeypatch.setenv(PIPELINES_PATH_ENV, str(parent))
    assert "notes" not in discover_pipelines()


def test_several_overlays_via_pathsep(monkeypatch, tmp_path):
    a = make_overlay(tmp_path, "alpha", {"01-a.md": "a"})
    b = tmp_path / "beta-parent" / "beta"
    b.mkdir(parents=True)
    (b / "01-b.md").write_text("b")
    monkeypatch.setenv(PIPELINES_PATH_ENV, os.pathsep.join([str(a), str(b.parent)]))
    names = available_pipelines()
    assert {"alpha", "beta"}.issubset(names)


def test_unknown_name_still_errors(monkeypatch):
    monkeypatch.delenv(PIPELINES_PATH_ENV, raising=False)
    with pytest.raises(ValueError, match="not found"):
        resolve_pipeline_dir("does-not-exist")


# --- Grouping: organizational subfolders, names stay flat --------------------


def make_grouped_overlay(tmp_path, group, name, files):
    """A parent dir holding a group folder with one pipeline inside; returns parent."""
    d = tmp_path / "overlay" / group / name
    d.mkdir(parents=True)
    for filename, text in files.items():
        (d / filename).write_text(text)
    return tmp_path / "overlay"


def test_grouped_pipeline_discovered_by_flat_name(monkeypatch, tmp_path):
    parent = make_grouped_overlay(tmp_path, "structural", "delivery", {"01-plan.md": "x"})
    monkeypatch.setenv(PIPELINES_PATH_ENV, str(parent))
    # Found and loaded by its bare name — the group folder is organizational only,
    # never part of how you address the pipeline.
    assert "delivery" in available_pipelines()
    assert resolve_pipeline_dir("delivery") == parent / "structural" / "delivery"
    assert load_pipeline("delivery").name == "delivery"


def test_group_folder_itself_is_not_a_pipeline(monkeypatch, tmp_path):
    parent = make_grouped_overlay(tmp_path, "structural", "delivery", {"01-plan.md": "x"})
    monkeypatch.setenv(PIPELINES_PATH_ENV, str(parent))
    # The container has no stage files, so it never masquerades as a pipeline.
    assert "structural" not in discover_pipelines()


def test_grouping_is_bounded_to_one_level(monkeypatch, tmp_path):
    # A pipeline two levels deep is deliberately NOT found: names stay flat and a
    # symlinked cycle can never loop discovery.
    deep = tmp_path / "overlay" / "group" / "subgroup" / "deep"
    deep.mkdir(parents=True)
    (deep / "01-x.md").write_text("x")
    monkeypatch.setenv(PIPELINES_PATH_ENV, str(tmp_path / "overlay"))
    assert "deep" not in discover_pipelines()


def test_builtin_structural_group(monkeypatch):
    monkeypatch.delenv(PIPELINES_PATH_ENV, raising=False)
    # refactor and architecture live under the 'structural' group but resolve flat.
    assert {"refactor", "architecture"}.issubset(builtin_pipelines())
    assert pipeline_group(resolve_pipeline_dir("refactor")) == "structural"
    assert pipeline_group(resolve_pipeline_dir("architecture")) == "structural"
    # A flat built-in reports no group.
    assert pipeline_group(resolve_pipeline_dir("bugfix")) is None
