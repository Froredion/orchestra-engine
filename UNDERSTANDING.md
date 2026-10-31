# Understanding Orchestra Engine

This document explains the whole system from zero context: what it is, why it
is shaped the way it is, and how every piece fits. The [README](README.md)
tells you how to run it; this tells you how to *think* about it.

---

## 1. What this project is

Orchestra Engine is an **orchestrator for chains of AI agent sessions**, built
on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk).

You give it a task in plain language. It runs a sequence of *stages*, where
each stage is a **completely fresh Claude agent session** with one narrow job.
When a stage finishes, the engine captures two things:

1. its **final summary** — the distilled result of everything it did
2. its **session ID** — a pointer to the full transcript on disk

…and injects both into the next stage's prompt. That pair is called the
**handoff**. The next agent starts clean, reads the handoff, looks at the
files the previous agent left behind, and does *its* one job.

```
task ──▶ [agent: stage 1] ──summary+session──▶ [agent: stage 2] ──▶ ... ──▶ done
                │                                    │
                └──── shared artifacts (files) ──────┘
```

Agents write to two directories, and the difference matters. The **workspace**
is the repo you pointed the loop at; it receives the deliverable and nothing
else. The **artifacts directory** is a fresh folder per run; it receives the
stage documents — the files stages write for each other, which are also the
record you read afterwards. Code goes in the repo, reasoning goes in the run.

That's the entire core idea. Everything else in the repo is either
(a) a way to describe *which* stages run in *what* shape, or
(b) plumbing to run them.

### Who runs it

The intended way to use the engine involves three parties, and it helps to
keep them apart:

| Who | Does what |
|---|---|
| **Your main agent** (e.g. Claude Code) | *Drives* the run. You ask it to run a pipeline; it launches the engine, answers the stage gates and the stage agents' questions over `--channel`, watches for escalations, and brings you the result. It doesn't do the work itself. |
| **The engine and its stage agents** | *Do* the work: one fresh session per stage, as described above. Stage agents never see the main agent's instructions or your global `CLAUDE.md`. |
| **You** | *Decide.* You watch the run in the live view, answer every permission prompt, and rule on escalations. |

The main agent learns how to drive from `./orchestra --install-guidance`, which
puts [agent_guidance.md](orchestra_engine/agent_guidance.md) in its memory.
Running the engine by hand at a terminal works too; it's the same engine with
you in the driver's seat.

---

## 2. Why fresh sessions? (the noise-removal argument)

Why not just one long conversation with one agent?

Because long agent sessions accumulate **noise**: dead-end explorations,
half-relevant search results, tool output that mattered for a moment and never
again. Everything stays in the context window and competes for the model's
attention. Quality degrades as the ratio of signal to noise drops.

The handoff is a deliberate **bottleneck**. A stage can thrash around as much
as it needs to — but the only things that survive it are:

- the files it deliberately wrote (artifacts, or code in the workspace), and
- the summary it deliberately composed at the end.

Everything else dies with the session. Each handoff is a filter pass, and a
pipeline is a series of filters. This is also why *repeating* a stage helps
(see §5): pass 2 starts clean, receives only pass 1's distilled output, and is
told to verify and cut. Iteration converges on signal.

The session ID is the escape hatch: if a summary turns out to be too thin, a
later stage can read that session's full transcript from
`~/.claude/projects/<project>/<session-id>.jsonl`. This isn't limited to the
immediately-next stage or to routers — the engine hands *every* stage a compact
index of all prior sessions' transcripts, and tells it to pull only the specific
fact it needs (grep the JSONL, read the slice around it) rather than ingest the
whole file, which would just refill the context this bottleneck exists to drain.
In practice the summary plus the artifact files are almost always enough; the
index is there so the bottleneck never *loses* information, it only
de-prioritizes it.

---

## 3. The three concepts

| Concept | What it is | Where it lives |
|---|---|---|
| **Engine** | Generic executor. Runs stages, threads handoffs, knows nothing about any specific workflow. | [orchestra_engine/engine/](orchestra_engine/engine/) |
| **Pipeline** | One workflow, described entirely as data: a folder of numbered markdown prompt files. | [orchestra_engine/pipelines/](orchestra_engine/pipelines/) or any directory you point at |
| **Stage** | One prompt file = one agent session (or several, if it repeats / fans out). | a single `.md` file inside a pipeline folder |

A pipeline is *just a folder*:

```
research/
├── 01-search.md        ← stage "search"
├── 02-findings.md      ← stage "findings"
├── 03-deep-dive.md     ← stage "deep-dive"
├── 04-report.md        ← stage "report"
└── handoff.md          ← optional: override how a stage's report is rendered
```

Order comes from the filename sort. The stage's *name* is the part after the
number (`03-deep-dive.md` → `deep-dive`). Nothing registers a pipeline
anywhere; dropping a folder on disk **is** creating a workflow.

---

## 4. Anatomy of a stage file

A stage file has two parts: an optional **frontmatter block** (execution
shape) and the **prompt template** (the agent's instructions).

```markdown
---
repeat: 2            ← run this stage 2 times, passing handoff between passes
handoff: all         ← each pass sees every prior pass (default: last)
fan_out: leads.txt   ← run once per line of this artifacts file, in parallel
max_items: 5         ← use only the top 5 lines of that file
---
You are a DEEP-DIVE agent (pass {iteration} of {repeat})...

Task: {task}
Your assigned lead: {item}
...
{handoff}
```

All frontmatter keys are optional; a file with no frontmatter is a plain
run-once stage — the default shape, and enough to build a whole pipeline out of
on its own.

A stage can also *point* at its prompt rather than hold it: `redirect: PATH`
takes the template from another markdown file — a `SKILL.md` you already
maintain, say — with the target's own frontmatter ignored so it works as-is.
The body below then serves as the fallback, and runs whenever that file isn't on
the machine ([details](docs/authoring-pipelines.md#pointing-at-the-prompt-redirect)).

The template is rendered with Python `str.format`, with these placeholders:

| Placeholder | Meaning |
|---|---|
| `{task}` | The user's task, verbatim |
| `{handoff}` | One block per visible prior session (summary + session ID), oldest first — see `handoff` in §5. Empty string for the very first session. |
| `{iteration}` | Which pass this is, starting at 1 |
| `{repeat}` | Total number of passes for this stage |
| `{item}` | The fan-out item assigned to this agent (empty for normal stages) |

> **Gotcha:** because templates go through `str.format`, a literal `{` or `}`
> in a prompt must be doubled (`{{`, `}}`), or loading will fail.

Frontmatter is validated at load time — unknown keys, non-integer counts,
`max_items` without `fan_out`, or a `fan_out` path that points outside the
workspace are all rejected *before* any agent runs. Fail fast, not mid-run.

---

## 5. The execution model

The engine walks the stages in order. For each stage it asks: what *shape* is
this one? The walk is forward by default — but every plain stage decides its own
next step (advance, run again, jump, or finish), so "in order" means "top to
bottom unless a stage routes otherwise" (see Routing, below).

### Plain stage (the default)

One agent session. Its handoff feeds the next stage. If it errors, the loop
stops — every later stage depends on it.

### `repeat: N` — sequential passes

The same stage runs N times **in sequence**, for *deepening*: each pass is
told, in its prompt, to read what earlier passes produced and go where they
didn't. The next stage receives the *last* pass's handoff (earlier work
survives as workspace files). An error in any pass stops the loop, same as a
plain stage.

What each pass can *see* is set by `handoff`, and the difference matters more
than it first looks.

**`handoff: last` (the default) — a chain.** Each pass sees exactly one
handoff: the pass immediately before it. Pass 1 sees the previous *stage*.

```
prev ──▶ pass 1 ──▶ pass 2 ──▶ pass 3 ──▶ next stage
           (sees prev)  (sees 1)   (sees 2)
```

**`handoff: all` — accumulation.** Each pass sees every prior handoff, oldest
first, each as its own block with its own session ID.

```
prev ──▶ pass 1 ──▶ pass 2 ──▶ pass 3 ──▶ next stage
           (sees prev)  (sees prev, 1)  (sees prev, 1, 2)
```

The distinction isn't reachability. Recall from §2 that every stage is handed a
compact index of *all* prior sessions' transcripts, session IDs included — so an
earlier pass stays openable whatever the handoff scope. What `handoff` controls
is which earlier summaries get *threaded into the prompt as full text*: under
`last`, pass 3 carries only pass 2's summary (leaner); under `all`, it carries
pass 1's and pass 2's both. So `all` isn't what makes pass 1 reachable — it's
what puts pass 1's summary in front of pass 3 without it having to go dig. That's
why the research pipeline's `search` stage uses `all`: pass 3 sees at a glance
that pass 1 and pass 2 disagree, then reads either transcript to settle it.

So which is right? `last` is the philosophically default-correct one — it's
the §2 bottleneck doing its job, and on a long chain it keeps prompts lean.
Reach for `all` when passes are meant to *compound* rather than merely refine.
(The engine rejects `handoff: all` without `repeat: N`, since it would
silently do nothing.)

### `fan_out: file.txt` — parallel per item

The engine reads `file.txt` from the run's artifacts directory — a file
**written by a previous stage's agent** — takes one item per line (blank lines
and `#` comments ignored, truncated to `max_items`), and runs **one agent per
item, in parallel**, capped at 5 concurrent sessions.

Crucially, `fan_out` composes with `repeat`: each item gets its own private
N-pass chain. Items are isolated from each other; passes within an item are
sequential and handoff-threaded.

```
              ┌─▶ item A: pass 1 ──▶ pass 2 ─┐
prev ──▶ fan ─┼─▶ item B: pass 1 ──▶ pass 2 ─┼─▶ merged handoff ──▶ next stage
              └─▶ item C: pass 1 ──▶ pass 2 ─┘
```

After the fan-out, the per-item summaries are merged into a single handoff
(each item labeled, with its final session ID) for the next stage.

Failure semantics differ from sequential stages on purpose: if *some* items
fail, the loop **continues** with the survivors (their failures are noted in
the merged handoff); it stops only if *every* item failed, or if the fan-out
file itself is missing or empty. A research run shouldn't die because one of
five websites was unreachable.

### Routing — every plain stage decides its own next step

Every shape above moves the run forward. Routing is how a stage moves it
anywhere else. As its last act, a plain sequential stage writes `decision.json`,
and the engine reads it and jumps:

| decision | goes to |
|---|---|
| `{"next": "advance"}` | the next stage (also the default when it writes nothing) |
| `{"next": "again"}` | this same stage, once more |
| `{"next": "<stage>"}` | a named stage (back = loop, forward = skip) |
| `{"done": true}` | straight to the terminal stage |

`advance` / `again` / `done` are free to any stage; a named jump must be listed
in that stage's `decide:` targets, so a stage file still declares where it can
send the run.

```
reproduce ⟲again ─▶ localize ─▶ diagnose ─▶ fix ─▶ verify ─┬─ {"done"} ─▶ prevent
    ▲                  ▲                        ▲           │
    └── {"next":"reproduce"} ── {"next":"diagnose"} ────────┘  (verify routes back)
```

This is where "an agent decides, not a fixed count" *becomes* the control flow.
bugfix used to march `reproduce ×2 → … → verify ×2 → prevent` on fixed counts;
now `reproduce` runs **once** and calls `again` only if the reproduction isn't
solid, and `verify` reads whether the fix *actually held* and, if not, loops
back to re-diagnose — or, for a genuinely different root cause, re-localize. It
leans on the §2 escape hatch directly: because each stage is a Claude Code
session, and every prior stage's session ID is on hand, a stage weighing a route
back can open those stages' full transcripts (not just their summaries) to judge
where the run went wrong.

Two properties keep a backward edge from becoming an infinite one:

- **It always halts.** A **hop budget** (`--max-hops`, default 2× the stage
  count) caps total stage executions; on exhaustion the run jumps straight to
  the last stage. A clean forward run spends ~1× and never nears it.
- **It always ends somewhere real.** The last stage never routes and is never a
  jump target, so every run ends on the terminal stage — bugfix always writes
  its POSTMORTEM.md, whether the fix landed, failed, or ran out of hops.

And like `skip_if`, an unhonorable decision **fails safe**: a missing, malformed,
out-of-bounds, or undeclared-target `decision.json` is logged and the run
advances, rather than looping forever or jumping somewhere the pipeline never
authorized. The engine reads a file an agent wrote and acts on it — the same
boundary as `fan_out`, with a routing choice as the payload. (Fan-out stages
can't route — parallel agents would race to write the one decision — and a fixed
`repeat: N` is a shape, not a decision, so the two never combine.)

### Everything else per session

Every session — regardless of shape — gets the same treatment:

- It runs inside the **workspace** directory (default `./workspace`), with the
  run's **artifacts** directory granted alongside it via the SDK's `add_dirs`.
  A prompt preamble names both, says which takes code and which takes stage
  documents, and pins the session to them so it never writes anywhere else.
- It runs under the configured **permission mode** (`auto` by default;
  `bypassPermissions` for fully unattended runs in a scratch workspace).
- In **interactive mode** (`-i`), a human gates each stage (run/skip/quit),
  approves non-auto-approved tool calls, and can be asked questions by the
  agent mid-run. Exception: parallel fan-out sessions always run unattended,
  because five agents can't share one terminal — the stage gate still applies
  before the fan-out starts.
- It is told how to escalate, in the same preamble — so **`ESCALATIONS.md`** is
  available to every stage of every pipeline, not only the ones whose prompts
  happen to mention it. That file is where a stage records that the run now
  promises less than it did: a behaviour withdrawn, a criterion narrowed, a
  stated requirement built as asked but flagged as fighting something. The engine
  restores text a later stage removes, so appending a rebuttal is available and
  erasing the original is not. Its growth is announced in the stream at the stage
  boundary that caused it, and the whole record is printed again when the run
  ends — nothing halts on one, because whether to stop depends on the entry and
  belongs to whoever is watching.
- Its cost, turn count, duration, summary, and session ID are recorded; the
  whole run is written to `<runs-dir>/<timestamp>/run.json` when the loop ends,
  next to the `artifacts/` the stages produced.

---

## 6. Worked example: the research pipeline

The built-in `research` pipeline exists to answer a question from the web
*with the noise filtered out*. Its shape:

```
search ×3  ──▶  findings  ──▶  deep-dive (∥ per lead, ×2 each)  ──▶  report
```

Trace a run of `./orchestra "research the best X for Y" --pipeline research -y`:

1. **search, pass 1** — fresh agent. Runs several web searches, crawls the
   promising hits, writes `search-findings-1.md` (facts with sources,
   candidate lead URLs, and an explicit "Noise" section of things that looked
   relevant but weren't). Ends with a summary.
2. **search, pass 2** — fresh agent, receives pass 1's summary + session ID.
   Its prompt tells it: do *not* re-tread covered ground; search different
   angles; dig where pass 1 was thin. Writes `search-findings-2.md`.
3. **search, pass 3** — same again, one level deeper, and because the stage
   declares `handoff: all`, this one receives passes 1 *and* 2 — both
   summaries, both session IDs. Three passes exist because one pass of web
   search returns whatever the search engine likes; three
   deliberately-different, compounding passes triangulate.
4. **findings** — fresh agent. Reads all three `search-findings-*.md`, merges
   duplicates, flags contradictions, discards everything marked as noise.
   Writes `FINDINGS.md` (the consolidated picture) and `leads.txt` — lead
   website URLs, one per line, **ranked best-first**, because the engine will
   deep-dive only the top of the list (`max_items: 5`).
5. **deep-dive** — the engine reads `leads.txt`, takes the top 5, and starts
   5 parallel agents. Each agent is bound to exactly one site:
   - *Pass 1* crawls that site (entry page, then the internal links that
     matter), writes `deep-dive-<domain>.md` with facts and exact page URLs.
   - *Pass 2* is a fresh agent handed pass 1's findings. Its job is noise
     removal: re-verify every claim against the site, deepen the thin parts,
     and **rewrite** the file — cutting anything it couldn't confirm.
6. **report** — fresh agent receives the merged per-lead summaries, reads
   `FINDINGS.md` and every `deep-dive-*.md`, and writes `REPORT.md`: themed,
   cited, confirmed-vs-single-source separated, with a short "discarded as
   noise" section so a reader knows what was considered and cut.

Change the intensity per run without editing any file:

```bash
./orchestra "..." --pipeline research --repeat search=5,deep-dive=3 --max-items deep-dive=10 -y
```

Another built-in, `feature`, is the same engine with a totally different
workflow: seven stages (explore → design → build → test → reread → verify →
integrate) that never repeat and never fan out, but *route* — a stage that
finds the plan was wrong sends the run back to an earlier one. Same mechanism,
nothing in common but the mechanism. That contrast is the point of the next
section.

---

## 7. How different workflows are organized (the architecture question)

Every pipeline is a different workflow — research loops and fans out, `feature`
routes back on itself, a future one might be something else. So where does each
workflow's logic *live*, and who is responsible for what?

### Mechanism vs. policy

The organizing principle is the classic **separation of mechanism and
policy**:

- The **engine is pure mechanism.** The `engine/` package can run a stage,
  thread a handoff, repeat a stage, and fan a stage out over items. It has no
  idea what "search" or "deploy" means. Grep it for "research" — nothing.
- **Each workflow is pure policy, expressed as data.** A pipeline folder
  contains no code: just prompts (the domain knowledge) and frontmatter (the
  shape). The research workflow's "3× search then parallel per-lead
  verification" lives entirely in two small frontmatter blocks.

This buys the **open/closed property**: the system is open to new workflows
but closed to modification — adding the research pipeline required *zero*
changes to how `feature` runs, and your next pipeline requires zero changes to
either. Deleting one is the same story in reverse: a pipeline nobody used comes
out as a folder, and no other workflow notices. If a workflow change ever forces
an engine change, that's the signal the abstraction is wrong.

### A deliberately tiny workflow vocabulary

The engine's workflow grammar is small and stays that way: three **shapes** —
**sequence** (the folder order), **repeat**, and **fan-out** — the `skip_if`
forward gate, and **routing**: every plain stage picks its own next step
(advance / again / jump / done), with named jumps declared in `decide:`. Plus
`handoff` and `max_items`, which only tune the shapes. That is not an accident,
and it's worth being honest about the trade-off:

- **What it can't express:** arbitrary DAGs the *engine* computes, dynamically
  generated stages, branching on computed expressions (`if retries < 3 and
  score > 0.7`). Routing is agent-directed, not engine-directed: the engine
  never evaluates a condition — it reads a stage name an agent wrote and jumps.
  This is still a pipeline whose edges an agent chooses, not a workflow engine
  that computes them.
- **Why that's a feature:** every keyword added to frontmatter moves logic
  out of prompts (which are legible, per-workflow, and editable by anyone)
  into engine code (which is shared and must stay universal). Follow that
  road far enough and the frontmatter becomes a bad programming language and
  the engine becomes a worse Airflow. The rule: the engine gets a new primitive
  only when a workflow *shape* genuinely can't be expressed — repeat and fan-out
  earned their place because "iterate to deepen" and "parallelize per item" are
  shapes, and routing earned its because "go again, or back, when a stage's own
  judgment says so" is a shape a prompt physically cannot express: a prompt can't
  move the engine's instruction pointer. Routing is the closest this gets to a
  general agent-directed goto — which is exactly the "reinvent the agent loop"
  risk — so it's fenced hard: the agent only writes a file, the engine reads it;
  the loop is bounded by a hop budget so it always halts; named jumps must be
  declared; the terminal always runs; and an unhonorable decision fails safe. The
  day it wants conditions and expressions *in frontmatter*, that's the smell to
  stop — that logic belongs in a prompt, judged by an agent.
- **Where dynamic behavior actually lives:** inside stages. An agent *within*
  a stage can decide what to search, which links matter, how many leads to
  list (the engine only caps them). The fan-out file is exactly this boundary:
  a *previous agent* decides the items; the *engine* just distributes them.
  Decisions belong to agents; structure belongs to the engine.

### Single responsibility across modules

The code side mirrors the same discipline — one module, one job:

| Module | Responsibility |
|---|---|
| [model.py](orchestra_engine/model.py) | Pure data: `Stage`, `Pipeline`, `StageResult`. Imports only the standard library. |
| [pipeline.py](orchestra_engine/pipeline.py) | *Describe* workflows: load folders, parse/validate frontmatter, apply overrides. Never runs anything. |
| [engine/session.py](orchestra_engine/engine/session.py) | One SDK agent session — the only engine module that imports `claude_agent_sdk`. |
| [engine/shapes.py](orchestra_engine/engine/shapes.py) | How a stage's sessions are arranged: repeat chains, parallel fan-out (bounded concurrency, artifacts-confined items file). |
| [engine/executor.py](orchestra_engine/engine/executor.py) | `run_loop`: stage ordering, gating, handoff threading, shape selection. |
| [engine/runlog.py](orchestra_engine/engine/runlog.py) | The run directory: `artifacts/` for the stages, `run.json` for the record. |
| [main.py](orchestra_engine/main.py) | CLI: flags → a loaded pipeline + options. |
| [wizard.py](orchestra_engine/wizard.py) | Interactive configuration of the same options the flags cover. |
| [interaction.py](orchestra_engine/interaction.py) | Human-in-the-loop surfaces: stage gates, tool approval, agent questions. |
| [ui.py](orchestra_engine/ui.py) | Terminal pickers/prompts, nothing else. |
| [guidance.py](orchestra_engine/guidance.py) | Installs `agent_guidance.md` into a Claude Code memory file as one `<orchestra-engine>` block, in place and idempotently. Touches no run. |

Two boundary rules keep the structure honest as it grows: **imports flow one
way** (`model` ← `pipeline` ← `engine`; nothing imports "up"), and **the SDK
stays quarantined** in `engine/session.py`, so SDK churn lands in one file.
Behavior is pinned by `tests/` — the engine tests run the real `run_loop`
against a stubbed session runner, so no API calls are made.

Plus **convention over configuration** (numbered filenames *are* the
ordering; a folder *is* a pipeline; no manifest, no registry) and **fail-fast
validation** (a typo'd frontmatter key or an out-of-artifacts `fan_out` path
kills the run at load time, before any tokens are spent).

So: yes — each pipeline having a different workflow is not a problem the
design has to fight; it's the thing the design is *for*. The engine defines
what workflows **can** be (sequences of repeatable, fan-out-able agent
stages), and each folder defines what one workflow **is**.

---

## 8. Writing your own pipeline — a checklist

1. Make a folder anywhere; add numbered `.md` files: `01-first.md`,
   `02-second.md`, …
2. In each file, write the prompt as if briefing a competent stranger who has
   *only* your text, `{task}`, `{handoff}`, and the files on disk. Name the
   exact files the stage must read and write — files are the contract between
   stages — and point them at `{artifacts}`, so the run's reasoning stays out
   of the user's repo. If the pipeline's deliverable is a *document* rather
   than code, have the last stage publish a copy to `{docs}` as well: that
   directory is named on the command line, sits outside the timestamped run
   tree, and is the only one a human is expected to browse.
3. End every prompt with an instruction to finish with a short summary "for
   the next stage" — that sentence is what makes handoffs good.
4. Add frontmatter only where the *shape* needs it: `repeat: N` to deepen by
   iteration (add `handoff: all` if the passes should compound rather than
   just refine the last one), `fan_out: <file>` (+ `max_items`) to parallelize
   per item. If you use `fan_out`, some earlier stage's prompt must say "write
   that file, one item per line, ranked best-first."
5. Escape literal braces as `{{` / `}}`.
6. Run it: `./orchestra "your task" --pipeline ./your-folder`. Tune counts per run
   with `--repeat stage=N` / `--max-items stage=N` instead of editing files.
7. Read the run directory afterwards — `run.json` gives per-session summaries,
   turn counts and costs (which stage needs a better prompt), and `artifacts/`
   holds what the stages actually wrote.

---

## 9. Mental model, in one paragraph

The engine is a dumb, reliable conveyor belt for agent sessions; a pipeline
is a folder that tells the belt how many stations there are, which stations
repeat, and which split into parallel lanes; each station is a fresh agent
whose entire worldview is one prompt file, one handoff, and the shared
workspace — and the handoff bottleneck between stations is not a limitation
but the mechanism that makes the whole thing progressively shed noise. Your
main agent runs the belt for you, and you only step in for what a human has to
decide.
