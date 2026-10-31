"""Terminal UI helpers.

At a real terminal these render questionary widgets — arrow-key menus and
space-to-toggle checkboxes. Everywhere else (pipes, scripts, tests) they fall
back to plain numbered prompts so nothing ever blocks on a fancy widget with
no terminal attached.

Prompts created with allow_back=True can return the BACK sentinel: at a
terminal via Esc (also the left arrow on menus), in the fallback by typing <.
Compare with `is BACK`.
"""

from __future__ import annotations

import asyncio
import sys

from . import channel

try:
    import questionary
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
    from questionary import Choice
except ImportError:  # keeps the engine importable without the extra
    questionary = None

BACK = object()  # sentinel: the human pressed the back key

_TTY_HINT = "  (esc = back)"
_PIPE_HINT = "  (< = back)"


def _tty() -> bool:
    return questionary is not None and sys.stdin.isatty() and sys.stdout.isatty()


def _enable_back(question, keys: tuple[str, ...]):
    kb = KeyBindings()

    def exit_with_back(event):
        event.app.exit(result=BACK)

    for key in keys:
        kb.add(key, eager=True)(exit_with_back)
    merged = [b for b in (question.application.key_bindings, kb) if b is not None]
    question.application.key_bindings = merge_key_bindings(merged)
    return question


async def _input(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip()


async def _over_channel(kind, message, mode, options, allow_back):
    """Route a prompt through the control channel when one is open. Every
    prompt goes there, not just some: a run with a channel has no one at its
    stdin, and a prompt that fell back to it would wait forever."""
    # Still said on stdout: someone tailing the run's log should see why it
    # paused, not a stream that simply stops.
    print(f"[waiting for an answer — live view or --answer: {kind}] {message}", flush=True)
    answer = await channel.active().ask(kind, message, mode, options, allow_back)
    return BACK if answer is channel.STEP_BACK else answer


async def text(message: str, allow_back: bool = False, kind: str = channel.QUESTION):
    if channel.active():
        return await _over_channel(kind, message, "text", None, allow_back)
    if _tty():
        q = questionary.text(message + (_TTY_HINT if allow_back else ""))
        if allow_back:
            _enable_back(q, keys=("escape",))  # left arrow moves the cursor here
        answer = await q.unsafe_ask_async()
        if answer is BACK:
            return BACK
        return (answer or "").strip()
    raw = await _input(f"{message}{_PIPE_HINT if allow_back else ''}\n> ")
    if allow_back and raw == "<":
        return BACK
    return raw


async def select(
    message: str,
    choices: list[tuple[str, str]],
    allow_back: bool = False,
    kind: str = channel.QUESTION,
):
    """choices are (title, value) pairs; returns the chosen value or BACK.
    kind says whose prompt it is (channel.GATE / QUESTION / PERMISSION)."""
    if channel.active():
        options = [{"label": t, "value": v} for t, v in choices]
        return await _over_channel(kind, message, "select", options, allow_back)
    if _tty():
        q = questionary.select(
            message + (_TTY_HINT if allow_back else ""),
            choices=[Choice(title, value=value) for title, value in choices],
        )
        if allow_back:
            _enable_back(q, keys=("escape", "left"))
        return await q.unsafe_ask_async()
    print(message + (_PIPE_HINT if allow_back else ""))
    for i, (title, _) in enumerate(choices, 1):
        print(f"  {i}. {title}")
    while True:
        raw = await _input("> ")
        if allow_back and raw == "<":
            return BACK
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1][1]
        print(f"  (enter a number 1-{len(choices)})")


async def checkbox(
    message: str,
    choices: list[tuple[str, str, bool]],
    allow_back: bool = False,
    kind: str = channel.QUESTION,
):
    """choices are (title, value, pre_checked); returns chosen values or BACK.
    Empty selection returns the pre-checked defaults."""
    if channel.active():
        options = [{"label": t, "value": v, "checked": c} for t, v, c in choices]
        return await _over_channel(kind, message, "checkbox", options, allow_back)
    if _tty():
        q = questionary.checkbox(
            message + (_TTY_HINT if allow_back else ""),
            choices=[Choice(t, value=v, checked=c) for t, v, c in choices],
        )
        if allow_back:
            _enable_back(q, keys=("escape", "left"))
        picked = await q.unsafe_ask_async()
        if picked is BACK:
            return BACK
        return picked or [v for _, v, c in choices if c]
    print(message + (_PIPE_HINT if allow_back else ""))
    for i, (title, _, checked) in enumerate(choices, 1):
        mark = "x" if checked else " "
        print(f"  [{mark}] {i}. {title}")
    while True:
        raw = await _input("numbers separated by commas [Enter=checked defaults] > ")
        if allow_back and raw == "<":
            return BACK
        if not raw:
            return [v for _, v, c in choices if c]
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if all(p.isdigit() and 1 <= int(p) <= len(choices) for p in parts) and parts:
            return [choices[int(p) - 1][1] for p in parts]
        print(f"  (numbers 1-{len(choices)}, comma-separated)")
