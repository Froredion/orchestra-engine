"""CLI entry point: python -m orchestra_engine "build a todo CLI app" """

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from . import channel, guidance, ui, viewer, window, wizard
from .engine import DEFAULT_SETTING_SOURCES, run_loop
from .engine.shapes import FanOutInterrupted
from .pipeline import (
    PIPELINES_PATH_ENV,
    apply_overrides,
    available_pipelines,
    filter_stages,
    load_pipeline,
    parse_stage_counts,
    pipeline_group,
    pipeline_mermaid,
    resolve_pipeline_dir,
    stage_signature,
)


EPILOG = """\
how it works
  Each stage is its own agent session. When one finishes, its final summary
  and session ID are handed to the next, so the next agent can pick up where
  the last left off — and open the previous transcript if the summary is too
  thin. Nothing is shared but that handoff and the files on disk.

  A pipeline is just a folder of numbered prompt files (01-plan.md,
  02-build.md, ...). The engine doesn't know what any of them mean: it runs
  them in filename order. To write your own, make a folder and pass it to
  --pipeline. Each file may open with a frontmatter block setting its shape —
  repeat: N to run it several times, fan_out: FILE to run one agent per line
  of an artifacts file in parallel, skip_if: FILE contains TEXT to skip it,
  decide: STAGE,... to let its agent loop the run back to an earlier stage.

  Code goes in the repo, reasoning goes in the run. Agents change code and
  tests inside --workspace — that's the deliverable, and it's all they leave
  there. Everything else they write (the stage documents they hand each other,
  and the JSON log of summaries, session IDs, turns and costs) lands in a
  fresh per-run directory under --runs-dir, so your repo stays clean and one
  run never overwrites another's evidence.

see also
  --install-guidance once per machine: teach your coding agent how to drive this
                     engine, as one <orchestra-engine> block in ~/.claude/CLAUDE.md
  --list             what the built-in pipelines are and what stages they run
  --graph            a Mermaid routing graph of a pipeline (paste into GitHub or
                     mermaid.live to see the flow — including the loop-backs)
  README.md          usage, options, and the built-in pipelines in detail
  UNDERSTANDING.md   the whole system from zero context: why it's built
                     this way, and how the execution model works
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="orchestra-engine",
        description="Run a task through a staged agent pipeline: each stage is its own "
        "agent session, handing its summary and session ID to the next.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Describe the built-in pipelines and their stages, then exit. With "
        "--pipeline, describe just that one (a path works too). Runs nothing.",
    )
    parser.add_argument(
        "--graph",
        action="store_true",
        help="Print a Mermaid routing graph of the pipeline(s) and exit (read-only). "
        "With --pipeline, just that one. Paste it into any Mermaid renderer or a "
        "GitHub Markdown file to see the flow.",
    )
    parser.add_argument(
        "--install-guidance",
        action="store_true",
        help="Install this engine's agent guidance into your global Claude Code "
        "memory as one <orchestra-engine> block, then exit. Idempotent: re-running "
        "replaces the block in place and leaves the rest of the file alone. Run "
        "it once per machine so an agent knows how to drive the engine.",
    )
    parser.add_argument(
        "--print-guidance",
        action="store_true",
        help="Print the <orchestra-engine> guidance block to stdout and exit, writing "
        "nothing — to review it, or to paste it somewhere else yourself.",
    )
    parser.add_argument(
        "--claude-md",
        type=Path,
        default=None,
        metavar="PATH",
        help="Which memory file --install-guidance writes to (default: "
        "~/.claude/CLAUDE.md). Point it at a repo's ./CLAUDE.md to scope the "
        "guidance to one project instead.",
    )
    parser.add_argument(
        "task",
        nargs="?",
        default=None,
        help="The task, in plain language. If omitted at a terminal, the setup "
        "wizard asks for it.",
    )
    parser.add_argument(
        "-y",
        "--defaults",
        action="store_true",
        help="Skip the setup wizard; use flags and defaults as-is (for scripting).",
    )
    parser.add_argument(
        "--pipeline",
        default=None,
        help=f"Pipeline name ({', '.join(available_pipelines())}) or a path "
        "to a directory of numbered stage prompts. The wizard asks when it "
        "runs; required in scripted runs.",
    )
    parser.add_argument(
        "--pipelines-dir",
        action="append",
        metavar="DIR",
        help="A directory of pipeline folders to discover by name, on top of the "
        f"built-ins (repeatable; also settable via ${PIPELINES_PATH_ENV}). Lets an "
        "overlay repo add pipelines without copying the engine.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
        help="Directory the agents work in (default: ./workspace).",
    )
    parser.add_argument(
        "--permission-mode",
        default=None,
        choices=["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"],
        help="Agent permission mode (default: auto). bypassPermissions auto-approves "
        "every tool call, so it asks you to confirm before starting; pass "
        "--dangerously-skip-permissions to confirm up front instead.",
    )
    parser.add_argument(
        "--setting-sources",
        default=None,
        metavar="LIST",
        help="Which settings layers each stage agent loads: a comma-separated "
        "subset of user,project,local — or 'none' (default: project). 'user' is "
        "off by default on purpose: your ~/.claude/CLAUDE.md is written for your "
        "own sessions, and inside a stage it becomes an unasked-for "
        "co-instruction. Add it back only when a pipeline needs something that "
        "lives there, such as a user-level MCP server.",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        help="Confirm bypassPermissions up front, without the interactive prompt, so "
        "unattended runs don't stall on it. Implies --permission-mode "
        "bypassPermissions: agents get unreviewed file and shell access to the "
        "workspace. Only point this at a workspace you can afford to lose.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Human-in-the-loop mode: agents can ask you questions (AskUserQuestion), "
        "non-auto-approved tool calls need your y/n, and you choose run/skip/quit "
        "at each stage. The arrow-key menus need a terminal; over a pipe it falls "
        "back to numbered prompts. To drive it from another process, add --channel.",
    )
    parser.add_argument(
        "--channel",
        type=Path,
        default=None,
        metavar="DIR",
        help="Drive the run from another process instead of stdin. The engine "
        "writes one JSON event per line to DIR/events.jsonl — stages starting "
        "and finishing, escalations as they're raised, every prompt with an id — "
        "and every prompt (gates, agent questions, permission y/n) waits for an "
        "answer given with --answer. Use a fresh DIR per run, outside the "
        "workspace and the runs/docs dirs: whoever writes it answers the "
        "permission prompts.",
    )
    parser.add_argument(
        "--answer",
        nargs=2,
        metavar=("ID", "VALUE"),
        default=None,
        help="Answer the prompt waiting on --channel DIR: an option's number or "
        "value, comma-separated numbers for a checkbox, or free text. Refused "
        "unless ID is the prompt actually waiting, so a stale reply can't land "
        "on the wrong question.",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Terminal only: don't open the live view. By default a run opens it "
        "in a window of its own (a browser tab on Linux), showing thinking, messages and tool calls as a "
        "chat, with prompts answered by button; this turns it off (CI, SSH). "
        "Prompts then go to stdin, unless --channel is given.",
    )
    parser.add_argument(
        "--view",
        nargs="?",
        const=True,
        default=None,
        metavar="DIR",
        help="Open the live view: every run, running and finished, on one page. "
        "With DIR, a channel directory from elsewhere is added to it first.",
    )
    parser.add_argument("--serve-viewer", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--app-window", nargs="?", const=True, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Print --channel DIR's events as they happen, one line each, and "
        "exit when the run ends (or its engine dies). Can be started before the "
        "run. Made for a monitoring tool to stream.",
    )
    parser.add_argument(
        "--no-stage-gates",
        action="store_true",
        help="In interactive mode, don't ask run/skip/quit before each stage — "
        "just run the pipeline through. Agents can still ask you questions and "
        "non-auto-approved tool calls still need your y/n; only the per-stage "
        "gate goes away. Use it when you're present to do the work rather than "
        "to supervise the walk, and the gate is only ceremony between you and it.",
    )
    parser.add_argument(
        "--stages",
        default=None,
        help="Comma-separated subset of stages to run, e.g. --stages plan,implement "
        "(pipeline order is preserved).",
    )
    parser.add_argument(
        "--repeat",
        default=None,
        metavar="STAGE=N,...",
        help="Override stage repeat counts, e.g. --repeat search=5,deep-dive=3. "
        "Defaults come from each stage file's frontmatter.",
    )
    parser.add_argument(
        "--max-items",
        default=None,
        metavar="STAGE=N,...",
        help="Override fan-out item caps, e.g. --max-items deep-dive=10.",
    )
    parser.add_argument("--max-turns", type=int, default=None, help="Turn cap per stage.")
    parser.add_argument(
        "--max-hops",
        type=int,
        default=None,
        help="Cap on total stage executions when stages route the run back on "
        "themselves (default: 2x the stage count). Once reached, the run jumps "
        "to the final stage. A clean forward run never approaches it.",
    )
    parser.add_argument("--model", default=None, help="Model override for all stages.")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("orchestra-docs/runs"),
        help="Where each run's directory goes — stage artifacts and the JSON "
        "log (default: ./orchestra-docs/runs).",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Continue inside an existing run directory instead of making a new "
        "one, so a run that ended on a question can be answered without "
        "re-deriving what it already worked out. Pair with --stages to re-run "
        "only what the answer changes. Must live under --runs-dir.",
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=Path("orchestra-docs/plans"),
        help="Where a pipeline's human-facing deliverable goes — the document "
        "you actually read, kept out of the timestamped run directory and named "
        "for its subject so it is still findable next month. Reaches a stage as "
        "{docs}, and only the stages that ask for it may write there (default: "
        "./orchestra-docs/plans).",
    )
    return parser.parse_args()


_BYPASS_WARNING = """
===== WARNING: bypassPermissions =====
Every tool call runs unreviewed — no gate, no y/n, no questions. Agents can
read, write, and delete files and run shell commands in:
  {workspace}
Only run this against a workspace you can afford to lose.
"""


_SETTING_SOURCES = ("user", "project", "local")


def resolve_setting_sources(raw: str | None) -> tuple[str, ...]:
    """Parse --setting-sources. None keeps the engine default; 'none' loads
    nothing; otherwise a comma-separated subset of user,project,local."""
    if raw is None:
        return DEFAULT_SETTING_SOURCES
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if [p.lower() for p in parts] == ["none"]:
        return ()
    unknown = [p for p in parts if p not in _SETTING_SOURCES]
    if unknown or not parts:
        sys.exit(
            f"--setting-sources: expected 'none' or a comma-separated subset of "
            f"{','.join(_SETTING_SOURCES)}, got {raw!r}"
        )
    return tuple(parts)


def resolve_permission_mode(args) -> str:
    """--dangerously-skip-permissions both selects bypassPermissions and
    confirms it, so it can't quietly contradict an explicit --permission-mode."""
    if args.dangerously_skip_permissions:
        if args.permission_mode not in (None, "bypassPermissions"):
            sys.exit(
                f"--dangerously-skip-permissions conflicts with --permission-mode "
                f"{args.permission_mode}: it only confirms bypassPermissions."
            )
        return "bypassPermissions"
    return args.permission_mode or "auto"


async def confirm_bypass(workspace: Path) -> None:
    """Warn about bypassPermissions and make the human say so. Exits otherwise.

    Fails closed: a run that can't ask must not proceed unreviewed."""
    print(_BYPASS_WARNING.format(workspace=workspace))
    try:
        decision = await ui.select(
            "Run with every tool call auto-approved?",
            # Cancel first: a stray Enter on the menu must not enable bypass.
            [("no — cancel", "no"), ("yes — bypass all permission checks", "yes")],
            kind=channel.PERMISSION,
        )
    except EOFError:
        sys.exit(
            "bypassPermissions needs confirming, but there's nothing to ask on "
            "(stdin is closed). Pass --dangerously-skip-permissions to confirm up front."
        )
    if decision != "yes":
        sys.exit("cancelled")


def apply_flag_overrides(pipeline, args):
    """Apply --repeat / --max-items / --stages to a loaded pipeline.

    Overrides go first so counts may target stages that --stages then drops."""
    pipeline = apply_overrides(pipeline, args.repeat_map, args.max_items_map)
    if args.stages:
        terminal = pipeline.stages[-1].name if pipeline.stages else None
        wanted = [s.strip() for s in args.stages.split(",") if s.strip()]
        pipeline = filter_stages(pipeline, wanted)
        # The last stage standing is the terminal: it always runs, never routes,
        # and is where the pipeline writes its handback. Dropping the real one
        # promotes whichever stage now ends the list, which then stops steering
        # and starts concluding — so the subset runs, and the run ends with no
        # document. Worth a word, not an error: a deliberate subset is a fine
        # thing to ask for.
        if terminal and pipeline.stages and pipeline.stages[-1].name != terminal:
            print(
                f"note: --stages drops '{terminal}', which normally ends this pipeline "
                f"and writes the handback. '{pipeline.stages[-1].name}' becomes the "
                f"terminal, so it will run once and its routing decision is ignored."
            )
    return pipeline


def resolve_pipeline(args, wizard_mode: bool):
    """The pipeline to start with, or None to let the wizard ask.

    There is no default pipeline. With no wizard to ask and no --pipeline to
    answer, a run has nothing to say which workflow the caller meant — so it
    stops. Guessing here would run a whole pipeline nobody named."""
    if not args.pipeline and wizard_mode:
        return None
    if not args.pipeline:
        sys.exit(
            "--pipeline is required in scripted runs "
            f"(available: {', '.join(available_pipelines())})"
        )
    try:
        return apply_flag_overrides(load_pipeline(args.pipeline), args)
    except ValueError as exc:
        sys.exit(str(exc))


def describe_pipelines(names: list[str]) -> str:
    """Render pipelines and their stage shapes — the shapes say most of it."""
    lines = []
    for name in names:
        pipeline = load_pipeline(name)
        directory = resolve_pipeline_dir(name)
        group = pipeline_group(directory)
        tag = f"  [group: {group}]" if group else ""
        lines.append(f"\n{pipeline.name}{tag}  ({directory})")
        for stage in pipeline.stages:
            lines.append(f"  {stage_signature(stage)}")
    lines.append(
        "\nEach stage above is one agent session, run in order, handing its "
        "summary\nand session ID to the next. Stage prompts are the .md files in "
        "the\ndirectory shown by each pipeline — read them to see exactly what "
        "each one does."
    )
    return "\n".join(lines)


def graph_pipelines(names: list[str]) -> str:
    """Render each pipeline as a fenced Mermaid block, ready to paste anywhere."""
    blocks = []
    for name in names:
        blocks.append(f"```mermaid\n{pipeline_mermaid(load_pipeline(name))}\n```")
    return "\n\n".join(blocks)


def _apply_pipelines_dir(dirs: list[str] | None) -> None:
    """Fold --pipelines-dir into $ORCHESTRA_PIPELINES_PATH so pipeline.py has one source.

    Flag dirs lead the existing env value, so a flag beats the environment."""
    if not dirs:
        return
    existing = os.environ.get(PIPELINES_PATH_ENV, "")
    parts = [*dirs, *([existing] if existing else [])]
    os.environ[PIPELINES_PATH_ENV] = os.pathsep.join(parts)


_GUIDANCE_DONE = """\
{status}: {target}
  the <orchestra-engine> block is now {tense} your Claude Code memory — an agent
  reading it knows to launch pipelines rather than read them, to drive -i
  over --channel, and that permission gates are yours to answer, not its.
  Nothing else in that file was touched. Re-run after changing the guidance."""


def install_guidance(args) -> None:
    """--install-guidance / --print-guidance: touch one file, run nothing."""
    if args.print_guidance:
        print(guidance.render_block(), end="")
        return
    target = args.claude_md or guidance.default_target()
    try:
        status = guidance.install(target)
    except OSError as exc:
        sys.exit(f"couldn't write {target}: {exc}")
    tense = "already in" if status == "unchanged" else "in"
    print(_GUIDANCE_DONE.format(status=status, target=target, tense=tense))


def channel_command(args) -> None:
    """--answer / --watch: the driver's side of a channel. Runs no agents."""
    if args.channel is None:
        sys.exit("--answer and --watch need --channel DIR")
    if args.watch:
        try:
            for line in channel.watch(args.channel):
                print(line, flush=True)
        except KeyboardInterrupt:
            pass
        return
    prompt_id, value = args.answer
    try:
        prompt = channel.answer(args.channel, prompt_id, value)
    except channel.ChannelError as exc:
        sys.exit(f"--answer: {exc}")
    print(f"answered {prompt_id} ({prompt['kind']}): {value}")


def _default_channel(args) -> Path:
    """Where a run's channel goes when none was named: the engine's home
    folder, unless a stage could write there (a workspace at or above home),
    in which case a private temporary directory, which never can."""
    directory = viewer.home() / "runs" / args.run_id
    for root in (args.workspace, args.runs_dir, args.docs_dir):
        root = Path(root).resolve()
        if directory.is_relative_to(root) or root.is_relative_to(directory):
            return Path(tempfile.mkdtemp(prefix="orchestra-channel-"))
    return directory


# A run another process drives doesn't wait on the page, so it can afford to
# keep looking for the viewer far longer than a run whose prompts depend on it.
BACKGROUND_VIEWER_SECONDS = 300


def open_view(run_id: str | None = None, background: bool = False) -> bool:
    """Make sure the one viewer is up and a page is showing it. Opens the app
    window (a browser tab where there is none) only when no page is open
    already: a second run appears in the open page's sidebar instead of in a
    window of its own. False if it couldn't start.

    With `background`, the waiting happens on a thread and this returns at
    once: the page opens whenever the viewer answers, however long it takes."""
    if background:
        threading.Thread(target=_open_view, args=(run_id, BACKGROUND_VIEWER_SECONDS), daemon=True).start()
        return True
    return _open_view(run_id)


def _open_view(run_id: str | None, wait: float = viewer.VIEWER_START_SECONDS) -> bool:
    try:
        info, page_open = viewer.ensure_viewer(timeout=wait)
    except (OSError, RuntimeError) as exc:
        where = "" if wait == BACKGROUND_VIEWER_SECONDS else "; continuing in the terminal"
        print(f"Live view unavailable ({exc}){where}. `./orchestra --view` opens it later.")
        return False
    url = viewer.viewer_url(info, run_id)
    print(f"Live view: {url}")
    if not page_open and not window.open_window(run_id):
        webbrowser.open(url)
    return True


def view_command(target) -> None:
    """--view [DIR]: open the page, adding DIR's run to it if given."""
    run_id = None
    if target is not True:
        directory = Path(target).expanduser().resolve()
        if not (directory / channel.EVENTS_FILE).exists():
            sys.exit(f"--view {target}: no {channel.EVENTS_FILE} there — not a run's channel")
        run_id = viewer.new_run_id()
        viewer.register(run_id, directory)
    if not open_view(run_id):
        sys.exit(1)


def main() -> None:
    args = parse_args()
    _apply_pipelines_dir(args.pipelines_dir)
    if args.answer or args.watch:
        channel_command(args)
        return
    if args.serve_viewer:
        viewer.serve_forever()
        return
    if args.app_window is not None:
        window.run_window(None if args.app_window is True else args.app_window)
        return
    if args.view is not None:
        view_command(args.view)
        return
    if args.install_guidance or args.print_guidance:
        # Before anything else: this writes one memory file and runs no agents.
        install_guidance(args)
        return
    if args.list or args.graph:
        # Before the wizard, the workspace, and any session: these only read.
        render = graph_pipelines if args.graph else describe_pipelines
        try:
            print(render([args.pipeline] if args.pipeline else available_pipelines()))
        except ValueError as exc:
            sys.exit(str(exc))
        return
    try:
        args.repeat_map = parse_stage_counts(args.repeat)
        args.max_items_map = parse_stage_counts(args.max_items)
    except ValueError as exc:
        sys.exit(f"--repeat/--max-items: {exc}")
    args.permission_mode = resolve_permission_mode(args)
    args.setting_sources = resolve_setting_sources(args.setting_sources)
    args.channel_named = args.channel is not None
    # A channel named on the command line means another process drives the
    # run, so nothing is asked on this terminal — not even the wizard.
    wizard_mode = sys.stdin.isatty() and not args.defaults and args.channel is None
    args.run_id = viewer.new_run_id()
    if not args.no_ui and args.channel is None:
        # The live view is the default interface, and it reads the channel, so
        # a run without one gets one in the engine's home folder. The wizard
        # still runs — its questions go to the page like every other prompt.
        args.channel = _default_channel(args)

    # Load up front when the flag decides it (or there's no wizard to ask);
    # otherwise the wizard picks the pipeline itself.
    pipeline = resolve_pipeline(args, wizard_mode)

    try:
        asyncio.run(_run(args, pipeline, wizard_mode))
    except KeyboardInterrupt:
        sys.exit("\ncancelled")
    except FanOutInterrupted as exc:
        # Expected when a usage limit lands mid-stage: no traceback, just what
        # happened and the flags that resume it.
        hint = getattr(exc, "resume_hint", None)
        sys.exit(f"\n{exc}\nResume with the same command plus: {hint}" if hint else f"\n{exc}")


async def _run(args, pipeline, wizard_mode: bool) -> None:
    if args.channel is not None:
        # First of all, so even the bypass confirmation below is asked over it.
        try:
            channel.open_channel(
                args.channel,
                forbidden={
                    "--workspace": args.workspace,
                    "--runs-dir": args.runs_dir,
                    "--docs-dir": args.docs_dir,
                },
            )
        except channel.ChannelError as exc:
            sys.exit(str(exc))
        print(f"Channel: {args.channel}")
        viewer.register(args.run_id, args.channel)
        if args.no_ui:
            pass
        elif args.channel_named:
            # Another process drives this channel, so nothing here waits on the
            # page: the run starts now, and the page opens when the viewer is up.
            open_view(args.run_id, background=True)
        elif not open_view(args.run_id):
            # Nobody can see the page, and nobody else drives this channel:
            # put the prompts back on this terminal rather than wait forever.
            channel.close_channel()
    try:
        await _run_open(args, pipeline, wizard_mode)
    except BaseException as exc:
        reason = "finished" if isinstance(exc, SystemExit) and not exc.code else repr(exc)
        channel.emit("engine_exited", reason=reason)
        raise
    channel.emit("engine_exited", reason="finished")
    channel.close_channel()


async def _run_open(args, pipeline, wizard_mode: bool) -> None:
    # Before the wizard and before any session: the scariest flag gets settled first.
    if args.permission_mode == "bypassPermissions":
        if args.dangerously_skip_permissions:
            print(_BYPASS_WARNING.format(workspace=args.workspace.resolve()))
        else:
            await confirm_bypass(args.workspace.resolve())

    if wizard_mode:
        task, pipeline, interactive = await wizard.configure(args, pipeline)
    else:
        if not args.task:
            sys.exit("task is required (or run from a terminal for the setup wizard)")
        task, interactive = args.task, args.interactive

    print(
        f"\nPipeline: {pipeline.name} "
        f"({' -> '.join(stage_signature(s) for s in pipeline.stages)})"
    )
    try:
        results = await run_loop(
            task=task,
            workspace=args.workspace.resolve(),
            pipeline=pipeline,
            permission_mode=args.permission_mode,
            max_turns=args.max_turns,
            model=args.model,
            interactive=interactive,
            stage_gates=not args.no_stage_gates,
            max_hops=args.max_hops,
            setting_sources=args.setting_sources,
            runs_dir=args.runs_dir.resolve(),
            run_dir=args.run_dir,
            docs=args.docs_dir.resolve(),
        )
    except ValueError as exc:
        # --run-dir didn't name a run directory this pass can continue, and no
        # session has started yet. Say which and stop, rather than minting a
        # fresh run the caller didn't ask for.
        sys.exit(str(exc))

    print("\n===== RUN COMPLETE =====")
    for r in results:
        status = "ERROR" if r.is_error else "ok"
        cost = f"${r.cost_usd:.4f}" if r.cost_usd is not None else "n/a"
        name = r.stage
        if r.iteration is not None:
            name += f" #{r.iteration}"
        if r.item is not None:
            item = r.item if len(r.item) <= 30 else r.item[:29] + "…"
            name += f" ({item})"
        print(f"  {name:<24} [{status}]  session={r.session_id}  turns={r.num_turns}  cost={cost}")


if __name__ == "__main__":
    main()
