"""Which settings layers reach a stage agent.

A stage's instruction is its prompt. The operator's ~/.claude/CLAUDE.md is
written for their own interactive sessions, and left to the CLI default it
loads into every stage as a second, unasked-for instruction — including this
engine's own driver guidance, which tells an agent to hand decisions back to
the human. These tests pin the layer list so that default can't creep back.
"""

import pytest

import orchestra_engine.engine.session as session
from orchestra_engine.engine import DEFAULT_SETTING_SOURCES
from orchestra_engine.main import resolve_setting_sources


class RecordingQuery:
    """Stands in for the SDK's query(), capturing the options it was handed."""

    def __init__(self):
        self.options = None

    def __call__(self, *, prompt, options):
        self.options = options

        async def empty():
            return
            yield  # pragma: no cover — makes this an async generator

        return empty()


def run_one(run, tmp_path, **kwargs):
    return run(
        session.run_stage(
            "plan", "p", tmp_path, tmp_path / "artifacts", log=lambda _: None, **kwargs
        )
    )


def test_user_memory_is_not_loaded_by_default(run, monkeypatch, tmp_path):
    """The regression this exists for: with no --setting-sources, a stage must
    not inherit the operator's global CLAUDE.md."""
    q = RecordingQuery()
    monkeypatch.setattr(session, "query", q)

    run_one(run, tmp_path)

    assert q.options.setting_sources == ["project"]
    assert "user" not in q.options.setting_sources


def test_setting_sources_is_always_explicit(run, monkeypatch, tmp_path):
    """Never left as None: that is what makes the CLI apply its own default."""
    q = RecordingQuery()
    monkeypatch.setattr(session, "query", q)

    run_one(run, tmp_path)

    assert q.options.setting_sources is not None


def test_caller_can_opt_user_memory_back_in(run, monkeypatch, tmp_path):
    """A pipeline needing a user-level MCP server has a way back."""
    q = RecordingQuery()
    monkeypatch.setattr(session, "query", q)

    run_one(run, tmp_path, setting_sources=("user", "project"))

    assert q.options.setting_sources == ["user", "project"]


def test_empty_sources_loads_nothing(run, monkeypatch, tmp_path):
    q = RecordingQuery()
    monkeypatch.setattr(session, "query", q)

    run_one(run, tmp_path, setting_sources=())

    assert q.options.setting_sources == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DEFAULT_SETTING_SOURCES),
        ("none", ()),
        ("NONE", ()),
        ("user", ("user",)),
        ("user,project", ("user", "project")),
        (" user , local ", ("user", "local")),
    ],
)
def test_resolve_setting_sources(raw, expected):
    assert resolve_setting_sources(raw) == expected


@pytest.mark.parametrize("raw", ["", "bogus", "user,bogus", "none,user", ","])
def test_resolve_setting_sources_rejects_junk(raw):
    """A typo must not silently fall back to loading everything."""
    with pytest.raises(SystemExit):
        resolve_setting_sources(raw)
