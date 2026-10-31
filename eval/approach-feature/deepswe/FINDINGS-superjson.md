# `approach+feature` on `superjson-error-stack-serialization`

**Result: reward 0. f2p 79/80, p2p 116/116.** One trial, Opus 5.5, 1h29m.

## Why this task

On the leaderboard, Opus 5 passes 3 of 20 trials and its `max` configuration
passes 0 of 4. Across all models it is 100 of 274. Most Opus 5 misses land at
77–79 of 80, so the task is mostly built correctly and fails on a few
interpretation calls.

## Configuration

This run used the same settings as
[FINDINGS-obsidian-linter.md](FINDINGS-obsidian-linter.md): `pipeline=approach+feature`,
`agent_timeout_sec=21000`, bypassPermissions, OAuth, and egress allowlisted to
`api.anthropic.com` only.

## Timeline

| Pipeline | Stages | Wall clock |
|---|---|---|
| `approach` | intake → ground → frame → option ×3 → compare → reuse ×9 → reconcile → spec | 06:45 → 07:09 (≈23m) |
| `feature` | explore → pin → design → build → test → reread → verify → build → test → reread → verify → integrate | 07:09 → 08:13 (≈64m) |

Verify sent the work back once and then passed it, so the hop cap was never
reached. Token use was 21.4M prompt (19.7M of it cached) and 522k completion.

## The one failure

```
✗ [f2p] src/error-stack.test.ts: Error Stack – additional public API behavior
        > sanitizeMessage is NOT applied to cause errors that fail classFilter
```

Each stage of the chain handled this as follows:

1. **The ticket contradicts itself.** One sentence says "`classFilter` restricts
   stack processing **and sanitization** to errors with matching `.name`". The
   next says `sanitizeMessage` applies "to the error's own message **and to
   every kept cause message**".
2. **`frame` found the conflict.** It listed it as an open question: "are the
   cause messages of a classFilter-missed error sanitized?"
3. **The options split.** Option A let the root's match govern every cause.
   Option C judged each cause by its own `.name`. **C is what the hidden tests
   assert.**
4. **`compare` merged the two readings instead of choosing one.** It
   recommended a union: sanitize a cause if the root matched *or* the cause
   matches. Its reason was that the union "satisfies 'every kept cause
   message' literally, and never leaves PII that either reading would remove."
   That is the safest reading for privacy, but it fails the test. A cause that
   misses `classFilter` still gets sanitized whenever its root matched.
5. **`spec` recorded the union as an invariant without escalating it.** It
   became SPEC invariant I-11, marked "inferred". `compare` raised the
   neighbouring hook-gating choice as an "open question for a human", but it
   did not raise this one. Nothing about the union reached `ESCALATIONS.md`.
6. **`feature` built exactly what the spec said.** Verify had no reason to
   question an invariant it had been told to implement. The ticket-wins
   override never applied either, because the ticket itself supports both
   readings.

## What this says about the chain

- **`approach` found the exact ambiguity the task hinges on, and one of its
  options had the right answer.** The failure came at the decision step, not
  in exploration.
- **The miss was caused by hedging.** When two readings conflict, the union
  satisfies neither of them exactly. A hidden test written for either reading
  can fail it, and here one did. A rule for `compare` might be: when options
  split on a behaviour the ticket states, pick one reading and escalate it.
  Don't merge them.
- **An ambiguity labelled "inferred" in the spec is effectively invisible
  downstream.** The ticket-wins rule in `_with_spec` only covers cases where
  the spec and the ticket disagree. Here the spec settled a question the
  ticket left open.
- **The chain landed where the leaderboard's typical Opus 5 miss lands
  (79/80).** See the baseline below. Compare the obsidian task, where the chain
  passed and Opus 5 passes 1 in 20.

## Baseline: one Claude Code session

This baseline uses Pier's built-in `-a claude-code` with Opus 5.5. Model, OAuth,
sandbox and `--agent-timeout-multiplier 2` are the same as the chain; it is one
trial, run in job `jobs/superjson-baseline/2026-10-27__17-04-37`.

| Arm | f2p | Wall clock | Prompt / cached / completion tokens | Failing test |
|---|---|---|---|---|
| `approach+feature` | 79/80 | 89m | 21.4M / 19.7M / 522k | sanitizeMessage is NOT applied to cause errors that fail classFilter |
| single session | 79/80 | 5.5m | 643k / 594k / 29k | string mode applies redactPaths, then maxStackLines, then stripInternalFrames |

Both arms end on the same score but fail **different** tests, and each one
passes the test the other fails:

- **The baseline got the step order wrong.** The ticket gives string mode and
  frames mode different orders for applying their steps. The chain's `intake`
  stage named exactly that ordering as a detail worth checking, and the chain
  built it correctly. The baseline missed it.
- **The baseline got cause sanitization right.** It made the direct call and
  judged each cause by its own `.name`. The chain instead produced the merged
  "union" rule described above.

The chain found more of the ticket's traps but then talked itself into the
wrong answer on one of them. It also cost about 33× the tokens and 16× the wall
clock, for the same score.

A single trial per arm says nothing about pass rates. What it does show is that
a pipeline which reads more carefully can still fail, if it then resolves an
ambiguity by hedging.

## Artifacts

Job directory:
`~/Documents/GitHub/deepswe-scratch/jobs/superjson-approach-feature/2026-10-27__14-44-24`.
The approach run is under `agent/loop-run/20261027-064554/artifacts/` (see
`DECISION.md` §6 item 2 and `SPEC.md` I-11). The feature run is under
`agent/loop-run/20261027-070918/`.

## Re-run with the "plainest reading" prompt change

The change being tested adds two rules, both uncommitted at the time of the
run:

- **`05-compare.md`:** when options read the ticket differently, pick the
  plainest reading and never merge two readings into one.
- **`08-spec.md`:** an inferred criterion may not add behavior. Anything
  beyond the ticket goes under "Open points" as a note.

The re-run used the same settings as the first chain run and is recorded in job
`jobs/superjson-approach-feature-plainread/2026-10-27__21-43-15`.

**Result: reward 0. f2p 78/80**, one fewer than before; p2p 116/116. Wall
clock was 1h57m. Tokens were 19.3M prompt (17.6M of it cached) and 519k
completion.

```
✗ sanitizeMessage is NOT applied to cause errors that fail classFilter
✗ registerErrorStackProcessor fires even when no errorStack option is set
```

What the new rules did:

- **No merge.** `compare` picked a single reading, as the rule told it to. But
  it picked option A's reading ("a matching root sanitizes all its kept
  causes"), arguing it is "the only reading in which 'every kept cause
  message' says anything." That is just as wrong as the union. **Both readings
  are literal.** "Plainest" gives no way to choose between them, so the
  heuristic doesn't settle the question.
- **Hook gating got firmer, and that cost the second test.** In the first
  chain run, "hooks only when `errorStack` is set" went into the spec as I-13,
  but carried an explicit "*Open question for the requester*". `feature`
  treated it as open and built hooks that fire whenever registered, which is
  what the test wants. This time `compare` dutifully "picked one" (gated, on
  the strength of "Omitting it leaves existing Error behavior unchanged"), and
  `spec` wrote that in as an `inferred` criterion. The open-points note
  pointed back at it rather than leaving it undecided. `feature` built the
  gate.

The lesson is that "pick one, don't hedge" turns open questions into
commitments, and a committed wrong reading is worse than an open one. The
first run lost to hedging. This run lost to deciding. The thing that worked
in both runs was **leaving a point undecided in the spec** so that `feature`
made the call while looking at the code. That is the obsidian pattern again.
A better rule might be the opposite of this change: when the ticket doesn't
settle a behavior, `approach` should present both readings and decide neither.
That is one trial, though, and the variance is clearly large.

## Re-run with the "open points" prompt change

The prompts changed again, uncommitted at the time of the run:

- **`05-compare.md`:** `compare` decides the design but not the reading. Where
  options read the ticket differently, it lists every reading against the
  ticket's words and how the design accommodates each, and it chooses none.
- **`08-spec.md`:** points the ticket leaves open go only under "Open points,
  not decided by the ticket", and the implementing run decides them. They may
  not reappear as inferred criteria, invariants or errors.

The re-run is recorded in job
`jobs/superjson-approach-feature-openpoints/2026-10-28__00-03-59`. The host
slept for about six hours during `pin`, and the container was paused for that
time, so the wall clock is meaningless. The run was awake for about 2h15m.
Tokens were 28.0M prompt (25.9M of it cached) and 630k completion. Verify sent
the work back twice. The hop cap was not hit.

**Result: reward 0. f2p 79/80, p2p 116/116.**

```
✗ sanitizeMessage is NOT applied to cause errors that fail classFilter
```

- **The prompts worked as written.** The spec carried about 11 open points,
  including both disputed ones (hook without config as Open point 1, cause
  sanitization as Open point 2), and settled neither.
- **The hook question was won back.** `feature`'s design, looking at the code,
  chose "the hook fires by error class name" even without config. That matches
  the test, which run 2 lost when `compare` decided it.
- **Cause sanitization was lost again.** `feature` chose "sanitized if its
  parent was sanitized or its own `.name` matches", the same union run 1's
  `compare` invented.
- **Step order held.** It was correct in all three chain runs.

## The four runs together

| Run | Sanitize-cause test | Hook-without-config test | Step-order test | f2p |
|---|---|---|---|---|
| single session | ✅ (own name) | ✅ | ❌ | 79/80 |
| chain, original prompts | ❌ (union, set by `compare`) | ✅ (left open, `feature` chose) | ✅ | 79/80 |
| chain, "plainest reading" | ❌ (root decides, set by `compare`) | ❌ (set by `compare`) | ✅ | 78/80 |
| chain, "open points" | ❌ (union, set by `feature`) | ✅ (left open, `feature` chose) | ✅ | 79/80 |

Three things follow:

- **Leaving a point open is better than having `approach` decide it.** Every
  time `approach` decided the hook question, it got it wrong. Every time it
  left it open, `feature` got it right. Of the three chain variants, the "open
  points" prompts are the best so far. They tie the original on score, but
  get there for a sounder reason.
- **On cause sanitization, the chain's readings lean one way.** Three chain
  runs gave three answers from two different stages, and every one of them
  sanitizes a cause that the test says should be left alone. The single
  session read it the other way. More reasoning about this sentence pushes
  Opus 5.5 towards the more protective reading. No change to where the
  decision gets made has moved it. It is probably not something prompt
  structure can fix without coaching the model towards the answer.
- **The chain reliably fixes the careless miss (step order), and reliably
  loses the protective one.** On this task the two cancel out at 79/80. On
  obsidian, the careless miss was the only one, so the chain came out ahead.
