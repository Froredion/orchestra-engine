"""The control channel: how something other than a terminal drives a run.

    --channel DIR
    ├── events.jsonl   <- engine-written, append-only: one JSON object per line
    └── answers/       <- one file per answered prompt, named for its id

Before this, an agent driving a run read a scrolling log to find the prompt and
wrote a bare number into a FIFO. Nothing tied that number to a prompt, so one
reply swallowed by the wrong question desynced the whole run, silently. Here
every prompt carries an id, an answer names the id it is for, and an answer to
anything but the prompt now waiting is refused instead of consumed.

The same file carries what a watcher wants without asking: stages starting and
finishing, escalations as they are raised, the run ending. `--watch` turns it
into one line per event, which is what a Monitor-style tool consumes, and exits
when the run does.

Why files and not Claude Code's cross-session messaging: a stage is a headless
SDK session, which cannot be messaged and cannot send outside its own process,
and the engine is a plain Python process with no supported way to send at all.
Everything here goes through `emit` and `ask`, so a messaging transport can be
added beside the file when one exists, without moving any call site.

Whoever can write answers/ can answer a permission prompt, so the directory is
the operator's and only the operator's: created 0700, and refused outright when
it sits anywhere a stage agent can write — otherwise a stage could approve its
own tool call.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Iterator

EVENTS_FILE = "events.jsonl"
ANSWERS_DIRNAME = "answers"
POLL_SECONDS = 0.5

# Prompt kinds. A permission prompt is the human's to answer, never the driving
# agent's; the kind is on the event so a driver can tell without parsing text.
GATE, QUESTION, PERMISSION = "gate", "question", "permission"

BACK_TOKEN = "<"
STEP_BACK = object()  # what an answer of BACK_TOKEN parses to, where back is allowed

_active: "Channel | None" = None


def active() -> "Channel | None":
    return _active


def emit(event: str, **fields) -> None:
    """Record an event on the active channel; a no-op when there is none, so
    call sites never need to know whether a run has one."""
    if _active is not None:
        _active.emit(event, **fields)


class ChannelError(ValueError):
    pass


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def open_channel(directory: Path, forbidden: dict[str, Path]) -> "Channel":
    """Create the channel for this run and make it the active one.

    forbidden maps a flag name to a directory stage agents can write into; the
    channel may sit inside none of them. A directory that already holds an
    events file is refused too: a watcher started against a reused directory
    would read the last run's ending and stop before this one began.
    """
    global _active
    resolved = directory.expanduser().resolve()
    for flag, root in forbidden.items():
        root = root.resolve()
        if _inside(resolved, root) or _inside(root, resolved):
            raise ChannelError(
                f"--channel {directory} overlaps {flag} {root}. Stage agents can "
                f"write there, and whoever writes the channel answers its "
                f"permission prompts — put the channel somewhere only you write."
            )
    if (resolved / EVENTS_FILE).exists():
        raise ChannelError(
            f"--channel {directory} already holds {EVENTS_FILE} from another run — "
            f"give each run a fresh channel directory."
        )
    (resolved / ANSWERS_DIRNAME).mkdir(parents=True, exist_ok=True)
    os.chmod(resolved, 0o700)
    os.chmod(resolved / ANSWERS_DIRNAME, 0o700)
    _active = Channel(resolved)
    _active.emit("engine_started", pid=os.getpid())
    return _active


def close_channel() -> None:
    global _active
    _active = None


class Channel:
    def __init__(self, directory: Path):
        self.dir = directory
        self.events = directory / EVENTS_FILE
        self.answers = directory / ANSWERS_DIRNAME
        self._seq = 0

    def emit(self, event: str, **fields) -> None:
        record = {"event": event, "ts": time.time(), **fields}
        # One write per line, flushed at once: a watcher reading mid-write sees
        # either the whole record or none of it.
        with self.events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def ask(
        self,
        kind: str,
        message: str,
        mode: str,
        options: list[dict] | None = None,
        allow_back: bool = False,
    ):
        """Publish a prompt and wait for its answer. Returns the parsed value,
        or STEP_BACK when the answer steps back."""
        self._seq += 1
        prompt = {
            "id": f"p{self._seq}",
            "kind": kind,
            "mode": mode,
            "message": message,
            "options": options,
            "allow_back": allow_back,
        }
        self.emit("prompt", **prompt)
        path = self.answers / prompt["id"]
        while True:
            if path.exists():
                raw = path.read_text(encoding="utf-8").strip()
                # The event goes down before the file comes up: `--answer`
                # treats "pending, no file" as free to write, and the other
                # order opens a window where a second answer gets in.
                try:
                    value = parse_answer(prompt, raw)
                except ValueError as exc:
                    self.emit("answer_rejected", id=prompt["id"], answer=raw, reason=str(exc))
                    path.unlink(missing_ok=True)
                    continue
                self.emit("answered", id=prompt["id"], answer=raw)
                path.unlink(missing_ok=True)
                return value
            await asyncio.sleep(POLL_SECONDS)


def parse_answer(prompt: dict, raw: str):
    """Turn an answer string into the value its prompt returns.

    Shared by the engine, which consumes the answer, and `--answer`, which
    checks it before writing — so a bad answer is refused at the command line
    rather than discovered by a run that is waiting on it. An option is named
    by its number (the same one the numbered fallback shows) or its value.
    """
    raw = raw.strip()
    if prompt.get("allow_back") and raw == BACK_TOKEN:
        return STEP_BACK
    mode = prompt["mode"]
    if mode == "text":
        return raw
    options = prompt.get("options") or []

    def one(token: str):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(options):
            return options[int(token) - 1]["value"]
        for opt in options:
            if token == opt["value"]:
                return opt["value"]
        raise ValueError(f"{token!r} is not an option: give 1-{len(options)} or an option's value")

    if mode == "select":
        return one(raw)
    if mode == "checkbox":
        if not raw:
            return [o["value"] for o in options if o.get("checked")]
        return [one(t) for t in raw.split(",") if t.strip()]
    raise ValueError(f"unknown prompt mode {mode!r}")


# ---- the driver's side: reading the channel from another process ----------


def read_events(directory: Path) -> list[dict]:
    path = directory / EVENTS_FILE
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                break  # a line still being written; it is complete next read
    return events


def pending_prompt(events: list[dict]) -> dict | None:
    """The prompt waiting for an answer, if any. Only one agent asks at a time,
    so at most one prompt is ever open."""
    answered = {e["id"] for e in events if e["event"] == "answered"}
    open_ = [e for e in events if e["event"] == "prompt" and e["id"] not in answered]
    return open_[-1] if open_ else None


def answer(directory: Path, prompt_id: str, raw: str) -> dict:
    """Write an answer for the prompt now waiting. Refuses one aimed anywhere
    else, which is the whole point: a stale or mistyped id fails here, loudly,
    instead of being taken by whichever question happens to be open."""
    directory = directory.expanduser().resolve()
    prompt = pending_prompt(read_events(directory))
    if prompt is None:
        raise ChannelError("no prompt is waiting for an answer")
    if prompt["id"] != prompt_id:
        raise ChannelError(f"{prompt_id} is not the waiting prompt — {prompt['id']} is")
    target = directory / ANSWERS_DIRNAME / prompt_id
    if target.exists():
        raise ChannelError(f"{prompt_id} already has an answer waiting to be read")
    try:
        parse_answer(prompt, raw)
    except ValueError as exc:
        raise ChannelError(str(exc)) from exc
    tmp = target.with_name(f".{prompt_id}.tmp")
    tmp.write_text(raw, encoding="utf-8")
    os.replace(tmp, target)
    return prompt


def _alive(pid: int) -> bool:
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _alive_windows(pid: int) -> bool:
    # Not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT, which
    # interrupts every process on the console, the caller included.
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied means it exists but belongs to someone else.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _oneline(text: str) -> str:
    return " ".join(str(text).split())


def render(event: dict, directory: Path) -> list[str]:
    """The watch lines for one event. Agent-written text is collapsed onto the
    event's own line or quoted with `  | `, so nothing a stage wrote can arrive
    looking like a line the engine produced."""
    kind = event["event"]
    if kind == "prompt":
        opts = event.get("options") or []
        listed = " | ".join(f"{i}. {_oneline(o['label'])}" for i, o in enumerate(opts, 1))
        hint = "text" if event["mode"] == "text" else (
            "numbers, comma-separated" if event["mode"] == "checkbox" else "a number"
        )
        lines = [f"PROMPT {event['id']} ({event['kind']}): {_oneline(event['message'])}"]
        if listed:
            lines.append(f"  options: {listed}")
        lines.append(
            f"  answer: ./orchestra --channel {directory} --answer {event['id']} <{hint}>"
        )
        if event["kind"] == PERMISSION:
            lines.append("  ** permission prompt: the human answers this one, not the driver **")
        return lines
    if kind == "escalation":
        return ["ESCALATION raised:"] + [f"  | {l}" for l in event["text"].splitlines()]
    if kind == "answered":
        return [f"ANSWERED {event['id']}: {_oneline(event['answer'])}"]
    if kind == "answer_rejected":
        return [f"REJECTED {event['id']}: {_oneline(event['answer'])} — {event['reason']}"]
    if kind == "stage_started":
        return [f"STAGE {event['stage']} started"]
    if kind == "stage_finished":
        status = "ERROR" if event.get("is_error") else "ok"
        return [f"STAGE {event['stage']} finished [{status}]"]
    if kind == "run_started":
        return [f"RUN started: {event['run_dir']}"]
    if kind == "run_complete":
        tail = " — escalations unresolved, see ESCALATIONS.md" if event.get("escalations") else ""
        return [f"RUN complete: {event['run_dir']}{tail}"]
    if kind == "engine_started":
        return [f"ENGINE started (pid {event['pid']})"]
    if kind == "engine_exited":
        return [f"ENGINE exited: {_oneline(event.get('reason', ''))}"]
    return [f"{kind.upper()}: {_oneline(json.dumps(event))}"]


def watch(directory: Path, poll: float = POLL_SECONDS) -> Iterator[str]:
    """Yield watch lines as events land, ending when the run does — or when
    the engine process is gone, so a crashed run cannot leave a watcher open.
    Waits for the events file to appear, so it can be armed before launch."""
    directory = directory.expanduser().resolve()
    seen = 0
    pid = None
    while True:
        events = read_events(directory)
        for event in events[seen:]:
            if event["event"] == "engine_started":
                pid = event["pid"]
            yield from render(event, directory)
            if event["event"] in ("run_complete", "engine_exited"):
                return
        seen = len(events)
        if pid is not None and not _alive(pid):
            # One last read: the final event may have landed after the read above.
            for event in read_events(directory)[seen:]:
                yield from render(event, directory)
            yield "ENGINE gone without finishing the run (crashed or killed)"
            return
        time.sleep(poll)
