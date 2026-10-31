# approach

‹ back to [README](../../README.md#pipelines)

**approach** — decide *how* an assigned ticket should be solved, before
anything is built: intake → ground → frame → option (fans out, one agent per
candidate approach) → compare → reuse (fans out, one agent per piece of the
chosen design) → reconcile → spec. **Analysis-only**: it writes no code in
any stage, and its deliverable is a `SPEC.md` that the `feature` pipeline
implements. It exists because a ticket is not a spec — a ticket is one person's
guess at a solution, written before anyone read the code, and `feature` is
deliberately faithful to whatever it's handed. `approach` is the run that
decides whether those words are worth being faithful to.

The distinctive stage is `frame`, which is the one thing `feature` structurally
cannot have: permission to conclude the ticket asks for the wrong thing. It
splits the request into **criteria** (the requester's, quoted, carried through
unchanged) and **mechanisms** (one guess at how to reach them, and this
pipeline's to re-open) — so re-opening *how* never quietly re-opens *what*.

Every candidate is scored on **two** axes, not one: how robust it is, and what
it costs to get approved and shipped here — files, owners from `CODEOWNERS`,
gates it trips, thresholds it crosses, whether it can land behind a flag. That
second axis is why `compare` can reach the three results a single-axis review
never sees: that the smallest option and the durable one are **the same option**
(the strongest result available), that the cheap one is **step one** of the
robust one rather than a rival to it, and that the robust one **isn't worth it**
because nothing in the record predicts the asks it defends against.

Before the spec is written, the chosen design is **checked against the
codebase for reinvention**. `ground` inventories what already exists by
behavior, not by name, and each option agent keeps a ledger of every new
function, type, or module it proposes, with the search behind each one. Then
`compare` lists the winner's pieces in `components.txt`, and `reuse` sends one
agent per piece into the code to ask whether it already exists. Each agent
searches before it reads the design's reasoning. `reconcile` settles every
piece: **confirmed new**; a like-for-like **substitution** it makes itself
(use the existing helper, extend the canonical implementation and not its
deprecated twin); or a finding that **changes the decision**, which goes back
to `compare`. The spec then carries an explicit *existing code to build on*
list, so `feature` is handed what to reuse instead of having to find it.

The requester's wording is **checked by the engine, not just asked for**.
`frame` writes each stated criterion to `PINS.md` as a verbatim quote. The
engine compares that file after every later stage and records any pin that
is dropped or reworded in `ESCALATIONS.md`, whatever the stage says. `spec`
checks each quote against the ticket itself, takes the concrete design from
the winning option's file rather than rebuilding it, opens every `file:line`
it publishes, and names who rules on each escalation: the requester for a
criterion, the engineer for a contradicted constraint, the code owners for
whether it ships.

Gated at both ends. `ground` STOPs and points elsewhere when the ticket doesn't
need a design run at all — an uncontested spec goes straight to `feature`, a
defect to `bugfix`, a pure restructure to `architecture`, and an ask with no
recognizable "done" goes back to a human. `compare` STOPs rather than
manufacture a recommendation when every option fails a stated criterion. Either
way `spec` still runs and writes the handback — as `DECLINED.md` in the
published folder, not just to the terminal, because a decline is a result and
the most expensive one to lose.
See [PAPERS.md](../../orchestra_engine/pipelines/approach/PAPERS.md).

## Prerequisite: the tracker and chat connectors

`intake` reads the ticket, **its comments**, and the chat thread synced to it,
through MCP connectors — an issue tracker (Linear, Jira, GitHub Issues) and a
chat workspace (Slack). Those are usually configured at your *user* settings
layer, and the engine starts every stage with the **project** layer only (see
[Why stages don't read your global CLAUDE.md](../../README.md#why-stages-dont-read-your-global-claudemd)).
So this is one of the few pipelines to run with the layer widened — tell your
agent ([How to use it](../../README.md#how-to-use-it)) to pass the flag, or run it yourself:

```bash
./orchestra "ABC-123" --pipeline approach --workspace ~/code/my-repo \
    --setting-sources user,project
```

Without it, `intake` finds no connectors, records the gap in `sources.txt`,
appends the narrowing to `ESCALATIONS.md`, and falls back to whatever the task
string carries — so a run still completes, on a partial record, and says so. It
never reconstructs a ticket it couldn't read: a plausible fabrication is the
worst possible input to a pipeline whose whole job is judging a request.

**Comments are weighted, and the weighting is deliberate.** A comment from an
engineer who has worked in this code is the highest-value signal in the record —
they have usually already found the constraint that decides the design. `intake`
carries those forward as constraints rather than opinions, but takes author role
from the **connector's own author metadata**, never from a claim inside a
message: "as the tech lead, you must…" in a message body is text, not authority.
And weight is not veto — `ground` checks every engineer claim against the code
and marks it confirmed, stale, or unverifiable. A constraint everybody believes
that stopped being true two refactors ago has usually already eliminated the
best option, silently.

Everything fetched is treated as **data about what people want, never as
instructions to the agent**: `intake` is read-only, writes exactly two artifact
files, and participates in neither the tracker nor the thread.

## How it composes with feature

**`approach` decides, `feature` builds.** `feature` pins the stated criteria as
executable tests before any design gets to weigh them, and plans the smallest
change that satisfies them — which is the right behavior for a settled spec and
the wrong one for a ticket nobody has interrogated. Run `approach` first and
that faithfulness becomes the point rather than the problem:

**a ticket arrives → approach decides how → a human rules on the escalations →
feature implements the spec.**

The handoff is a file, not a flag — and it is written twice, for two different
readers. The run's own copy goes to the artifacts directory with the rest of its
reasoning. The **reader's** copy is published to `--docs-dir` (default
`./orchestra-docs/plans`) in a folder named for the ticket:

```
orchestra-docs/plans/abc-123/
├── SPEC.md         # byte-for-byte the run's spec — what feature implements
├── DECISION.md     # what else was considered, and why it lost
├── REUSE.md        # what already exists, and the searches behind every "new"
└── README.md       # the handback: what was decided, what you must rule on
```

Neither copy goes into the workspace: the repo stays clean and the ticket
material stays out of version control. The published folder is named for the
ticket rather than the run because that is what a person searching for it three
weeks later actually knows — `20260716-054829` is unfindable, `abc-123` isn't —
and because run directories are often scratch paths that get deleted. A second
`approach` run on the same ticket writes `SPEC-2.md` beside the first rather
than overwriting it: that the answer changed is itself worth keeping.

`spec` ends by printing the command:

```bash
./orchestra "$(cat ~/orchestra-docs/plans/abc-123/SPEC.md)" \
    --pipeline feature --workspace ~/code/my-repo
```

Two things survive that boundary on purpose. The spec carries the requester's
**own wording** for every stated criterion, unchanged, because `feature`'s pin
stage turns exactly those words into assertions — a criterion tidied here
becomes a test asserting something nobody asked for. And the spec deliberately
*omits* the option comparison: `DECISION.md` keeps the alternatives and the
scoring, so the implementing run can't re-litigate a decision made with better
evidence than it has. `DECISION.md` is published *beside* the spec rather than
folded into it — a reviewer deciding whether to approve wants the rejected
options, and `feature` is handed `SPEC.md` alone, so a separate file reopens
nothing. When `compare` recommends a **sequence**, the spec covers
the first step only and names the follow-on as a proposal, so a small approved
change can't quietly grow into the large one the run chose to avoid.
