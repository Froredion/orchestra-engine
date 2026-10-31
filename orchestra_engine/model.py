"""Pure data types shared by the description (pipeline) and execution (engine)
layers. This module imports nothing but the standard library."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import handoff_tool

# The placeholders a stage prompt may use; `Pipeline.build_prompt` supplies one
# value per name. They are named here, as data, because the *loader* needs the
# same list: text a stage pulls in with `redirect:` gets every other brace
# escaped, so a foreign markdown file can't collide with str.format.
PLACEHOLDERS = (
    "task",
    "handoff",
    "item",
    "iteration",
    "repeat",
    "artifacts",
    "docs",
)


@dataclass(frozen=True)
class SkipIf:
    """A gate: skip the stage when `file` (in the artifacts dir) contains `needle`.

    Deliberately dumb — a path and a substring. An earlier stage decides what
    the verdict is and writes it down; the engine only reads. That keeps a
    pipeline's meaning inside its own folder, the way fan_out does.
    """

    file: str
    needle: str


@dataclass(frozen=True)
class Stage:
    """One prompt file: a template plus its execution shape."""

    name: str
    prompt_template: str
    repeat: int = 1
    fan_out: str | None = None
    max_items: int | None = None
    handoff: str = "last"  # "last": see the previous pass only; "all": see every prior pass
    skip_if: SkipIf | None = None
    # When set, this is a decider: after it runs, its agent writes decision.json
    # naming which of these stages to jump back to (or done). The engine only
    # honors a target listed here — the same "an agent decides, the engine reads"
    # boundary as fan_out, but the payload is a routing choice, not a list.
    decide_targets: tuple[str, ...] | None = None
    # Where the prompt text came from, when it isn't this file's own body:
    # `redirect` is the target as written in frontmatter, `redirect_source` the
    # file actually read — None when that target was missing and this file's
    # body stood in for it.
    redirect: str | None = None
    redirect_source: str | None = None


@dataclass(frozen=True)
class Pipeline:
    """An ordered workflow of stages, loaded from a directory of prompts."""

    name: str
    stages: tuple[Stage, ...]
    handoff_template: str

    def build_prompt(
        self,
        stage: Stage,
        task: str,
        handoffs: Sequence[Handoff] = (),
        item: str = "",
        iteration: int = 1,
        artifacts: str = "",
        docs: str = "",
    ) -> str:
        """Render a stage's prompt. {handoff} names the stages that left a
        report and points at the tool serving them; no handoffs (the very first
        session) renders as empty.

        The reports themselves are deliberately not here. A handoff is the
        previous agent's own account, and interpolating it into this string put
        it in the same channel — and the same syntactic position — as this
        stage's instructions. It is served as a tool result instead; see
        engine/handoff_tool.py.

        {artifacts} is the absolute path to this run's artifacts directory —
        where a stage writes what the next stage reads.

        {docs} is the absolute path to the operator's documents directory — the
        one place a stage writes *for a human*. Everything under artifacts is
        addressed to the next agent and buried in a timestamped run directory; a
        pipeline whose deliverable is a document rather than code needs a folder
        a person can still find next month, so the operator names one on the
        command line and it outlives the run.
        """
        values = {
            "task": task,
            "handoff": handoff_tool.pointer(handoffs),
            "item": item,
            "iteration": iteration,
            "repeat": stage.repeat,
            "artifacts": artifacts,
            "docs": docs,
        }
        return stage.prompt_template.format(**values)


@dataclass
class StageResult:
    """What one agent session left behind: the handoff data plus accounting."""

    stage: str
    session_id: str | None
    summary: str
    is_error: bool
    cost_usd: float | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    iteration: int | None = None  # pass number, when the stage repeats
    item: str | None = None  # fan-out item this session worked on


@dataclass(frozen=True)
class Handoff:
    """One earlier session, as the next one sees it: what it was, what it
    produced, and the session ID that opens its full transcript."""

    label: str
    session_id: str | None
    summary: str

    @classmethod
    def from_result(cls, result: StageResult, label: str | None = None) -> Handoff:
        return cls(
            label=label or result.stage,
            session_id=result.session_id,
            summary=result.summary or "(no summary produced)",
        )
