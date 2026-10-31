"""Pipelines: ordered lists of stage prompts loaded from a directory.

A pipeline is just a folder of numbered markdown files:

    my-pipeline/
    ├── 01-research.md
    ├── 02-draft.md
    └── 03-polish.md

Order comes from the filename sort; the stage name is the part after the
number (the Stage/Pipeline types themselves live in model.py). Each file may
start with a frontmatter block of stage options:

    ---
    repeat: 3            # run this stage 3 times, threading handoff between passes
    fan_out: leads.txt   # run once per line of this artifacts file, in parallel
    max_items: 5         # cap on fan-out items (top of the file wins)
    decide: repro, diag  # this stage may also jump the run back to one of these
                         # named stages (every plain stage can already advance /
                         # again / done by writing decision.json; `decide` adds
                         # the named jump targets). A hop budget bounds it.
    redirect: ../../skills/review/SKILL.md
                         # take this stage's prompt from that file rather than
                         # from the body below, so a prompt can live where it is
                         # already maintained. The target's own frontmatter is
                         # ignored, so a SKILL.md works as-is; the body below is
                         # the fallback, and runs whenever that file isn't there.
    handoff: none        # what this stage sees of what came before: "last" (the
                         # default, the previous stage's report), "all" (every
                         # pass — only meaningful with repeat), or "none" (no
                         # reports and no transcript pointers, so the stage
                         # starts from the request and the workspace alone).
                         # Use "none" when inheriting an earlier reading is the
                         # thing you are trying to prevent. It cannot decide.
    ---

Paths in frontmatter (fan_out, skip_if) are relative to the run's artifacts
directory, not the workspace — they name files an earlier stage wrote for the
engine to read.

`redirect` is the exception: it names *authored* content rather than something
an agent produced mid-run, so it resolves against the stage file's own directory
and may point anywhere — including into the repo's own skills.

Prompt templates may use these placeholders: {task} (the user's task),
{handoff} (names the stages that reported and points at the read_handoff tool
that serves them — their summaries and session IDs come through that tool, not
through the prompt; empty for the first stage), {iteration} and {repeat}
(current pass / total passes), {item} (the
fan-out item, empty for normal stages), {artifacts} (absolute path to the
run's artifacts directory — where stage documents go), and {docs} (absolute
path to the operator's documents directory — where a stage publishes the
document a *human* reads, outside the timestamped run tree; only stages naming
it can write there). A pipeline folder may
also ship its own handoff.md to override the default handoff template.
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

from .model import PLACEHOLDERS, Pipeline, SkipIf, Stage

BUILTIN_PIPELINES_DIR = Path(__file__).parent / "pipelines"

# Extra pipeline collections to discover by name, alongside the built-ins. Each
# entry is a PARENT directory holding pipeline subfolders (same shape as
# BUILTIN_PIPELINES_DIR). This is how a thin overlay repo — one that
# ships only pipelines/ and reuses this engine —
# adds its own pipelines without copying the engine. Set it to that repo's
# pipelines/ dir (os.pathsep-separated for several).
PIPELINES_PATH_ENV = "ORCHESTRA_PIPELINES_PATH"
DEFAULT_HANDOFF_TEMPLATE = (Path(__file__).parent / "handoff.md").read_text(encoding="utf-8")

_STAGE_FILE_RE = re.compile(r"^\d+[-_](?P<name>.+)\.md$")
_STAGE_OPTIONS = (
    "repeat", "fan_out", "max_items", "handoff", "skip_if", "decide", "redirect",
)
_HANDOFF_SCOPES = ("last", "all", "none")
_SKIP_IF_SEPARATOR = " contains "


def stage_signature(stage: Stage) -> str:
    """Human-readable stage label with its repeat/fan-out shape, for banners."""
    label = stage.name
    if stage.repeat > 1:
        label += f" x{stage.repeat}"
        if stage.handoff == "all":
            label += " cumulative"
    if stage.handoff == "none":
        label += " [sees no prior stage]"
    if stage.fan_out:
        cap = f", top {stage.max_items}" if stage.max_items else ""
        label += f" [parallel per {stage.fan_out}{cap}]"
    if stage.skip_if:
        label += f" [skipped if {stage.skip_if.file} contains {stage.skip_if.needle!r}]"
    if stage.redirect:
        label += (
            f" [prompt from {stage.redirect}]"
            if stage.redirect_source
            else f" [{stage.redirect} not found — running this file's own body]"
        )
    if stage.decide_targets:
        label += f" [decides: may loop back to {', '.join(stage.decide_targets)}]"
    return label


def pipeline_mermaid(pipeline: Pipeline) -> str:
    """A Mermaid flowchart of a pipeline's routing, built from the stage data.

    Nodes are stages (terminal, fan-out and gated ones annotated); solid edges
    are the forward advance; `back` edges are each stage's declared decide
    targets. Any plain stage may also advance / again / done — noted once at the
    top, since that's a universal capability, not per-stage structure. Reading
    it off the real stages means the picture can't drift from the pipeline.
    """
    stages = pipeline.stages
    last = len(stages) - 1
    node_id = {s.name: f"S{i}" for i, s in enumerate(stages)}

    def label(i: int, stage: Stage) -> str:
        parts = [stage.name]
        if stage.fan_out:
            parts.append("fan-out")
        if stage.skip_if:
            parts.append(f"skip if {stage.skip_if.needle}")
        if i == last:
            parts.append("terminal")
        return " · ".join(parts).replace('"', "'")

    lines = [
        f"%% {pipeline.name} — every plain stage may also advance / again / done",
        "flowchart TD",
    ]
    for i, stage in enumerate(stages):
        text = label(i, stage)
        # terminal gets a stadium node; the rest are rounded rectangles
        lines.append(f'    {node_id[stage.name]}(["{text}"])' if i == last
                     else f'    {node_id[stage.name]}("{text}")')
    for i in range(last):
        lines.append(f"    {node_id[stages[i].name]} --> {node_id[stages[i + 1].name]}")
    for stage in stages:
        for target in stage.decide_targets or ():
            if target in node_id:
                lines.append(f"    {node_id[stage.name]} -->|back| {node_id[target]}")
    return "\n".join(lines)


def builtin_pipelines() -> list[str]:
    """Names of the built-in pipelines, flat — grouping subfolders don't count."""
    found: dict[str, Path] = {}
    _collect_pipelines_from(BUILTIN_PIPELINES_DIR, found)
    return sorted(found)


def _overlay_dirs() -> list[Path]:
    """Parent dirs from $ORCHESTRA_PIPELINES_PATH — each holds pipeline subfolders."""
    raw = os.environ.get(PIPELINES_PATH_ENV, "")
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def pipeline_search_dirs() -> list[Path]:
    """Where a bare pipeline name is looked up: overlays first, built-ins last.

    Overlays lead so an overlay may deliberately shadow a built-in of the same
    name; the built-in dir is always searched, so overlays only ever add."""
    return [d for d in (*_overlay_dirs(), BUILTIN_PIPELINES_DIR) if d.is_dir()]


def _has_stage_files(directory: Path) -> bool:
    return any(_STAGE_FILE_RE.match(f.name) for f in directory.glob("*.md"))


def _collect_pipelines_from(parent: Path, found: dict[str, Path]) -> None:
    """Add the pipelines under `parent` to `found`, first name wins.

    A child directory that holds a numbered stage file *is* a pipeline, keyed by
    its folder name. A child that holds none is treated as a **group** — a purely
    organizational subfolder (e.g. `structural/` over `refactor` and
    `architecture`) — and its own immediate children are scanned for pipelines.
    That's one level of grouping and no deeper: pipeline names stay flat and
    globally unique so invocation is always `--pipeline <name>` and never a path,
    and the fixed bound keeps a symlinked cycle from ever looping discovery."""
    for child in sorted(parent.iterdir()):
        if not child.is_dir():
            continue
        if _has_stage_files(child):
            found.setdefault(child.name, child)
        else:  # a group folder — look one level in, not deeper
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir() and _has_stage_files(grandchild):
                    found.setdefault(grandchild.name, grandchild)


def discover_pipelines() -> dict[str, Path]:
    """Map every discoverable pipeline name to its directory (first wins).

    A directory counts as a pipeline only if it holds a numbered stage file, so a
    stray folder in a search dir doesn't masquerade as one; a folder that holds
    *pipelines* rather than stage files is a group, and its pipelines are found
    one level in. Overlays are searched before built-ins, so an overlay can
    shadow a built-in of the same name."""
    found: dict[str, Path] = {}
    for parent in pipeline_search_dirs():
        _collect_pipelines_from(parent, found)
    return found


def available_pipelines() -> list[str]:
    """All pipeline names runnable by bare name — built-ins plus any overlays."""
    return sorted(discover_pipelines())


def pipeline_group(directory: Path) -> str | None:
    """The group folder a pipeline sits in, or None if it's flat under a search dir."""
    return None if directory.parent in pipeline_search_dirs() else directory.parent.name


def resolve_pipeline_dir(name_or_path: str) -> Path:
    """A pipeline's directory, from a path or a discoverable (built-in/overlay) name."""
    path = Path(name_or_path).expanduser()
    if path.is_dir():
        return path
    found = discover_pipelines()
    if name_or_path in found:
        return found[name_or_path]
    raise ValueError(
        f"Pipeline {name_or_path!r} not found — pass a directory path or one "
        f"of: {', '.join(available_pipelines())}"
    )


def filter_stages(pipeline: Pipeline, wanted: list[str]) -> Pipeline:
    """Keep only the named stages (pipeline order preserved)."""
    known = {s.name for s in pipeline.stages}
    unknown = [w for w in wanted if w not in known]
    if unknown:
        raise ValueError(
            f"Unknown stage(s) {', '.join(unknown)} — pipeline "
            f"'{pipeline.name}' has: {', '.join(s.name for s in pipeline.stages)}"
        )
    wanted_set = set(wanted)
    return dataclasses.replace(
        pipeline, stages=tuple(s for s in pipeline.stages if s.name in wanted_set)
    )


def apply_overrides(
    pipeline: Pipeline,
    repeats: dict[str, int] | None = None,
    max_items: dict[str, int] | None = None,
) -> Pipeline:
    """Override stage repeat counts / fan-out caps (from flags or the wizard)."""
    repeats = repeats or {}
    max_items = max_items or {}
    if not repeats and not max_items:
        return pipeline

    known = {s.name for s in pipeline.stages}
    unknown = [n for n in {*repeats, *max_items} if n not in known]
    if unknown:
        raise ValueError(
            f"Unknown stage(s) {', '.join(sorted(unknown))} — pipeline "
            f"'{pipeline.name}' has: {', '.join(s.name for s in pipeline.stages)}"
        )
    for name, value in {**repeats, **max_items}.items():
        if value < 1:
            raise ValueError(f"Stage counts must be >= 1, got {name}={value}")

    stages = []
    for stage in pipeline.stages:
        changes = {}
        if stage.name in repeats:
            changes["repeat"] = repeats[stage.name]
        if stage.name in max_items:
            if stage.fan_out is None:
                raise ValueError(
                    f"max_items only applies to fan-out stages; '{stage.name}' has no fan_out"
                )
            changes["max_items"] = max_items[stage.name]
        stages.append(dataclasses.replace(stage, **changes) if changes else stage)
    return dataclasses.replace(pipeline, stages=tuple(stages))


def parse_stage_counts(spec: str | None) -> dict[str, int]:
    """Parse a 'stage=N,stage2=M' spec into a dict. Raises ValueError on bad input."""
    counts: dict[str, int] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, num = part.partition("=")
        name = name.strip()
        if not sep or not name or not num.strip().isdigit() or int(num) < 1:
            raise ValueError(
                f"Expected stage=N pairs (N a positive integer), got {part!r}"
            )
        counts[name] = int(num)
    return counts


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `--- ... ---` options block off a stage file."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    options: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return options, "".join(lines[i + 1 :])
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"Bad frontmatter line {line.strip()!r} — expected 'key: value'")
        options[key.strip()] = value.strip()
    raise ValueError("Frontmatter block opened with '---' but never closed")


def _strip_frontmatter_block(text: str) -> str:
    """Drop a leading `--- ... ---` block from a *foreign* file, unparsed.

    Deliberately not `_split_frontmatter`: that one reads the block as stage
    options and is strict about the dialect, which is right for a stage file and
    wrong for a redirect target. A SKILL.md opens with YAML this engine has no
    business interpreting — `name:`, `description:`, list values — so the block
    is cut on its fences and thrown away, keys unread. Shape stays with the
    stage file; the target contributes prose only. A `redirect:` inside a target
    goes the same way, so redirects can never chain or cycle.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[i + 1 :])
    return text  # opened and never closed — not a frontmatter block, keep it all


_SAFE_BRACE_RE = re.compile("|".join(rf"\{{{name}\}}" for name in PLACEHOLDERS))


def _escape_foreign_braces(text: str) -> str:
    """Double every brace that isn't one of the engine's own placeholders.

    A redirect target usually wasn't written for this engine, and prose like
    that is full of braces nobody meant as template syntax: a JSON sample, a
    shell expansion, a TypeScript generic. Prompts render through str.format, so
    one stray `{...}` would raise on the prompt of a stage the run had already
    committed to. Escaping leaves {task} and friends live and turns everything
    else back into the literal text the file's author wrote.
    """
    out, last = [], 0
    for match in _SAFE_BRACE_RE.finditer(text):
        out.append(text[last : match.start()].replace("{", "{{").replace("}", "}}"))
        out.append(match.group(0))
        last = match.end()
    out.append(text[last:].replace("{", "{{").replace("}", "}}"))
    return "".join(out)


def _load_redirect(value: str, source: Path) -> tuple[str, str] | None:
    """A stage's `redirect:` target as (prompt, file read), or None if it can't be.

    None means the target can't stand in for a prompt — missing, a directory,
    empty, unreadable — and the caller runs the stage file's own body instead.
    A pipeline that points at a file which isn't on this machine still runs.

    The path resolves against the stage file's directory and may leave it: `..`
    and absolute paths are both fine. That inverts the rule for `fan_out` and
    `skip_if` on purpose. Those name files an *agent* wrote during the run, so
    they stay confined to the artifacts directory; a redirect names authored
    content in a folder the operator named on the command line — the same trust
    as the stage file doing the pointing.
    """
    target = (source.parent / Path(value).expanduser()).resolve()
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    body = _strip_frontmatter_block(text)
    if not body.strip():
        return None
    return _escape_foreign_braces(body), str(target)


def _check_artifacts_relative(value: str, option: str, source: Path) -> None:
    """Reject a frontmatter path that could point outside the artifacts dir.

    First line of defense, at load time; the engine re-checks the resolved
    path at read time, since only that catches a symlink an agent plants.
    """
    path = Path(value)
    # anchor, not is_absolute(): on Windows "/etc/x" and "C:x" aren't absolute
    # but still leave the directory they're joined to.
    if path.anchor or ".." in path.parts:
        raise ValueError(
            f"{source}: {option} must be a relative path inside the run's "
            f"artifacts directory, got {value!r}"
        )


def _parse_skip_if(value: str, source: Path) -> SkipIf:
    """Parse `<file> contains <text>` into a gate."""
    file, sep, needle = value.partition(_SKIP_IF_SEPARATOR)
    if not sep or not file.strip() or not needle.strip():
        raise ValueError(
            f"{source}: skip_if must read '<file> contains <text>', got {value!r}"
        )
    file = file.strip()
    _check_artifacts_relative(file, "skip_if", source)
    return SkipIf(file=file, needle=needle.strip())


def _parse_stage(name: str, text: str, source: Path) -> Stage:
    try:
        options, body = _split_frontmatter(text)
    except ValueError as exc:
        raise ValueError(f"{source}: {exc}") from exc

    unknown = set(options) - set(_STAGE_OPTIONS)
    if unknown:
        raise ValueError(
            f"{source}: unknown stage option(s) {', '.join(sorted(unknown))} — "
            f"supported: {', '.join(_STAGE_OPTIONS)}"
        )

    def _int_option(key: str, default: int | None) -> int | None:
        if key not in options:
            return default
        try:
            value = int(options[key])
        except ValueError:
            raise ValueError(f"{source}: {key} must be an integer, got {options[key]!r}")
        if value < 1:
            raise ValueError(f"{source}: {key} must be >= 1, got {value}")
        return value

    fan_out = options.get("fan_out")
    if fan_out is not None:
        _check_artifacts_relative(fan_out, "fan_out", source)

    skip_if = options.get("skip_if")
    if skip_if is not None:
        skip_if = _parse_skip_if(skip_if, source)

    max_items = _int_option("max_items", None)
    if max_items is not None and fan_out is None:
        raise ValueError(f"{source}: max_items only makes sense with fan_out")

    repeat = _int_option("repeat", 1)
    handoff = options.get("handoff", "last")
    if handoff not in _HANDOFF_SCOPES:
        raise ValueError(
            f"{source}: handoff must be one of {', '.join(_HANDOFF_SCOPES)}, got {handoff!r}"
        )
    if handoff == "all" and repeat == 1:
        raise ValueError(
            f"{source}: handoff: all only affects stages that repeat — add repeat: N or drop it"
        )
    if handoff == "none" and options.get("decide") is not None:
        raise ValueError(
            f"{source}: handoff: none cannot decide — a stage that sees no prior "
            f"report has nothing to route on"
        )

    decide = options.get("decide")
    decide_targets = None
    if decide is not None:
        decide_targets = tuple(t.strip() for t in decide.split(",") if t.strip())
        if not decide_targets:
            raise ValueError(
                f"{source}: decide must list at least one stage name, e.g. "
                f"decide: reproduce, diagnose"
            )
        if fan_out is not None:
            raise ValueError(
                f"{source}: a stage can't both fan_out and decide — parallel agents "
                f"would race to write the one decision"
            )
        if repeat > 1:
            raise ValueError(
                f"{source}: a stage can't both repeat and decide — a fixed repeat is "
                f"a shape, routing is a decision; use one or the other"
            )

    # Where the prompt text comes from. The body is always the fallback: a
    # redirect that isn't on disk downgrades to it rather than failing the run,
    # which is why a redirect stage must have one.
    redirect = options.get("redirect")
    prompt, redirect_source = body, None
    if redirect is not None:
        if not body.strip():
            raise ValueError(
                f"{source}: a stage with 'redirect: {redirect}' still needs a body "
                f"of its own — it is what runs when that file isn't on this machine. "
                f"Say what the stage is for and how to get the target"
            )
        loaded = _load_redirect(redirect, source)
        if loaded is not None:
            prompt, redirect_source = loaded

    return Stage(
        name=name,
        prompt_template=prompt,
        repeat=repeat,
        fan_out=fan_out,
        max_items=max_items,
        handoff=handoff,
        skip_if=skip_if,
        decide_targets=decide_targets,
        redirect=redirect,
        redirect_source=redirect_source,
    )


def load_pipeline(name_or_path: str) -> Pipeline:
    """Load a pipeline by name (built-in or overlay, e.g. "bugfix") or directory path."""
    path = resolve_pipeline_dir(name_or_path)

    stages = []
    for file in sorted(path.glob("*.md")):
        match = _STAGE_FILE_RE.match(file.name)
        if match:
            stages.append(_parse_stage(match.group("name"), file.read_text(encoding="utf-8"), file))
    if not stages:
        raise ValueError(
            f"No stage files in {path} — expected numbered prompts like 01-plan.md"
        )

    # A decider may only route to stages that exist, and the pipeline must keep a
    # terminal stage that always runs last — so the run can never end mid-loop
    # with no final agent to write the handback. Both are checked here, once the
    # whole stage set is known (a single file can't see its siblings).
    names = {s.name for s in stages}
    for s in stages:
        if not s.decide_targets:
            continue
        unknown = [t for t in s.decide_targets if t not in names]
        if unknown:
            raise ValueError(
                f"{path.name}: stage '{s.name}' decides to {', '.join(unknown)}, "
                f"which name no stage in this pipeline (stages: {', '.join(names)})"
            )
    if stages[-1].decide_targets:
        raise ValueError(
            f"{path.name}: the last stage '{stages[-1].name}' cannot be a decider — "
            f"a pipeline needs a terminal stage that always runs last"
        )

    handoff_file = path / "handoff.md"
    handoff = handoff_file.read_text(encoding="utf-8") if handoff_file.is_file() else DEFAULT_HANDOFF_TEMPLATE

    return Pipeline(name=path.name, stages=tuple(stages), handoff_template=handoff)
