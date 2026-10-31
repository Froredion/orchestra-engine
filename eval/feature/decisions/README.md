# Decision replay — unit tests for prompt rules

A pipeline prompt is the load-bearing logic of this engine and the only
component with no test suite. Today the way to learn what a rule change did is
to run a full trial: hours, tens of dollars, and an answer that arrives days
later mixed in with everything else that moved. `FINDINGS-meriyah.md` names that
as the binding constraint on improving the pipeline, more than any individual
rule.

This runs the **decision** instead of the run. A case freezes the inputs a stage
actually had at the moment it made a call — the request, the scope entry, the
stage documents on disk, the source tree it could read — and asks the current
prompts to make that call again, in one short session with no build and no
suite. Minutes and cents, against hours and $50.

```sh
.venv/bin/python -m eval.feature.decisions.runner using-bracket -n 5 --model claude-opus-5
```

## What a pass and a fail mean

**A rule that cannot flip a decision we already know the prompts get wrong is
not worth a live trial.** That filter is the whole point.

**Passing here is not passing.** A frozen decision is easier than a live one:
the real stage builds its own context over an hour, writes its own probes, and
can wander somewhere this replay never goes. Treat a flip as permission to spend
the $50, never as the result.

The report says which of the three it saw:

| Reading | Means |
|---|---|
| reproduces the live failure | every replay landed where the live run did — the rules do not bind here |
| unstable | the decision is a coin-flip, not a rule. See below, because this is the interesting one |
| flipped every time | worth a live trial |
| nothing measured / partial | replays died or went unparsed. Not evidence about the prompts |

## Case format

A case is a directory under `cases/`, with a `case.md` whose frontmatter uses
the same `--- key: value ---` block stage files use:

| Key | |
|---|---|
| `rules` | stage prompt file(s) under test, e.g. `06-verify.md` |
| `pipeline` | which pipeline they come from (default `feature`) |
| `task` | the file holding the run's task text, filling `{task}` |
| `documents` | files copied into the replay's artifacts directory, under their own names — the stage reads them there, as it would live |
| `handoff` | optional; served through the engine's own handoff tool, not spliced into the prompt |
| `answers` | the decision tokens, e.g. `hold, route-back` |
| `correct` | which one the answer key supports |
| `observed` | what live runs actually did, for the report |
| `repo` / `commit` / `sparse` | source tree to check out, cached by commit |

The body of `case.md` is the question. The runner renders the stage file exactly
the way `Pipeline.build_prompt` does — frontmatter split off, the same
placeholders filled — so what arrives is the prompt the engine would have sent.

Two disciplines make a case worth anything:

- **Give it the evidence, not the answer.** The stage gets a source tree so it
  can find precedent itself. Hand it a curated list of facts and every rule
  about "go and look" passes trivially, because the finding it was supposed to
  make has been made for it.
- **Include what pushed the live run the wrong way.** The first draft of
  `using-bracket` left out the blind reading and held 1/1 — it was measuring an
  easier decision than the one that failed. Add the document and the failure
  reproduces.

## The seeded case: `using-bracket`

The moment the meriyah run went wrong. VERIFY pass 2 decides whether to route
back a stated requirement (AC-12, `cannot have destructuring`) because
implementing it over `[` makes some previously-valid JavaScript a syntax error
under `next: true`.

- `hold` is correct — the answer key requires the `[` rejection, and VERIFY
  pass 1 reached `hold` with the right reasoning before pass 2 superseded it.
- `route-back` is what the live run did, and what every one of the 16 recorded
  trials ends up shipping.

**First result — 5 replays, `claude-opus-5`, $4.27:**

```
   4 / 5  hold          <- correct
   1 / 5  route-back    <- what live runs did
```

Not a confident wrong prior. The dissenting replay reproduces the live argument
closely, including checking itself against the guard rule in `06-verify.md` and
concluding the warning's signature is absent.

That reframes the loss. If a single pass holds ~80% of the time, but a
route-back **deletes the behaviour and re-pins the tests against it** — the
escalation record measured 20 tests going red if the gate is changed back — then
`hold` is provisional and `route-back` is permanent. A run with several passes
converges on the wrong answer without the model ever being confidently wrong.
On that reading the rule to add is not another prohibition but a **ratchet**:
something that makes reversing an earlier stage's correct call cost more than
making it.

Caveats, in order of size: n=5, so 4/5 is consistent with anything from a coin
flip to near-certain. The replay is easier than the live decision. One case, one
task, and the task whose prompts were tuned against it.

## The control: `await-using-fallthrough`

A case is only worth something next to one with the opposite answer. Otherwise a
rule that says *"never reverse an earlier stage's call"* scores 5/5 on
`using-bracket` and ships something that never routes a real defect again.

So: VERIFY pass 1 on the same run, deciding a defect it **correctly routed** —
the `await using` async-context check sits before the declaration predicate, so
`function f(){ await using; }` reports a declaration error the user never wrote.
`correct: route-back`, and the live run agrees. Ground truth here is weaker than
`using-bracket`'s: no hidden test covers it. It rests on the run routing it,
BUILD fixing it, and the fix costing nothing.

**Result — 5 replays, `claude-opus-5`, $4.42:**

```
   4 / 5  hold
   1 / 5  route-back    <- correct, what live runs did
```

**The control is broken, and that is the finding.** Both cases come out
`hold`-heavy: 4/5 correct on one, 1/5 on the other, in the same direction. Read
together they do not say "the rules do not bind." They say **verify holds too
much**, and the same rule produces both results:

> behaviour that differs only when the new one is *on* is the feature doing its
> job — `06-verify.md`

In `using-bracket` that rule correctly stops a reversal. In
`await-using-fallthrough` it stops a route-back that should happen — the replays
cite it by name. And the counterweight, *"it fails either way, only the wording
differs" is the producer-side argument in its purest form*, lives in
`02-design.md` and has no equivalent in the verify prompt.

That kills the ratchet idea before it cost a live trial. A rule making reversal
expensive pushes further toward `hold` and breaks this control worse. Which is
the entire job of a control case.

One honest note on how this number was produced: the first baseline was 0/5. The
case's `STATE.md` had truncated a clause from the source document — keeping *"it
is not the finished fix"* and dropping *"but it localises the defect and shows
the design does not need revisiting"* — which reads as a cost of fixing rather
than a caveat about a sketch. Restoring it moved 0/5 to 1/5. One fix, one re-run,
both numbers recorded. Tuning a case until it gives the wanted answer is the same
error as tuning prompts against one task, and it is easier to commit here because
it is cheap.

## Ruling the harness out: `--ablate`

A tool that grades prompts has to be able to rule *itself* out first. `--ablate`
makes a one-variable change to the replay while holding the case fixed:

| | `await-using-fallthrough`, n=5 each |
|---|---|
| `none` | **1/5** correct |
| `may-measure` — drop *"you are not being asked to re-measure it, and you cannot"* | **0/5** |
| `flip-answers` — present `route-back` first | **2/5** |

Pooled: **3/15**. Every arm is consistent with one underlying rate near 20%, so
neither suspect explains the lean. Both are ruled out; the lean is elsewhere.

## What the two cases actually establish — less than it first looked

Both cases lean `hold`. On `using-bracket` that lean happens to agree with the
correct answer; on the control it does not. **Until the lean is explained, neither
number can be read as a statement about the prompts** — including the 4/5 that
looked like good news. The harness is not yet usable for testing a rule.

Three live explanations, and this pair of cases cannot separate them:

1. **Verify holds too much.** The rule *"behaviour that differs only when the new
   one is on is the feature doing its job"* (`06-verify.md`) is cited by name in
   replays of *both* cases — correctly in one, wrongly in the other. Its
   counterweight, *"it fails either way, only the wording differs is the
   producer-side argument in its purest form"*, lives in `02-design.md` and has no
   equivalent in the verify prompt.
2. **The replay under-routes because it did not make the measurements.** The live
   verify pass *found* this defect by running its own probes; a replay is handed
   it. That is the gap named under Limits below, and it is the one that cannot be
   closed with the artifacts on disk.
3. **The control's ground truth is contestable.** Its `correct: route-back` rests
   on the run routing it and BUILD fixing it cheaply — not on a test. The replays'
   argument (every input errors either way, only wording moved, no stated AC
   covers the wording) is not stupid. If this call is genuinely marginal, ~20% is
   a defensible rate and there is no bias to explain.

Explanation 3 is the fixable one, and it is a flaw in how the control was chosen:
**a control needs ground truth that does not depend on the pipeline's own
judgement.** `using-bracket` has that — the hidden tests settle it. The control
does not. The next control should be a decision where routing back is settled by
the answer key, so "correct" is not the harness author's opinion.

## The third case: `scope-of-of`, and what it says about the method

Built to A/B a proposed `01-explore.md` rule — *a claim about another
implementation is not grounds for narrowing a stated criterion unless you ran
it*. The rule targets a **verified** error: `SCOPE.md` excluded
`for (using of of y)` from a stated criterion because *"acorn rejects it"*, and
acorn 8.18 (which implements the proposal — `using x = 1` parses as
`VariableDeclaration kind: using`) parses it fine as a `ForOfStatement`. The
claim is false and no stage in 18 runs re-checked it.

Ground truth here is a fact anyone can re-run, not the harness author's
judgement — the quality `await-using-fallthrough` lacks.

**Baseline, before the rule — 5 replays: `5/5 include` (correct).** The live run
excluded it in all 18 trials. No headroom, so the rule cannot be A/B'd here.

That makes **three for three** where the replay diverges from live:

| case | live | replay |
|---|---|---|
| `using-bracket` | route-back (wrong) | 4/5 hold (right) |
| `await-using-fallthrough` | route-back (right) | 1/5 route-back (wrong) |
| `scope-of-of` | exclude (wrong) | 5/5 include (right) |

The simplest explanation that fits all three: **isolating a decision makes it
easier than making it inside the real task.** Live, this one is a single line
inside a document enumerating nineteen criteria; here it is the only question
asked. Two of the three then come out right, and the one that does not is the
case where the stage had to *find* a defect rather than judge a question — and
the replay was handed the measurement instead of discovering it.

If that is what is happening, no ablation fixes it, because it is the premise of
the method rather than a flaw in this implementation. A frozen decision is not a
smaller version of the live decision; it is a different and easier one. That
bounds what this rig can ever be: useful for catching a rule that makes an
isolated decision *worse*, not for showing one makes the real run better.

## Limits

- **The replay reads measurements; a live stage makes them.** A stage that
  measured something owns it; one handed a number discounts it. Closing this means
  replaying against the actual branch state, and it is not reconstructable: the
  trial archives `model.patch`, the *final* post-reversal tree, and the
  intermediate heads exist nowhere on disk. Making this possible is a change to
  the Pier adapter — archive per-stage patches — not something retrofittable to
  finished runs.
- **n=5 per arm** resolves only large effects. 1/5 against 2/5 is nothing.
- **One task**, and the task whose prompts were tuned against it.

## Tests

```sh
.venv/bin/pytest tests/test_decisions.py
```

They need no API key and no network — they exercise case loading, prompt
rendering, decision parsing, and the report's readings.
