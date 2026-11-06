"""One agent session, start to finish.

This is the engine's SDK boundary: the only engine module that imports
claude_agent_sdk. Everything above it deals in prompts in, StageResult out.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator, Callable

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    Message,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from .. import channel, handoff_tool, interaction
from ..model import StageResult
from .runlog import ESCALATIONS_FILE


def default_log(line: str) -> None:
    """Print a line, and never take the run down doing it.

    Stage output is agent-written and streams through here — thinking blocks
    carry a 💭 prefix, and the text itself can hold anything the model typed. A
    console that cannot encode one of those characters raises UnicodeEncodeError
    from `print`, and because this is called from inside the message loop the
    stage dies mid-answer; all three retries then die at the same block. That is
    the Windows default below Python 3.15, where stdout is cp1252 and the
    thinking prefix alone is enough to do it.

    So the unencodable characters are replaced rather than allowed to raise. The
    fast path is unchanged: this only costs anything on a line that would
    otherwise have crashed the run.
    """
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(encoding, "replace").decode(encoding, "replace"), flush=True)


def _handoff_server(body: str | None):
    """An in-process MCP server serving this stage's incoming handoffs, or None
    when there are none — the first stage of a run gets no server and no tool.

    The tool closes over text the engine already holds: it reads no file, opens
    no socket, and takes no argument, so a caller cannot point it anywhere. The
    body keeps the pipeline's ``--- HANDOFF ---`` fences, which matter more
    through this channel than the old one: the text is written by a previous
    agent and can contain anything that session emitted, and the whole point of
    moving it here is that it arrives marked as somebody's account rather than
    as this stage's instructions.
    """
    if not body:
        return None

    @tool(handoff_tool.TOOL_NAME, handoff_tool.DESCRIPTION, {})
    async def read_handoff(_args: dict) -> dict:
        return {"content": [{"type": "text", "text": body}]}

    return create_sdk_mcp_server(
        name=handoff_tool.SERVER_NAME, version="1.0.0", tools=[read_handoff]
    )


# The field that best says "what" a tool call is doing, in priority order.
# Turns a bare "-> tool: Read" into "-> tool: Read  (path/to/file)" so someone
# watching the live stream sees the step, not just the tool's name.
_TOOL_DETAIL_KEYS = (
    "file_path",   # Read / Write / Edit / NotebookEdit
    "command",     # Bash
    "pattern",     # Grep / Glob
    "query",       # ToolSearch / WebSearch
    "url",         # WebFetch
    "description",  # Task / Agent
    "prompt",      # Agent / Skill
    "path",        # fallback
)


def _tool_detail(tool_input: object, limit: int = 100) -> str:
    """A single-line hint of what a tool call is doing. Short for the terminal;
    the live view asks for more, since it trims to fit visually and needs whole
    paths to show them relative to the workspace."""
    if not isinstance(tool_input, dict):
        return ""
    for key in _TOOL_DETAIL_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            detail = " ".join(val.split())  # collapse newlines so it stays one line
            return detail if len(detail) <= limit else detail[: limit - 1] + "…"
    return ""


# A tool result can be a whole file or a build log. The live view shows the top
# of it, which is what a watcher reads; the transcript keeps the rest.
_RESULT_PREVIEW_CHARS = 4000


def _result_text(content: object) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        text = ""
    text = text.strip()
    if len(text) > _RESULT_PREVIEW_CHARS:
        text = text[:_RESULT_PREVIEW_CHARS] + f"\n… ({len(text) - _RESULT_PREVIEW_CHARS} more characters)"
    return text


def _emit_block(block: object, stage: str, label: str) -> None:
    """The same block _render_block prints, as a structured event for the live
    view: it shows thinking, text and tool calls as a chat, not as log lines."""
    base = {"stage": stage, "label": label}
    if isinstance(block, ThinkingBlock) and block.thinking.strip():
        channel.emit("message", type="thinking", text=block.thinking.strip(), **base)
    elif isinstance(block, TextBlock) and block.text.strip():
        channel.emit("message", type="text", text=block.text.strip(), **base)
    elif isinstance(block, ToolUseBlock):
        channel.emit(
            "message", type="tool", tool=block.name, detail=_tool_detail(block.input, limit=2000),
            tool_use_id=block.id, **base,
        )
    elif isinstance(block, ToolResultBlock):
        channel.emit(
            "message", type="tool_result", text=_result_text(block.content),
            is_error=bool(block.is_error), tool_use_id=block.tool_use_id, **base,
        )


def _render_block(block: object, label: str) -> str | None:
    """The live-log line for one content block, or None if it carries nothing
    worth showing.

    Thinking is rendered alongside text and tool calls, not dropped: someone
    watching the stream is here to learn how the run reasons while it waits, and
    a stream that shows only the tool calls and the final answer hides exactly
    the part worth learning from. It's marked distinctly (💭) so the reasoning
    reads as reasoning, not as the stage's output — and it stays display-only:
    the loop never folds thinking into the summary handed to the next stage.
    """
    if isinstance(block, ThinkingBlock) and block.thinking.strip():
        return f"[{label}] 💭 {block.thinking.strip()}"
    if isinstance(block, TextBlock) and block.text.strip():
        return f"[{label}] {block.text.strip()}"
    if isinstance(block, ToolUseBlock):
        detail = _tool_detail(block.input)
        return f"[{label}] -> tool: {block.name}" + (f"  ({detail})" if detail else "")
    return None


# Agents dropped into an empty workspace inside a larger git repo tend to
# orient themselves to the enclosing repo root; this preamble pins them to
# the two directories they may write to, and says what belongs in each.
#
# It also carries the one instruction every stage of every pipeline needs and
# no pipeline should have to repeat: how to record that the run is delivering
# less than it was asked for. The engine already protects and reports that file
# whoever writes it, so leaving the instruction to the prompts meant only the
# pipeline that remembered to ask for it had the channel at all.
WORKSPACE_PREAMBLE = """\
You may write to exactly two directories, and they are not interchangeable.

1. The workspace — {workspace}
   Treat this as the project root. It holds the deliverable, and only the
   deliverable: source changes, and tests that ship with the project.

2. The artifacts directory — {artifacts}
   This holds the stage documents: what you write for the next stage to read,
   and what a human reads afterwards to see how the run reasoned. Every
   document a stage prompt names (REPRO.md, verdict.txt, and so on) belongs
   here, at this exact path.

Do not write stage documents into the workspace. The workspace is someone's
real repository, and notes left in it show up in their `git status` mixed in
with the change they actually asked for.

Never write anywhere else — not to parent directories, and not to the
enclosing git repository if one exists above the workspace. Nothing outside
those two directories is part of this run.

One document in the artifacts directory is different: {escalations}. Append to
it when this run stops promising something it was asked for — a behaviour
withdrawn, a criterion narrowed to less than it stated, a stated requirement you
built around rather than met, a case that was asserted and no longer is. Write
what was given up, what it costs, and which part of the request it belonged to.

Append; never edit or replace what is already there. A later stage that
disagrees with an entry appends its case underneath, and the engine puts back
anything removed — so the one outcome unavailable to you is leaving the run
looking like nothing was given up. Most stages have nothing to write here and an
absent file is the normal case: this is for the moment the run delivers less than
was asked of it, not a log of what you weighed along the way.

Ending your turn ends this stage. The message you send without a tool call is your
report, and the run moves on. So don't end your turn until the stage's work is
done, and never end it to wait: not on "waiting for X to start", and not on a
background command or a notification you expect to wake you. A session woken that
way can find its calls refused as if someone had said no, when nobody was asked,
and a command still running some minutes after your turn ends gets stopped. Either
way the stage fails.

To wait for something, stay in your turn and run one foreground shell command that
blocks until it is true, such as `until <check>; do sleep 2; done`, with a timeout
long enough for it. This holds even where a tool's own description points you at a
background command or a later notification instead. Anything you do start in the
background is yours to finish or stop before your last message.

"""

INTERACTIVE_PREAMBLE = """\
A human operator is present at the terminal. When you face a decision the
task does not settle (ambiguous requirement, scope question, a choice between
meaningfully different approaches), use the AskUserQuestion tool to ask them
instead of guessing. Keep questions short and concrete.

"""

# Which settings layers a stage session loads. The SDK passes this straight to
# --setting-sources; leaving it unset lets the CLI load its own default, which
# includes the operator's ~/.claude/CLAUDE.md.
#
# That default is wrong here, and quietly so. A stage's instruction is its
# prompt — that is the whole premise of a fixed decomposition. An operator's
# personal global memory is written for their interactive sessions and lands in
# every stage as an unasked-for co-instruction: a mandate to call some MCP tool
# before writing code turns a 2-turn stage into 7 and can walk it into the turn
# cap. Worst of all is this engine's own driver guidance, which
# --install-guidance writes into that same file: it tells an agent to launch
# pipelines and to hand permission decisions back to the human, so a stage
# inheriting it tries to defer instead of doing its work — the loop stops
# proceeding on its own.
#
# "project" is kept because it resolves against cwd, which is the workspace:
# that is the target repo describing its own conventions to an agent working on
# it, which is context about the deliverable rather than instructions from
# somewhere else. "user" is what gets dropped.
DEFAULT_SETTING_SOURCES = ("project",)


def _streaming_prompt(prompt: str, release: asyncio.Event):
    """The prompt as a stream that stays open until `release` is set. The SDK won't
    close the session's stdin while the stream is open, and once it ends the SDK
    decides as it always did: at once, or when the run is over if something still
    needs answering. See _ShellWait for why the engine holds it."""

    async def gen():
        yield {"type": "user", "message": {"role": "user", "content": prompt}}
        await release.wait()

    return gen()


# A dropped connection mid-stage used to kill the whole run: one bad packet in
# stage 4 of 6 threw away every completed session. Transport faults are worth
# retrying; these are not.
#   - buffer size: the SDK caps a single JSON message at 1MB. An agent that
#     read something huge will read it again — retrying just re-runs an
#     expensive stage into the same wall. Raise max_buffer_size instead.
#   - auth: retrying without credentials fails identically, three times.
#   - turn cap: the stage ran out of turns doing whatever it was doing. A
#     retry starts from zero turns and walks into the same cap, three times,
#     at full price. Raise --max-turns instead.
PERMANENT_ERROR_MARKERS = (
    "exceeded maximum buffer size",
    "authentication",
    "invalid api key",
    "maximum number of turns",
)
MAX_ATTEMPTS = 3

# How long a headless session waits for background tasks it started (a
# background sub-agent, most often) before the CLI terminates them. The CLI's
# own ceiling is 600s; a stage that hands a long walk to a background agent and
# ends its turn waiting on it was killed at that ceiling with its work unwritten,
# and the engine walked on to a stage whose input never arrived. Two hours, not
# unlimited: a background task that hangs must still end the stage eventually.
# Set the variable in the engine's own environment to change it.
BG_WAIT_ENV = "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"
BG_WAIT_DEFAULT_MS = str(2 * 60 * 60 * 1000)
RETRY_BACKOFF_SECONDS = 2

# What the CLI answers a tool call with when it could not get a permission
# decision, word for word from its bundle (test_session_wake.py checks the bundle
# still says it). It tells the agent a user refused and to stop.
#
# In a stage that is never true. A real refusal comes through can_use_tool with
# the engine's own wording, and an unattended stage has no user at all. Seen live,
# it comes from this sequence: a stage starts a background command and ends its
# turn; the SDK sees the result, keeps no hold for a shell (it only holds for
# delegated agents) and closes the session's stdin; the command finishes and wakes
# the session for another turn. Permission requests and hooks both travel over
# that stdin, so in an interactive stage every tool call of the woken turn, a plain
# Read included, is refused within milliseconds with no prompt shown. The agent
# obeys and stops, and the CLI reports success. _ShellWait now keeps stdin open
# through that sequence, so this is what's left for a wake it couldn't hold for.
#
# A second session start alone doesn't mean this. A background sub-agent wakes the
# session the same way, but the SDK holds stdin open for it and its follow-up turn
# works, so the refusal is what fails a stage, not the wake.
UNASKED_REFUSAL = "The user doesn't want to take this action right now"

# How long a session may sit after ending its turn with only background commands
# running, before the engine stops them and fails the stage. Set the variable in the
# engine's own environment to change it.
#
# The CLI doesn't bound that wait in any useful way. Seen live, it holds the session
# for as long as the command runs and sends nothing at all, and BG_WAIT_ENV doesn't
# apply: at 30 s, a dev server still held the session for 30 minutes. A background
# shell has its own deadline, the `timeout` the agent gave it or 30 minutes, and the
# ceiling only counts once that has passed. At the deadline the CLI kills the command,
# wakes the session and tells it to start the command again with a longer timeout if
# it still needs it. Most did, with the two-hour maximum. So a run can sit silent for
# hours, looking exactly like a slow one.
SHELL_WAIT_ENV = "ORCHESTRA_SHELL_WAIT_SECONDS"
SHELL_WAIT_DEFAULT_SECONDS = 600.0

# A command or an agent ending usually wakes the session for another turn, and that
# turn needs stdin. Not every ending does, though, and then nothing else arrives to
# go on. So this is how long the engine waits for the turn before deciding without it.
SHELL_SETTLE_SECONDS = 15.0

# How long the CLI gets to exit after the engine stops its commands, before the SDK
# terminates it.
SESSION_EXIT_GRACE_SECONDS = 30.0

# What a command's task type is, and the ones that reach an end and wake the session
# when they do (the SDK's DEFERRING_TASK_TYPES). The CLI reports other kinds of task
# too, some of them its own housekeeping, and none of those change anything here.
SHELL_TASK_TYPE = "local_bash"
AGENT_TASK_TYPES = ("local_agent", "local_workflow")

# The SDK caps a single JSON message at 1MB, which a fan-out agent reading a
# large artifact blows past — taking the whole run with it. Agents crawl PDFs
# and clone repos, so a tool result over 1MB is ordinary, not pathological.
# Kept bounded rather than removed: a fan-out runs up to FAN_OUT_CONCURRENCY
# sessions at once, so the real ceiling is this times that.
MAX_BUFFER_BYTES = 16 * 1024 * 1024


def _is_permanent(exc: Exception) -> bool:
    """Whether retrying this error would only reproduce it.

    The SDK raises a bare Exception from its transport error channel with no
    code to switch on, so the message text is the only signal available.
    Unrecognized errors are treated as transient: a wasted retry costs less
    than a discarded run, and the final attempt re-raises either way.
    """
    message = str(exc).lower()
    return any(marker in message for marker in PERMANENT_ERROR_MARKERS)


def _refused_unasked(block: object) -> bool:
    """Whether this tool result is the CLI refusing a call nobody was asked about."""
    return (
        isinstance(block, ToolResultBlock)
        and bool(block.is_error)
        and _result_text(block.content).startswith(UNASKED_REFUSAL)
    )


def _refusal_report(refused: list[str], woken: bool) -> str:
    """Why a stage that reported success is being failed, for the run log and the
    channel: the session's own last message usually reads like a calm stop."""
    tools = ", ".join(dict.fromkeys(refused))
    report = (
        f"STAGE FAILED: {len(refused)} tool call(s) ({tools}) were refused with no "
        f"one asked. The agent was told a user didn't want the action and to stop, "
        f"but no prompt was ever shown, so the stage's work stopped there."
    )
    if woken:
        report += (
            " The session had ended its turn and was woken afterwards, most likely by "
            "a background command finishing. By then its request stream had closed, "
            "so nothing could answer its tool calls."
        )
    return report


def _shell_wait_seconds() -> float:
    """SHELL_WAIT_ENV as a number of seconds. A typo falls back to the default rather
    than taking the run down or turning the limit off."""
    raw = os.environ.get(SHELL_WAIT_ENV)
    if raw is None:
        return SHELL_WAIT_DEFAULT_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        return SHELL_WAIT_DEFAULT_SECONDS
    return seconds if 0 <= seconds < float("inf") else SHELL_WAIT_DEFAULT_SECONDS


class _ShellWait:
    """Watches the session's background commands, and stops one it left running.

    Ending the session from out here isn't enough. Terminating the CLI on Windows
    leaves the command running, still holding whatever it holds, such as a port. The
    clean way out is the SDK's stop_task: the CLI kills the command and can exit. That
    goes over the session's stdin, which the SDK closes as soon as a turn ends if it's
    left to it. So the engine holds the prompt stream open past a turn that left only
    background commands running. A command that finishes in time then wakes a session
    whose tools still work, which is the other thing the closed stdin used to break.

    Background agents are left to the SDK and the CLI, as they always were: they reach
    an end, wake the session when they do and fall under BG_WAIT_ENV. So stdin is
    released for them, and the clock only runs for a command with no agent still
    working, since the agent may be using it. If a command then outlasts the agent's
    follow-up turn, stdin can be gone and stop_task with it. The session is ended
    anyway, and the report says the command may still be running.
    """

    def __init__(self, stage: str, label: str, log: Callable[[str], None]) -> None:
        self.stage = stage
        self.label = label
        self.log = log
        self.limit = _shell_wait_seconds()
        self.release = asyncio.Event()
        self.left_running: list[str] = []
        self.not_stopped: list[str] = []
        self._commands: dict[str, str] = {}
        self._running: dict[str, TaskStartedMessage] = {}
        self._left_since: dict[str, float] = {}
        self._between_turns = False
        self._settle_at: float | None = None
        self._exit_by: float | None = None

    def saw(self, message: Message) -> None:
        if isinstance(message, TaskStartedMessage):
            self._running[message.task_id] = message
        elif isinstance(message, TaskNotificationMessage):
            self._ended(message.task_id)
        elif isinstance(message, TaskUpdatedMessage):
            if message.status in TERMINAL_TASK_STATUSES:
                self._ended(message.task_id)
        elif isinstance(message, ResultMessage):
            self._turn_ended()
        elif isinstance(message, SystemMessage):
            if message.subtype == "init":
                self._turn_started()
        elif isinstance(message, (AssistantMessage, UserMessage)):
            # A background agent's own messages come through between the session's turns.
            if message.parent_tool_use_id is None:
                self._turn_started()
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    # Bash, PowerShell and Monitor all report as a shell.
                    if isinstance(block, ToolUseBlock):
                        self._commands[block.id] = _tool_detail(block.input, limit=200)

    def next_look(self) -> float | None:
        """When act() next has something to do, or None if only a frame can change that."""
        if self._exit_by is not None:
            return self._exit_by
        if not self._between_turns:
            return None
        if self._settle_at is not None:
            return self._settle_at
        if self._left_since and not self._agents():
            return min(self._left_since.values()) + self.limit
        return None

    async def act(self, client: ClaudeSDKClient) -> bool:
        """Do what next_look() was for. True means stop waiting for the CLI to exit."""
        if self._exit_by is not None:
            return True
        if self._settle_at is not None:
            self._settle_at = None
            self._decide()
            return False
        for task in self._shells():
            name = self._name(task)
            self.left_running.append(name)
            self.log(f"[{self.label}] STOPPING `{name}`: still running {self.limit:g} s after the session's turn ended.")
            try:
                await client.stop_task(task.task_id)
            except Exception as exc:
                self.not_stopped.append(name)
                self.log(f"[{self.label}] couldn't stop `{name}` ({type(exc).__name__}: {exc})")
        self.release.set()
        self._exit_by = time.monotonic() + SESSION_EXIT_GRACE_SECONDS
        return False

    def report(self) -> str:
        """Why the stage is being failed, for the run log and the channel: the session's
        own last message usually says everything went fine."""
        commands = "; ".join(f"`{name}`" for name in self.left_running)
        report = (
            f"STAGE FAILED: its session ended its turn and left {len(self.left_running)} "
            f"background command(s) running: {commands}. They were still going {self.limit:g} s "
            f"later, so the engine ended the session rather than let the run sit on them."
        )
        if self.not_stopped:
            names = "; ".join(f"`{name}`" for name in self.not_stopped)
            report += (
                f" It couldn't stop {names}, and ending a session doesn't always end its "
                f"commands, so check whether that's still running."
            )
        else:
            report += " It stopped them first, so anything that needed them has lost them."
        return (
            f"{report} A stage has to finish or stop what it starts, or wait for it with a "
            f"foreground command. {SHELL_WAIT_ENV} sets how long the engine gives them."
        )

    def _ended(self, task_id: str) -> None:
        self._running.pop(task_id, None)
        self._left_since.pop(task_id, None)
        if self._between_turns:
            self._settle_at = time.monotonic() + SHELL_SETTLE_SECONDS

    def _turn_started(self) -> None:
        self._between_turns = False
        self._settle_at = None

    def _turn_ended(self) -> None:
        self._between_turns = True
        self._settle_at = None
        self._decide()

    def _decide(self) -> None:
        """The session is between turns: let it go, or start the clock on what it left."""
        if not self._shells() or self._agents():
            self._left_since.clear()
            self.release.set()
            return
        now = time.monotonic()
        # Not restarted for a command that's already waiting: the CLI can wake the
        # session just to check on one, and that shouldn't buy the command more time.
        new = [task for task in self._shells() if task.task_id not in self._left_since]
        self._left_since.update((task.task_id, now) for task in new)
        if new:
            names = [self._name(task) for task in new]
            listed = "; ".join(f"`{name}`" for name in names)
            self.log(
                f"[{self.label}] WAITING: this session ended its turn with background command(s) "
                f"still running: {listed}. They get {self.limit:g} s, then the engine stops them "
                f"and fails the stage."
            )
            channel.emit(
                "stage_waiting", stage=self.stage, label=self.label, commands=names, limit_seconds=self.limit
            )

    def _shells(self) -> list[TaskStartedMessage]:
        return [task for task in self._running.values() if task.task_type == SHELL_TASK_TYPE]

    def _agents(self) -> list[TaskStartedMessage]:
        return [task for task in self._running.values() if task.task_type in AGENT_TASK_TYPES]

    def _name(self, task: TaskStartedMessage) -> str:
        return self._commands.get(task.tool_use_id or "") or task.description


async def _session_frames(client: ClaudeSDKClient, wait: _ShellWait) -> AsyncIterator[Message]:
    """The session's frames until the CLI exits, with _ShellWait shown each one and
    given its turn when nothing arrives in time. The read stays pending across a
    timeout, since cancelling it would end the SDK's stream."""
    frames = client.receive_messages()
    pending: asyncio.Future | None = None
    try:
        while True:
            pending = pending or asyncio.ensure_future(anext(frames))
            look_at = wait.next_look()
            timeout = None if look_at is None else max(0.0, look_at - time.monotonic())
            done, _ = await asyncio.wait({pending}, timeout=timeout)
            if not done:
                if await wait.act(client):
                    return
                continue
            try:
                message = pending.result()
            except StopAsyncIteration:
                pending = None
                return
            pending = None
            wait.saw(message)
            yield message
    finally:
        if pending is not None:
            pending.cancel()


async def run_stage(
    stage_name: str,
    prompt: str,
    workspace: Path,
    artifacts: Path,
    permission_mode: str = "auto",
    max_turns: int | None = None,
    model: str | None = None,
    interactive: bool = False,
    log: Callable[[str], None] = default_log,
    label: str | None = None,
    iteration: int | None = None,
    item: str | None = None,
    extra_add_dirs: list[str] | None = None,
    setting_sources: tuple[str, ...] = DEFAULT_SETTING_SOURCES,
    handoff_body: str | None = None,
) -> StageResult:
    """Run one agent session to completion and return its handoff data.

    A transport fault is retried up to MAX_ATTEMPTS times. Each attempt is a
    fresh session, so a retry re-runs the stage from a cold start — it will
    see whatever files the failed attempt already wrote, which is the same
    position every stage starts from anyway.

    extra_add_dirs grants read access beyond the workspace and artifacts dir —
    any stage uses it to reach the prior sessions' transcripts on disk, to pull
    a specific fact a handoff summary dropped.
    """
    label = label or stage_name
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await _run_session(
                stage_name,
                prompt,
                workspace,
                artifacts,
                permission_mode=permission_mode,
                max_turns=max_turns,
                model=model,
                interactive=interactive,
                log=log,
                label=label,
                iteration=iteration,
                item=item,
                extra_add_dirs=extra_add_dirs,
                setting_sources=setting_sources,
                handoff_body=handoff_body,
            )
        except Exception as exc:
            if attempt == MAX_ATTEMPTS or _is_permanent(exc):
                log(f"[{label}] session failed ({type(exc).__name__}: {exc})")
                raise
            delay = RETRY_BACKOFF_SECONDS ** attempt
            log(
                f"[{label}] session error on attempt {attempt}/{MAX_ATTEMPTS} "
                f"({type(exc).__name__}: {exc}) — retrying in {delay}s"
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable: the loop returns or raises on every path")


async def _run_session(
    stage_name: str,
    prompt: str,
    workspace: Path,
    artifacts: Path,
    *,
    permission_mode: str,
    max_turns: int | None,
    model: str | None,
    interactive: bool,
    log: Callable[[str], None],
    label: str,
    iteration: int | None,
    item: str | None,
    extra_add_dirs: list[str] | None = None,
    setting_sources: tuple[str, ...] = DEFAULT_SETTING_SOURCES,
    handoff_body: str | None = None,
) -> StageResult:
    """One attempt at a session. Prompt and options are built per call — the
    prompt is wrapped in a single-use async generator, so a retry that reused
    them would send an already-exhausted stream."""
    prompt = WORKSPACE_PREAMBLE.format(
        workspace=workspace, artifacts=artifacts, escalations=ESCALATIONS_FILE
    ) + prompt
    handoff_server = _handoff_server(handoff_body)
    options = ClaudeAgentOptions(
        cwd=str(workspace),
        # The artifacts dir sits outside the workspace, so cwd alone doesn't
        # reach it. Telling the agent about it in the preamble isn't enough —
        # without add_dirs the SDK denies the write and the stage fails on its
        # first attempt to hand anything to the next one. extra_add_dirs adds
        # read reach for any stage into the prior sessions' transcript dirs.
        add_dirs=[str(artifacts), *(extra_add_dirs or [])],
        # Explicit, never left to the CLI default — see DEFAULT_SETTING_SOURCES.
        setting_sources=list(setting_sources),
        permission_mode=permission_mode,
        max_turns=max_turns,
        model=model,
        max_buffer_size=MAX_BUFFER_BYTES,
        # Ask for the reasoning text, not just the answer. Opus 4.7+ defaults to
        # "omitted" — thinking blocks arrive signature-only, with no text — so
        # without this the live stream can never show how the run reasons, which
        # is the part a watching human learns from. "adaptive" lets the model
        # decide how much to think; "summarized" is what makes the text come back.
        thinking={"type": "adaptive", "display": "summarized"},
        # The previous stages' reports, served as a tool result instead of
        # spliced into the prompt above. allowed_tools here is the SDK's
        # auto-approve list, not an exclusive allowlist, so naming one tool
        # cannot narrow what else the stage may use — it only stops an
        # unattended run stalling on a confirmation prompt for the engine's own
        # data channel. In interactive mode the SDK notes that this shadows
        # can_use_tool for this tool alone; that is the intent, since a human
        # gating the handoff is gating the engine, not a capability.
        mcp_servers={handoff_tool.SERVER_NAME: handoff_server} if handoff_server else {},
        allowed_tools=[handoff_tool.QUALIFIED_TOOL] if handoff_server else [],
        env={BG_WAIT_ENV: os.environ.get(BG_WAIT_ENV, BG_WAIT_DEFAULT_MS)},
    )
    if interactive:
        prompt = INTERACTIVE_PREAMBLE + prompt
        options.can_use_tool = interaction.can_use_tool
        # can_use_tool needs streaming input plus a hook holding the stream open
        options.hooks = {
            "PreToolUse": [HookMatcher(matcher=None, hooks=[interaction.keep_stream_open])]
        }

    wait = _ShellWait(stage_name, label, log)
    session_id: str | None = None
    last_text = ""
    # One result per turn the session took, and it can take more than one: anything
    # it left running in the background wakes it again after it ended its turn.
    results: list[ResultMessage] = []
    tool_names: dict[str, str] = {}
    refused: list[str] = []

    client = ClaudeSDKClient(options)
    await client.connect(_streaming_prompt(prompt, wait.release))
    try:
        async with aclosing(_session_frames(client, wait)) as frames:
            async for message in frames:
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    session_id = message.data.get("session_id")
                    if results:
                        log(
                            f"[{label}] WOKEN: this session ended its turn and has now started "
                            f"another, so the stage was still running when it reported."
                        )
                        channel.emit("stage_woken", stage=stage_name, label=label, session_id=session_id)
                    log(f"[{label}] session started: {session_id}")
                    channel.emit("session_started", stage=stage_name, label=label, session_id=session_id)
                elif isinstance(message, UserMessage) and isinstance(message.content, list):
                    for block in message.content:
                        _emit_block(block, stage_name, label)
                        # A sub-agent's refusal is its parent's to deal with, and the parent
                        # still has its tools. Only the stage's own thread is told to stop.
                        if message.parent_tool_use_id is None and _refused_unasked(block):
                            refused.append(tool_names.get(block.tool_use_id, "unknown tool"))
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        # last_text tracks the final text only — it's the fallback
                        # summary handed to the next stage, and thinking is not a summary.
                        if isinstance(block, TextBlock) and block.text.strip():
                            last_text = block.text
                        elif isinstance(block, ToolUseBlock):
                            tool_names[block.id] = block.name
                        line = _render_block(block, label)
                        if line is not None:
                            log(line)
                        _emit_block(block, stage_name, label)
                elif isinstance(message, ResultMessage):
                    results.append(message)
    finally:
        await client.disconnect()

    if results:
        final = results[-1]
        session_id = final.session_id or session_id
        # Every turn's report, not just the last: the turn that did the work is not
        # always the turn that ended the session.
        summary = "\n\n".join(r.result for r in results if r.result)
        summary = summary or last_text or "(no summary produced)"
        is_error = any(r.is_error for r in results)
        if refused:
            is_error = True
            report = _refusal_report(refused, woken=len(results) > 1)
            log(f"[{label}] {report}")
            summary = f"{report}\n\nWhat the session reported:\n\n{summary}"
        if wait.left_running:
            is_error = True
            report = wait.report()
            log(f"[{label}] {report}")
            summary = f"{report}\n\nWhat the session reported:\n\n{summary}"
        return StageResult(
            stage=stage_name,
            session_id=session_id,
            summary=summary,
            is_error=is_error,
            # The CLI's cost total runs across the whole session, while its turn and
            # duration counts start over with each turn.
            cost_usd=final.total_cost_usd,
            num_turns=sum(r.num_turns or 0 for r in results),
            duration_ms=sum(r.duration_ms or 0 for r in results),
            iteration=iteration,
            item=item,
            segments=len(results),
        )

    return StageResult(
        stage=stage_name,
        session_id=session_id,
        summary=last_text or "(session ended without a result message)",
        is_error=True,
        iteration=iteration,
        item=item,
    )
