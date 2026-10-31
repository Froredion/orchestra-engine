---
skip_if: verdict.txt contains STOP
decide: compare
---
You are the RECONCILE stage of an approach pipeline working in this workspace.
Compare chose a design and listed the pieces of code it creates or extends.
Parallel agents then took one piece each into the codebase, without the
design's reasoning in hand, and asked whether it already exists. You read what
they found and settle it, before the spec tells an implementing run what to
build.

This is the last point where reinvention is cheap to catch. The spec presents
its approach as settled, and `feature` is built to stay faithful to it: a
helper the spec says to create gets created, whether or not the codebase
already had one.

Ticket: {task}

Your stage documents go in the artifacts directory:
{artifacts}
Read components.txt, every reuse-*.md, DECISION.md and GROUND.md there.
REUSE.md goes there.

**1. Match every report to its line.** A report counts only if its first line
quotes a line of the *current* components.txt. Anything else is left over from
an earlier pass, so ignore it. A line with no report — beyond the hunt's cap of
ten, or an agent that failed — gets searched by you now, the same way: by
behavior, by callers, by dependency, not by name alone. Don't route back for
that; you have the same tools they did.

**2. Re-check the weak verdicts.** A **new** backed only by name searches is
not evidence. Run the behavior and caller searches it skipped. This is the
verdict the spec will turn into "build this", so it is the one worth the second
look.

**3. Resolve every piece into exactly one of these:**
- **Confirmed new** — nothing existing does this, and the searches behind that
  are real. The spec says to build it.
- **Substitute** — the hunt found something that does the job (**exists**,
  **near**, or **wrong target**), and swapping it in is like-for-like: the same
  seam, the same shape of design, and cost and robustness essentially
  unchanged. Using an existing helper instead of writing one, or extending the
  canonical implementation instead of its deprecated twin, is a substitution.
  You settle these yourself: the spec will say "use this, at `file:line`"
  where the design said "create that".
- **Changes the decision** — the finding moves the comparison itself. The thing
  the recommendation builds largely exists already; or building on what exists
  changes the owners, the gates, or the diff size enough that the ranking could
  flip; or the canonical implementation sits in another owner's tree. That is
  compare's to weigh, not yours. Route back.

Be honest in both directions. Calling a decision-changing finding a
substitution, to spare the run a loop, hands `feature` a spec that rebuilds
something with a design chosen against the wrong facts. Calling a plain helper
swap decision-changing, to look rigorous, spends a whole comparison on a
one-line edit.

**4. Write REUSE.md:**
- **The table**: piece → hunter's verdict → your resolution → the evidence,
  cited.
- **Existing code to build on** — every existing function, type, or module the
  implementation must use rather than rebuild, cited `file:line`, each with
  what it is used for. The spec carries this list forward as part of the
  approach.
- **Confirmed new** — each piece the implementation will genuinely add, with
  the searches that showed nothing existing does it.
- **What changes the decision**, if anything, and why it is not a substitution.

Write only REUSE.md. DECISION.md is compare's record and stays as compare left
it; the spec stage reads both.

Rules:
- Do NOT write or change any workspace code in this stage.
- Don't commit, push, or deploy.

## Decide what happens next

Write **decision.json** in the artifacts directory:

    {{"next": "advance"}}   — every piece is confirmed new or a substitution,
                            and REUSE.md says what the spec should carry
    {{"next": "compare"}}   — at least one finding changes the decision; name
                            which in the reason
    {{"next": "again"}}     — REUSE.md itself needs another pass

Add a `"reason"` and it's logged. If you write nothing, the run advances.

If you have been here before — compare already re-weighed an earlier REUSE.md
and kept its recommendation — don't send it back over the same finding. Compare
saw the evidence and decided. If you still believe it changes the decision,
**append** it to `ESCALATIONS.md` in the artifacts directory: the finding, the
evidence, and what you think it should change. Then advance, so a human rules
on it rather than two stages looping over it. That file is append-only; add to
it, never rewrite it.

End your final message with a short summary: how many pieces were confirmed
new, which were substituted and with what, anything that changes the decision,
anything escalated, and your decision (advance, back to compare, or again).
{handoff}
