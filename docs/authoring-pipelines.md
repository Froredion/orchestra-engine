# Authoring pipelines

‹ back to [README](../README.md#pipelines)

A **pipeline** is just a folder of numbered prompt files — the engine runs them
in filename order. This page is the reference for the frontmatter that changes a
stage's *shape*: how many times it runs, whether it fans out, when it's skipped,
and where it can send the run next.

## Stage options: repeat, handoff scope, fan-out

A stage file may start with a frontmatter block:

```
---
repeat: 3            # run this stage 3 times; each pass gets the previous
                     # pass's handoff, so it digs instead of re-treading
handoff: all         # every pass sees ALL prior passes (default: last)
fan_out: leads.txt   # run once per line of this artifacts file — one agent
                     # per item, in parallel (bounded at 5 concurrent)
max_items: 5         # only the top N lines of the fan-out file are used
skip_if: verdict.txt contains STOP   # skip this stage when an earlier one
                     # wrote that verdict (see "Conditional stages")
decide: reproduce, diagnose          # let this stage also jump the run back to
                     # these named stages (every plain stage can already
                     # advance / again / done — see "Deciding what's next")
redirect: ../../skills/deep-review/SKILL.md   # take the prompt from that file
                     # instead of from the body below (see "Pointing at the
                     # prompt")
---
```

Paths in frontmatter (`fan_out`, `skip_if`) are relative to the run's
artifacts directory — see [Where things get written](../README.md#where-things-get-written).

## Publishing for a human: `{docs}`

`{artifacts}` is where a stage writes for the *next stage*. If your pipeline's
deliverable is a document rather than code, its last stage should also write
for a *person*, and that goes to `{docs}` — the directory named by `--docs-dir`
(default `./orchestra-docs/plans`), which sits outside the timestamped run tree and
outlives it.

Name a subfolder for the subject rather than the run — `{docs}/<ticket-id>/` —
so a reader who knows the ticket can find the answer without knowing when it
was produced, and keep the run's own copy in `{artifacts}` so the record stays
complete either way. Only stages whose prompt actually contains `{docs}` are
granted write access to that directory, so adding the placeholder to your final
stage is also what opens the door; the stages before it stay locked out.

Not every pipeline needs this. `feature` and `bugfix` leave their deliverable in
the workspace as code, and a pipeline that names `{docs}` nowhere never touches
the directory.

## Pointing at the prompt: `redirect`

A stage file doesn't have to *hold* its prompt. `redirect:` takes the template
from another markdown file, so the prose can live where it's already
maintained — most usefully a skill you already have:

```markdown
<!-- my-pipeline/01-review.md -->
---
redirect: ../../.claude/skills/deep-review/SKILL.md
---
Review the diff for correctness bugs and report what breaks. The full checklist
is the deep-review skill; if it isn't on this machine, work from the criteria in
`artifacts/CRITERIA.md` instead.
```

The stage file stays what it was — a number, a name, a shape — so discovery is
still a directory listing. What the engine does with the pointer:

| | |
|---|---|
| **Resolution** | Against the stage file's own directory. `..` and absolute paths are both fine, unlike `fan_out`/`skip_if`: those name files an *agent* wrote mid-run, this names authored content in a folder you chose. |
| **The target's frontmatter** | Cut on its fences and thrown away, keys unread. That's what lets a `SKILL.md` work as-is — its `name:`/`description:` YAML is a different dialect, and reading it as stage options would fail the load on every skill file. |
| **Shape** | Still the stage file's. `repeat`, `fan_out`, `skip_if` and `decide` sit alongside `redirect` and mean exactly what they always did. |
| **Braces** | Everything in the target except `{task}` and the other placeholders becomes literal text, so a JSON sample or a `${VAR}` in a file written for humans can't break rendering halfway through a run. |
| **Chaining** | None. A `redirect:` inside the target leaves with the rest of its frontmatter, so redirects are one hop and can't cycle. |

### The body is the fallback — and it's required

A target may simply not be on this machine: a skill that lives in the
*workspace* repo rather than the pipeline's, a checkout that doesn't have it
yet. When the file is missing, unreadable, or empty, the stage runs **its own
body** instead of failing the run — which is why a redirect stage isn't allowed
to leave that body blank. Write it as the short version of the target: what
this stage is for, and how to get the real prompt. Both `--list` and the run
banner say which one a stage actually got:

```
review [prompt from ../../.claude/skills/deep-review/SKILL.md]
review [../../.claude/skills/deep-review/SKILL.md not found — running this file's own body]
```

## Conditional stages: `skip_if`

`skip_if: <file> contains <text>` skips a stage when a file in the artifacts
directory contains that text. The file is written by an earlier *agent* stage,
so this is how an agent's judgment becomes control flow: it decides, writes the
decision down, and the engine enforces it.

The engine stays pipeline-agnostic — it opens a file and matches a substring;
it knows nothing about what the text means. The meaning stays in the pipeline
folder, the same way `fan_out` only ever reads lines.

**Every outcome is logged, including the one where nothing happens:**

```
[fix] gate: verdict.txt contains 'STOP' — skipping.
[fix] gate: verdict.txt does not contain 'STOP' — running.
[fix] GATE NOT EVALUATED: 'verdict.txt' not found — the stage that writes
      it may have failed. Running the stage anyway.
```

That third line is the point. **A missing or unreadable gate fails open and
says so**, rather than silently skipping the rest of a run. A gate that
quietly stops gating is worse than no gate at all: the pipeline still looks
conditional, so you keep believing a check is protecting you long after it
stopped running — a real, measured failure mode in shipped repair systems,
where one such fallback silently disengaged 53.7% of the time. Skipped stages
are recorded in the run log too, for the same reason.

Substring matching is deliberately forgiving, because an LLM writes the file.
The cost is that one verdict must not be a substring of another —
`STOP` and `PROCEED` are safe; `NOT REPRODUCED` would also match
`NOT REPRODUCED BUT FREQUENT` and skip on exactly the case meant to continue.

## Deciding what's next: every plain stage routes itself

`skip_if` only ever skips *forward*. Routing is the general version — and it
belongs to **every plain stage**, not one special decider. As its last act, a
stage writes `decision.json` saying where the run goes from here:

| decision | meaning | replaces |
|---|---|---|
| `{"next": "advance"}` | go to the next stage (also the default if it writes nothing) | the plain walk |
| `{"next": "again"}` | run this same stage again | a fixed `repeat: N` |
| `{"next": "<stage>"}` | jump to a named stage (back = loop, forward = skip) | — |
| `{"done": true}` | go straight to the terminal stage and end | — |

`advance`, `again`, and `done` need no declaration. Jumping to another *named*
stage requires listing it in that stage's `decide:` frontmatter, so reading a
stage file still tells you exactly where it can send the run. It's the same
boundary as `fan_out` and `skip_if` — the agent decides and writes it down, the
engine only reads a file and acts — with a routing choice as the payload.

This is what lets the AI, not a fixed count, decide a stage needs another pass.
bugfix's `reproduce` runs *once* and calls `again` only if the reproduction
isn't solid yet; `verify` reads whether the fix truly held and, if not, loops
back to re-diagnose, or (for a genuinely different root cause) re-localize —
instead of marching to a postmortem that says "didn't work." Because each stage
is a Claude Code session, a stage deciding whether to route back can open the
earlier stages' full transcripts by their session IDs, not just their one-line
summaries, before choosing — and it is not alone in that: **every** stage, a
terminal write-up and a fan-out worker included, is handed the same index of
prior transcripts and told to pull specific facts from them (grep, don't
ingest), so a detail an earlier summary dropped is never actually lost.

Two guarantees keep a backward edge from becoming a runaway:

- **It always halts.** A hop budget (`--max-hops`, default 2× the stage count)
  caps total stage executions; once reached, the run jumps straight to the final
  stage. A clean forward run spends ~1× and never nears it.
- **It always lands somewhere real.** The last stage never routes and is never a
  jump target, so every run — however the middle loops, and even on budget
  exhaustion — ends on the terminal stage that writes the handback.

A decision that can't be honored **fails safe**, like the gate: a missing,
malformed, out-of-bounds, or undeclared-target `decision.json` is logged and the
run advances, rather than looping or jumping somewhere unauthorized. (`fan_out`
stages don't route — parallel agents would race to write the one decision — and
a fixed `repeat: N` is a shape, not a decision, so the two can't be combined.)

**`handoff` controls what a repeating pass can see.** The default, `last`, is
a chain: pass 3 sees pass 2 and nothing before it. With `handoff: all` the
chain accumulates — pass 3 receives pass 1 *and* pass 2, each as its own block
with its own session ID:

```
search → summary + session A
search (sees A) → summary + sessions A, B
search (sees A + B) → summary + sessions A, B, C
```

`handoff` controls *scope* — how many earlier reports the `read_handoff` tool
serves a pass — not reachability. Every stage also receives a compact index of all prior
sessions' transcripts regardless of scope, so an older pass under `last` is
leaner-summarized, not unreachable: its transcript is still one grep away. Use
`all` when passes should actively build on each other's summaries (the research
pipeline's `search` does); leave it `last` when each pass only needs to refine
the one before it, which keeps prompts lean on long chains.

`repeat` and `fan_out` compose: a fan-out stage with `repeat: 2` gives every
item its own two-pass chain (pass 2 sees pass 1's findings and refines them).
Prompts in such stages get three extra placeholders: `{iteration}`, `{repeat}`
and `{item}`. After a fan-out stage, the next stage receives all per-item
summaries merged into one handoff. If some items fail the loop continues with
the survivors; it stops only when every item failed.
