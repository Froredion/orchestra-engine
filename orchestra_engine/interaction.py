"""Human-in-the-loop pieces for interactive runs.

Two surfaces:
- Agents ask the human questions via the built-in AskUserQuestion tool; the
  SDK routes it (and any tool call the permission mode didn't auto-approve)
  through the can_use_tool callback below.
- Between stages, the human decides run / skip / quit at the stage gate.
"""

from __future__ import annotations

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
)

from . import channel, ui
from .ui import BACK

_OTHER = "__other__"


async def _answer_one(q: dict, allow_back: bool):
    """Render one AskUserQuestion entry as a picker; return the answer or BACK."""
    message = f"[{q.get('header', 'Question')}] {q['question']}"
    option_choices = [
        (f"{opt['label']} — {opt.get('description', '')}", opt["label"])
        for opt in q.get("options", [])
    ]
    other = ("type my own answer...", _OTHER)

    while True:
        if q.get("multiSelect"):
            picked = await ui.checkbox(
                message,
                [(t, v, False) for t, v in option_choices] + [(*other, False)],
                allow_back=allow_back,
            )
            if picked is BACK:
                return BACK
            if _OTHER in picked:
                picked.remove(_OTHER)
                custom = await ui.text("Your answer:", allow_back=True)
                if custom is BACK:
                    continue  # back to this question's options
                if custom:
                    picked.append(custom)
            return picked
        picked = await ui.select(message, option_choices + [other], allow_back=allow_back)
        if picked is BACK:
            return BACK
        if picked == _OTHER:
            custom = await ui.text("Your answer:", allow_back=True)
            if custom is BACK:
                continue  # back to this question's options
            return custom
        return picked


async def handle_ask_user_question(input_data: dict) -> PermissionResultAllow:
    """Show the agent's questions, collect answers, hand them back.
    Esc on question N steps back to question N-1."""
    questions = input_data.get("questions", [])
    answers: dict[str, str | list[str]] = {}
    i = 0
    while i < len(questions):
        q = questions[i]
        answer = await _answer_one(q, allow_back=i > 0)
        if answer is BACK:
            i -= 1
            continue
        answers[q["question"]] = answer
        i += 1

    return PermissionResultAllow(
        updated_input={"questions": questions, "answers": answers}
    )


async def can_use_tool(tool_name: str, input_data: dict, context) -> object:
    if tool_name == "AskUserQuestion":
        return await handle_ask_user_question(input_data)

    # Anything else reaching this callback wasn't auto-approved by the
    # permission mode — put it to the human.
    decision = await ui.select(
        f"The agent wants to use {tool_name}: {input_data}",
        [("allow", "allow"), ("deny", "deny")],
        kind=channel.PERMISSION,
    )
    if decision == "allow":
        return PermissionResultAllow(updated_input=input_data)
    return PermissionResultDeny(message="The human operator denied this action.")


async def keep_stream_open(input_data, tool_use_id, context) -> dict:
    """Documented SDK workaround: a PreToolUse hook keeps the streaming input
    open so can_use_tool can still be invoked after the prompt is sent."""
    return {"continue_": True}


async def stage_gate(stage_name: str) -> str:
    """Ask the human whether to run the next stage. Returns run|skip|quit."""
    return await ui.select(
        f"Next stage: '{stage_name}'",
        [("run", "run"), ("skip", "skip"), ("quit the run", "quit")],
        kind=channel.GATE,
    )
