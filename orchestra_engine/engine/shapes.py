"""Stage shapes: how one stage's agent sessions are arranged.

Two shapes exist today, and they compose:
  run_chain    N sequential passes of a stage, handoff threaded between them
  run_fan_out  one chain per item of an artifacts file, run in parallel
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Callable

from .. import handoff_tool
from ..model import Handoff, Pipeline, Stage, StageResult
from .session import DEFAULT_SETTING_SOURCES, default_log, run_stage

# Upper bound on agent sessions running at once during a fan-out stage.
FAN_OUT_CONCURRENCY = 5

# Where a run directory keeps the engine's own resume state. Beside artifacts/,
# not inside it: agents can write anywhere in artifacts/, and the record of which
# items are finished is the engine's to keep, not something a stage can edit.
CHECKPOINTS_DIRNAME = "checkpoints"


class FanOutInterrupted(Exception):
    """A fan-out stage stopped because at least one item's session raised.

    Raised only after every item already running has finished, and with the
    finished ones recorded, so resuming the stage runs just the rest.
    """

    def __init__(self, stage: str, done: int, total: int, failed: list[str], cause: str):
        self.stage = stage
        self.done = done
        self.total = total
        self.failed = failed
        self.cause = cause
        super().__init__(
            f"[{stage}] stopped after {done}/{total} item(s) finished; "
            f"{len(failed)} failed or never started ({cause})"
        )


def fan_out_checkpoint_path(run_dir: Path, stage_name: str) -> Path:
    """The file recording which of a fan-out stage's items have finished."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", stage_name)
    return run_dir / CHECKPOINTS_DIRNAME / f"fanout-{safe}.json"


def _load_checkpoint(path: Path | None, log: Callable[[str], None], stage: str) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        # A checkpoint that can't be read costs re-running the items, never
        # skipping one that didn't finish — so ignore it and say so.
        log(f"[{stage}] fan-out checkpoint {path} unreadable ({exc}) — running every item")
        return {}
    done = data.get("done") if isinstance(data, dict) else None
    return done if isinstance(done, dict) else {}


def _save_checkpoint(path: Path, done: dict) -> None:
    """Write atomically: a crash mid-write must leave the previous record, not half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"done": done}, indent=2))
    os.replace(tmp, path)


async def run_chain(
    stage: Stage,
    pipeline: Pipeline,
    task: str,
    workspace: Path,
    artifacts: Path,
    prev: StageResult | None,
    *,
    docs: str = "",
    item: str | None = None,
    label_prefix: str | None = None,
    extra_context: str | None = None,
    extra_add_dirs: list[str] | None = None,
    permission_mode: str,
    max_turns: int | None,
    model: str | None,
    interactive: bool,
    setting_sources: tuple[str, ...] = DEFAULT_SETTING_SOURCES,
    log: Callable[[str], None] = default_log,
) -> list[StageResult]:
    """Run one stage `repeat` times sequentially, threading handoff between passes.

    History accumulates as the chain runs — the incoming stage's handoff, then
    each completed pass. What a pass actually sees is `stage.handoff`:
    "last" shows it only the pass before it; "all" shows it every prior pass,
    so it holds every earlier session ID rather than just the newest; "none"
    shows it nothing, so the stage starts from the request and the workspace
    alone. "none" exists for the case where inheriting an earlier stage's
    framing is the failure mode — a stage asked to read something afresh cannot
    do that while the previous reading sits in its context, and asking it to
    ignore what it has already been told is not a barrier, it is a request.

    extra_context is appended to every pass's prompt (every stage gets its prior
    sessions' transcript paths this way); extra_add_dirs grants the read access
    to reach them.
    """
    results: list[StageResult] = []
    history: list[Handoff] = [Handoff.from_result(prev)] if prev else []
    for i in range(1, stage.repeat + 1):
        if stage.handoff == "none":
            visible: list[Handoff] = []
        elif stage.handoff == "all":
            visible = history
        else:
            visible = history[-1:]
        prompt = pipeline.build_prompt(
            stage,
            task,
            handoffs=visible,
            item=item or "",
            iteration=i,
            artifacts=str(artifacts),
            docs=docs,
        )
        if extra_context:
            prompt += extra_context
        # The prompt names who reported; the reports themselves go to the tool.
        handoff_body = handoff_tool.render(visible, pipeline.handoff_template) if visible else None
        label = label_prefix or stage.name
        if stage.repeat > 1:
            label = f"{label} {i}/{stage.repeat}"
        result = await run_stage(
            stage.name,
            prompt,
            workspace,
            artifacts,
            permission_mode=permission_mode,
            max_turns=max_turns,
            model=model,
            interactive=interactive,
            log=log,
            label=label,
            iteration=i if stage.repeat > 1 else None,
            item=item,
            extra_add_dirs=extra_add_dirs,
            setting_sources=setting_sources,
            handoff_body=handoff_body,
        )
        results.append(result)
        if result.is_error:
            break
        pass_label = f"{stage.name} (pass {i}/{stage.repeat})"
        if item:
            pass_label += f" for {item}"
        history.append(Handoff.from_result(result, label=pass_label))
    return results


def _read_fan_out_items(
    artifacts: Path, stage: Stage, log: Callable[[str], None]
) -> list[str]:
    """Read the stage's fan-out items file (one item per line) from the artifacts dir.

    The path is confined to that directory so a stage file can never point the
    engine at host files outside the agents' sandbox. resolve() runs before the
    check, so a symlink an agent planted is caught too.
    """
    path = (artifacts / stage.fan_out).resolve()
    if not path.is_relative_to(artifacts.resolve()):
        raise ValueError(f"fan_out file {stage.fan_out!r} escapes the artifacts directory")
    if not path.is_file():
        raise ValueError(
            f"fan_out file {stage.fan_out!r} not found in the artifacts directory "
            f"— the previous stage was expected to write it"
        )
    items = [line.strip() for line in path.read_text().splitlines()]
    items = [i for i in items if i and not i.startswith("#")]
    if not items:
        raise ValueError(f"fan_out file {stage.fan_out!r} is empty")
    if stage.max_items is not None and len(items) > stage.max_items:
        log(
            f"[{stage.name}] {len(items)} items in {stage.fan_out}, "
            f"taking the top {stage.max_items}"
        )
        items = items[: stage.max_items]
    return items


def _shorten(item: str, limit: int = 40) -> str:
    item = item.removeprefix("https://").removeprefix("http://").rstrip("/")
    return item if len(item) <= limit else item[: limit - 1] + "…"


async def run_fan_out(
    stage: Stage,
    pipeline: Pipeline,
    task: str,
    workspace: Path,
    artifacts: Path,
    prev: StageResult | None,
    *,
    docs: str = "",
    extra_context: str | None = None,
    extra_add_dirs: list[str] | None = None,
    permission_mode: str,
    max_turns: int | None,
    model: str | None,
    interactive: bool,
    setting_sources: tuple[str, ...] = DEFAULT_SETTING_SOURCES,
    log: Callable[[str], None] = default_log,
    checkpoint_path: Path | None = None,
) -> tuple[list[StageResult], StageResult]:
    """Run the stage once per item, in parallel. Returns (all session results,
    a merged result to hand to the next stage).

    extra_context / extra_add_dirs reach every item's chain, so a fan-out agent
    can pull from the prior stages' transcripts the same way a plain stage can.

    checkpoint_path, when given, records each item the moment it finishes. A
    stage that is interrupted — a usage limit, a dropped connection, a killed
    process — and then re-entered skips the items recorded there and runs only
    the rest. One item's session raising no longer discards the others: items
    already running finish and are recorded, queued ones are not started, and
    FanOutInterrupted is raised once the stage has drained. The record is
    removed when the stage completes, so a later route-back into the same stage
    starts fresh rather than skipping work it was sent back to redo.
    """
    items = _read_fan_out_items(artifacts, stage, log)
    done = _load_checkpoint(checkpoint_path, log, stage.name)
    already = [it for it in items if it in done]
    log(
        f"[{stage.name}] fanning out over {len(items)} items "
        f"({stage.repeat} pass(es) each, up to {FAN_OUT_CONCURRENCY} in parallel)"
    )
    if already:
        log(
            f"[{stage.name}] resuming: {len(already)}/{len(items)} item(s) already "
            f"finished in an earlier pass — running the other {len(items) - len(already)}"
        )
    if interactive:
        # Parallel sessions can't share one terminal; item agents run unattended.
        log(f"[{stage.name}] parallel sessions run without human interaction")

    semaphore = asyncio.Semaphore(FAN_OUT_CONCURRENCY)
    # Set by the first item that raises. Whatever made it raise — a usage limit
    # most often — will fail every session started after it, so stop starting
    # them; the record says which items are left for the resume.
    stop = asyncio.Event()
    raised: list[tuple[str, Exception]] = []
    not_started: list[str] = []

    async def run_item(idx: int, item: str) -> list[StageResult] | None:
        if item in done:
            rec = done[item]
            return [
                StageResult(
                    stage=stage.name,
                    session_id=rec.get("session_id"),
                    summary=rec.get("summary", ""),
                    is_error=False,
                    item=item,
                )
            ]
        async with semaphore:
            if stop.is_set():
                not_started.append(item)
                return None
            try:
                chain = await run_chain(
                    stage,
                    pipeline,
                    task,
                    workspace,
                    artifacts,
                    prev,
                    docs=docs,
                    item=item,
                    label_prefix=f"{stage.name} {idx}/{len(items)} {_shorten(item)}",
                    extra_context=extra_context,
                    extra_add_dirs=extra_add_dirs,
                    permission_mode=permission_mode,
                    max_turns=max_turns,
                    model=model,
                    interactive=False,
                    setting_sources=setting_sources,
                    log=log,
                )
            except Exception as exc:
                stop.set()
                raised.append((item, exc))
                return None
        final = chain[-1]
        if checkpoint_path is not None and not final.is_error:
            done[item] = {"session_id": final.session_id, "summary": final.summary}
            _save_checkpoint(checkpoint_path, done)
        return chain

    outcomes = await asyncio.gather(*(run_item(i, it) for i, it in enumerate(items, 1)))

    if raised:
        failed = [item for item, _ in raised] + not_started
        first_item, first_exc = raised[0]
        raise FanOutInterrupted(
            stage.name,
            done=len(items) - len(failed),
            total=len(items),
            failed=failed,
            cause=f"{_shorten(first_item)}: {type(first_exc).__name__}: {first_exc}",
        ) from first_exc

    chains: list[list[StageResult]] = [c for c in outcomes if c is not None]
    if checkpoint_path is not None:
        checkpoint_path.unlink(missing_ok=True)

    sections = []
    failures = 0
    for item, chain in zip(items, chains):
        final = chain[-1]
        if final.is_error:
            failures += 1
        status = "ERROR" if final.is_error else "ok"
        sections.append(
            f"### {item} [{status}] (final session: {final.session_id})\n{final.summary}"
        )
    merged = StageResult(
        stage=stage.name,
        session_id="(multiple — per-item session IDs are listed in the summary)",
        summary=(
            f"Parallel results for {len(items)} items "
            f"({failures} failed):\n\n" + "\n\n".join(sections)
        ),
        is_error=failures == len(items),
    )
    if failures:
        log(f"[{stage.name}] {failures}/{len(items)} item(s) failed")
    return [r for chain in chains for r in chain], merged
