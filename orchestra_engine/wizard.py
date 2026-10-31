"""Startup wizard: pick the run options at the terminal instead of
remembering flags.

Asks only for things not already given as flags. Esc (or < in plain-prompt
mode) steps back to the previous question; the last step is a summary with
start/cancel.
"""

from __future__ import annotations

import dataclasses
import sys

from . import ui
from .model import Pipeline
from .pipeline import (
    apply_overrides,
    available_pipelines,
    filter_stages,
    load_pipeline,
    parse_stage_counts,
    stage_signature,
)
from .ui import BACK

_CUSTOM_PATH = "__path__"


async def _ask_pipeline(allow_back: bool):
    while True:
        choice = await ui.select(
            "Pipeline",
            [(name, name) for name in available_pipelines()]
            + [("custom path… (a directory of numbered stage prompts)", _CUSTOM_PATH)],
            allow_back=allow_back,
        )
        if choice is BACK:
            return BACK
        target = choice
        if choice == _CUSTOM_PATH:
            target = await ui.text("Path to the pipeline directory:", allow_back=True)
            if target is BACK:
                continue  # back to the pipeline menu
        try:
            return load_pipeline(target)
        except ValueError as exc:
            print(f"  {exc}")


async def _ask_task(allow_back: bool):
    while True:
        task = await ui.text("What should the orchestra work on?", allow_back=allow_back)
        if task is BACK:
            return BACK
        if task:
            return task


async def _ask_stages(pipeline: Pipeline, checked: list[str] | None, allow_back: bool):
    names = [s.name for s in pipeline.stages]
    while True:
        how = await ui.select(
            "Which stages?",
            [
                ("all stages", "all"),
                ("up to a stage… (drop everything after it)", "upto"),
                ("custom selection… (checkbox list)", "custom"),
            ],
            allow_back=allow_back,
        )
        if how is BACK:
            return BACK
        if how == "all":
            return names
        if how == "upto":
            last = await ui.select(
                "Run through which stage (inclusive)?",
                [(n, n) for n in names],
                allow_back=True,
            )
            if last is BACK:
                continue  # back to the how-menu
            return names[: names.index(last) + 1]
        checked_set = set(checked) if checked is not None else set(names)
        picked = await ui.checkbox(
            "Select stages", [(n, n, n in checked_set) for n in names], allow_back=True
        )
        if picked is BACK:
            continue  # back to the how-menu
        return picked


def _countable(pipeline: Pipeline) -> bool:
    return any(s.repeat > 1 or s.fan_out for s in pipeline.stages)


async def _ask_counts(
    pipeline: Pipeline,
    repeats: dict[str, int],
    caps: dict[str, int],
    allow_back: bool,
):
    """Adjust repeat counts / fan-out caps; returns (repeats, caps) or BACK."""
    while True:
        preview = apply_overrides(pipeline, repeats, caps)
        shaped = [stage_signature(s) for s in preview.stages if s.repeat > 1 or s.fan_out]
        choices = [("keep these counts", "keep"), ("change repeat counts…", "repeats")]
        if any(s.fan_out for s in pipeline.stages):
            choices.append(("change fan-out item caps…", "caps"))
        choice = await ui.select(
            f"Stage counts: {'; '.join(shaped)}", choices, allow_back=allow_back
        )
        if choice is BACK:
            return BACK
        if choice == "keep":
            return repeats, caps
        entry = await ui.text("stage=N pairs (e.g. search=5,deep-dive=3):", allow_back=True)
        if entry is BACK:
            continue  # back to the counts menu
        try:
            parsed = parse_stage_counts(entry)
            if choice == "repeats":
                apply_overrides(pipeline, {**repeats, **parsed}, caps)  # validate
                repeats = {**repeats, **parsed}
            else:
                apply_overrides(pipeline, repeats, {**caps, **parsed})  # validate
                caps = {**caps, **parsed}
        except ValueError as exc:
            print(f"  {exc}")


async def _ask_mode(allow_back: bool):
    mode = await ui.select(
        "Mode",
        [
            ("interactive — agents can ask you questions; you gate each stage", "interactive"),
            ("autonomous — runs unattended, no questions", "autonomous"),
        ],
        allow_back=allow_back,
    )
    if mode is BACK:
        return BACK
    return mode == "interactive"


async def configure(args, pipeline: Pipeline | None) -> tuple[str, Pipeline, bool]:
    """Walk the steps flags didn't decide; Esc steps backward."""

    def apply_stages_flag(pl: Pipeline) -> Pipeline:
        if not args.stages:
            return pl
        wanted = [s.strip() for s in args.stages.split(",") if s.strip()]
        try:
            return filter_stages(pl, wanted)
        except ValueError as exc:
            sys.exit(str(exc))

    task: str | None = args.task
    picked: list[str] | None = None
    picked_for: str | None = None  # pipeline name the picks were made against
    interactive: bool = bool(args.interactive)
    repeats: dict[str, int] = dict(args.repeat_map)
    caps: dict[str, int] = dict(args.max_items_map)

    def preview_pipeline() -> Pipeline:
        try:
            return apply_overrides(pipeline, repeats, caps)
        except ValueError as exc:
            sys.exit(str(exc))  # count flags name a stage this pipeline doesn't have

    steps = []
    if pipeline is None:
        steps.append("pipeline")
    if not args.task:
        steps.append("task")
    if args.stages is None:
        steps.append("stages")
    if args.repeat is None and args.max_items is None:
        steps.append("counts")
    if not args.interactive:
        steps.append("mode")
    steps.append("confirm")

    i = 0
    direction = 1
    while i < len(steps):
        step = steps[i]
        allow_back = i > 0
        if step == "counts" and not _countable(pipeline):
            i += direction  # nothing to ask; pass through in whichever direction
            continue
        if step == "pipeline":
            result = await _ask_pipeline(allow_back)
            if result is not BACK:
                pipeline = apply_stages_flag(result)
                if picked_for != pipeline.name:
                    picked = None  # stage picks don't carry across pipelines
        elif step == "task":
            result = await _ask_task(allow_back)
            if result is not BACK:
                task = result
        elif step == "stages":
            result = await _ask_stages(pipeline, picked, allow_back)
            if result is not BACK:
                picked, picked_for = result, pipeline.name
        elif step == "counts":
            result = await _ask_counts(pipeline, repeats, caps, allow_back)
            if result is not BACK:
                repeats, caps = result
        elif step == "mode":
            result = await _ask_mode(allow_back)
            if result is not BACK:
                interactive = result
        else:  # confirm
            preview = preview_pipeline()
            stage_names = picked if picked is not None else [s.name for s in preview.stages]
            shown = [stage_signature(s) for s in preview.stages if s.name in set(stage_names)]
            print(f"\n  task:     {task}")
            print(f"  pipeline: {preview.name}")
            print(f"  stages:   {' -> '.join(shown)}")
            print(f"  mode:     {'interactive' if interactive else 'autonomous'}")
            perms = args.permission_mode
            print(f"  perms:    {perms}{'  (every tool call unreviewed)' if perms == 'bypassPermissions' else ''}")
            result = await ui.select(
                "Start the run?",
                [("start", "start"), ("cancel", "cancel")],
                allow_back=True,
            )
            if result == "cancel":
                sys.exit("cancelled")
            if result is not BACK:
                break

        direction = -1 if result is BACK else 1
        i += direction

    pipeline = preview_pipeline()
    if picked is not None and len(picked) < len(pipeline.stages):
        picked_set = set(picked)
        pipeline = dataclasses.replace(
            pipeline, stages=tuple(s for s in pipeline.stages if s.name in picked_set)
        )
    return task, pipeline, interactive
