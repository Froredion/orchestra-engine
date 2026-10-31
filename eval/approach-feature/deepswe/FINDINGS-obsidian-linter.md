# Findings: `approach+feature` on `obsidian-linter-link-format-conversion`

The first run of the chained arm, where `approach` decides how to build the
ticket and `feature` builds from its spec, cleared a task that a single Opus 5
session clears 1 time in 20. The trial scored **reward 1, f2p 60/60, p2p
1131/1131** in 2h 31m, which is inside the task's own 3h agent budget.

**This is one trial.** It shows the chain can solve the task. It does not give a
pass rate, and no single-session Opus 5.5 baseline has been run on this host, so
part of the gap could be the newer model. Read it as a mechanism illustration
until [Next](#next) is done.

Setup and flags are in [`README.md`](../../feature/deepswe/README.md). The companion measurements are
[`FINDINGS.md`](../../feature/deepswe/FINDINGS.md) (prometheus, `feature` alone) and
[`FINDINGS-meriyah.md`](../../feature/deepswe/FINDINGS-meriyah.md).

## The task

`obsidian-linter` is an Obsidian plugin written in TypeScript. The ticket adds a
Content rule, `link-style`, that converts between Obsidian wiki links/embeds
(`[[t|d]]`, `![[f.png]]`) and markdown links/images (`[d](t)`, `![alt](f.png)`).
It spells out about 20 mapping and escaping rules (`<...>` destinations,
balanced parentheses, backslash escapes, nested `[]` in labels, titles,
external targets), plus a list of regions where nothing may be converted:
frontmatter, code, math, HTML, Templater, `%%` comments, tables, and ignore
blocks.

The ticket states every rule; the hard part is the edges between them. That
leaves a one-pass agent most of the way there and still failing.

## Why this task

It was picked for headroom, which the meriyah run showed a comparison needs.
The figures below are from the DeepSWE v1.1 board's trial data
(`deepswe.datacurve.ai/artifacts/v1.1/trials.json`), all `mini-swe-agent`:

| | Passed | Typical miss |
|---|---|---|
| Opus 5, all five efforts | **1 / 20** (max: 0/4) | 57–59 / 60 f2p, p2p intact |
| All models | 69 / 280 (25%) | |

Opus 5 does not break anything here. It just misses two or three edge cases
every time, and that is the kind of miss a verify loop can catch.

## Configuration under test

| | |
|---|---|
| Arm | `--ak pipeline=approach+feature` (the chain added to [`pier_agent.py`](../../feature/deepswe/pier_agent.py) for this run) |
| Model | `claude-opus-5-5`, CLI default effort |
| Budget | `--agent-timeout-multiplier 2`, `--ak agent_timeout_sec=21000` |
| Permissions | `bypassPermissions` + `dangerously_skip_permissions` |
| Auth | `CLAUDE_CODE_OAUTH_TOKEN` (subscription) |
| Trials | `-k 1 -n 1` |
| Engine | `92527b8` **plus uncommitted adapter changes** (the chain and the offline install) |

How the chain works: `approach` runs first and gets at most a third of the
remaining budget (`CHAIN_FIRST_SHARE`). It publishes `SPEC.md` under
`/logs/loop-run/docs/<slug>/`. `feature` then gets the original ticket text,
followed by a pointer to that spec. Nobody can rule on `approach`'s escalations
in an unattended trial, so the appended text says the ticket wins wherever the
two disagree. The verifier grades against the ticket, not the spec. If
`approach` fails or declines, `feature` runs on the bare ticket.

## Results

| Pipeline | Wall time | Stages run | Outcome |
|---|---|---|---|
| `approach` | 24m 37s | 8, no hops | DECISION: option A (rule-local scanner) in C's shape; SPEC.md + REUSE.md published |
| `feature` | 2h 5m | 8 + 3 verify-driven hops (16, the cap) | Committed rule, tests, docs, locale strings |
| **Graded** | **2h 31m job** | | **reward 1, f2p 60/60, p2p 1131/1131, partial 1.0** |

Tokens: 35.7M prompt (33.5M of it cache reads), 624k completion, 639 steps. No
`total_cost_usd`, because the run used subscription auth.

## What the verify loop caught

`feature`'s verify stage sent the work back three times. Each time it ran its
own probes against the ticket's stated mappings, and each time it found a real
input the stated mapping gets wrong:

1. **Pass 1 → design.** An image nested inside a link label, `[![a](f.png)](t)`,
   was left unchanged under `image=wiki`. Under `{wiki, wiki}` it only converted
   on a second run, which made the rule non-idempotent. Rev. 2 fixed this
   without recursion, because the recursive version overflowed at N=3000.
2. **Pass 2 → design.** A guard in `link-style.ts:88` that looked at neighbouring
   characters dropped valid conversions: `note: [see [[Book]]]` and
   `[x ![[f.png]]](t)` stayed unchanged. Rev. 3 narrowed the guard to the
   malformed `[[[` and unmatched-`]` shapes.
3. **Pass 3 → design, overridden.** The unmatched-`]` shape still withheld the
   mapping in cases like `[[a]]]`, `see [[Book]]] typo` and `![[f.png]]]`. With
   the hop budget spent (16), the engine logged
   `DECISION OVERRIDDEN: asked for 'design', but the hop budget (16) is spent —
   advancing` and went on to `integrate`.

So the graded patch still has a defect that verify had found: a wiki link
directly followed by a stray `]` is not converted. The hidden tests do not
cover that input, so the reward is real, but **the patch is not as good as the
reward says**. Nobody has checked whether one more hop would have fixed it
without breaking something else.

Before any of this, `pin` wrote 155 tests for the requirements. All 155 passed
at every revision, and none of their claims changed. The pipeline's own suite
grew from 1,177 baseline tests to 1,419, and all of them passed at the end.

## What `approach` contributed, as far as one run shows

`approach` spent 25 minutes and wrote no code. What it handed `feature`:

- A choice between three designs, rated 22/22 against the stated
  criteria: a rule-local scanner, an mdast-hybrid, and a shared link layer. It
  picked the scanner, built so it could later become the shared layer.
- Ten reuse findings, four of which swapped new code for existing helpers
  (for example `unescapeMarkdownSpecialCharacters`).
- Three findings it carried as notes, not changes: the repo's default branch is
  `master`, not `main`; `%%` comments have no inline ignore type; and a literal
  wiki→markdown conversion breaks targets that contain spaces.

`feature`'s design stage then flagged 8 conflicts between the ticket and the
SPEC, and resolved every one in the ticket's favour, as the handoff text told it
to. Whether `approach` made the pass more likely can't be told from one trial
with no `feature`-only control on this task. That control is in [Next](#next).

## Defects this run exposed in the harness

Two problems had to be fixed before this trial could start. Both were in the
harness, not the pipeline:

- **The runtime install hit PyPI through the egress proxy, and the proxy
  refused it with `403 Forbidden`.** The agent container's allowlist is now just
  `api.anthropic.com`. The adapter now downloads the engine's dependencies into
  `/opt/orchestra-engine-wheels` at image build time, when there is network, and
  installs with `--no-index` at runtime (`WHEELHOUSE`). Widening the allowlist
  would have given the staged arm network access the baseline doesn't have.
- **An OAuth token in `ANTHROPIC_API_KEY` fails with "Invalid API key".** The
  CLI sends that variable as an API key. An `sk-ant-oat01…` value has to go in
  `CLAUDE_CODE_OAUTH_TOKEN`. One trial was lost to this, at no cost.

## Baseline: one Claude Code session

This baseline uses Pier's built-in `-a claude-code` with Opus 5.5. Model, OAuth,
sandbox and `--agent-timeout-multiplier 2` are the same as the chain; it is one
trial, run in job `jobs/obsidian-baseline/2026-10-27__17-24-06`.

| Arm | Reward | f2p | p2p | Wall clock | Prompt / cached / completion tokens |
|---|---|---|---|---|---|
| `approach+feature` | **1** | 60/60 | 1131/1131 | 2h31m | 35.7M / 33.5M / 624k |
| single session | 0 | 59/60 | 1131/1131 | 6.5m | 957k / 907k / 21k |

The baseline fails one test:
`Link Style Wiki link with spaces in target is preserved`.

`approach` predicted exactly this failure. Its notes, carried to `feature` (see
the section above), include "a literal wiki→markdown conversion breaks targets
that contain spaces". The single session converted literally and broke on that
case. The chain was told about it ahead of time and handled it.

This is the cleanest case so far of `approach` paying for itself: it found a
trap that the ticket doesn't state, and the hidden tests check for it. It is
still a single trial per arm, and the chain cost about 37× the tokens and 23×
the wall clock.

## Limits

- **n = 1.** On a task Opus 5 passes 5% of the time, one pass is strong evidence
  the chain *can* solve it. It says almost nothing about how *often*.
- **A single same-model baseline.** It failed on the case `approach` flagged
  (above). One trial per arm can't give a rate.
- **No `feature`-only control.** The chain's value over `feature` alone is
  untested here.
- **Subscription auth.** There is no cost figure, and the chain, with its many
  sessions, is more exposed to throttling than a baseline would be.
- **The working tree was uncommitted at launch.** Commit the adapter changes and
  record that hash before quoting this result.

## Next

1. **More single-session Opus 5.5 baselines on this task, at the same budget**
   (`-a claude-code -m anthropic/claude-opus-5-5 --agent-timeout-multiplier 2
   -k 4`). One has run (above, 59/60). More runs would turn that into a rate.
2. **Three more chain trials** (`-k 3`), to turn 1/1 into a rate.
3. **`feature` alone on this task**, to separate what `approach` adds.
4. **The hop cap.** Pass 3 found a real defect and was overridden. Try
   `--ak max_hops=20` on one trial, and check whether the extra pass fixes the
   stray-`]` case without regressing.
