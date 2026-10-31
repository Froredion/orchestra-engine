# Findings: the `feature` pipeline on `meriyah-explicit-resource-declarations`

A staged run and a single Claude Code session first scored *identically* on this
task — same tests failed, same regression result — with the staged arm spending
five times as much to get there. Fifteen trials never once cleared 46/49.

Diagnosing why produced a new stage, and the arm now reaches 48. The remaining
failure turns out to be a defect in the task: it asserts behaviour that neither
V8 nor the shipped upstream implementation of this same feature has, and the
reference solution buys it by breaking working code that nothing in the
pass-to-pass suite covers.

Both halves are the interesting part, and neither is "the model could not do
it."

Setup, flags, and how to reproduce: [`README.md`](README.md). The companion
measurement, on a task the prompts *were* tuned against, is
[`FINDINGS.md`](FINDINGS.md).

## The task

`meriyah` is a JavaScript parser written in TypeScript. The request adds
`using` and `await using` declarations — a real language feature for values that
clean themselves up — behind a `next: true` option.

The difficulty is that `using` is not a reserved word. It is an ordinary
identifier that existing code already uses, so the parser must decide from
context which meaning applies, and the two readings are spelled identically:

```js
using [a] = b;      // a declaration destructuring an array? (request: reject it)
using[a] = b;       // or the variable `using`, indexed at a, assigned b?
```

JavaScript ignores the space. Both lines are the same input.

Its second property is scale: **49 fail-to-pass tests against 51,469
pass-to-pass tests.** Adding a contextual keyword changes how every existing
program tokenises, so the regression surface is the whole language.

## Configuration under test

| | |
|---|---|
| Pipeline | `feature` — explore → design → build → test → reread → verify → integrate for the first 15 trials; **pin** inserted after explore for the last 4 |
| Model | `claude-opus-5`, both arms |
| Thinking | `{"type": "adaptive", "display": "summarized"}` |
| Effort | CLI default (`xhigh`), left unset on both arms |
| Permissions | `bypassPermissions` + `dangerously_skip_permissions` |
| Budget | `--agent-timeout-multiplier 4`, `--ak agent_timeout_sec=21000` |
| Host | Windows 11, Docker on WSL2, native amd64 |

## Results

Harness validation, both without an API key:

| Arm | Result | Time |
|---|---|---|
| `-a nop` | `reward 0`, f2p **0/49**, p2p **51469/51469** | 3m 47s |
| `-a oracle` | `reward 1`, f2p **49/49**, p2p **51469/51469** | 3m 21s |

The reference patch is 24.8 KB and clears every test without regressing
anything, so the bar is passable.

**Nineteen staged trials and one baseline trial**, spread over eight days and
many pipeline revisions. The staged arm splits cleanly at the point a `pin`
stage was added between `explore` and `design`:

| Arm | Trials | f2p | p2p | Sessions | Agent time | Spend |
|---|---|---|---|---|---|---|
| staged, before `pin` | 15 | 45 ×4, **46 ×11** | 51469/51469, every trial | 12–15 | 2h13m – 3h01m | — |
| staged, after `pin` | 4 | 45, **47**, **48 ×2** | 51469/51469, every trial | 8–16 | 1h50m – 3h00m | — |
| baseline | 1 | **45** | 51469/51469 | 1 | **33 min** | — |

Roughly **$1,374** across every logged pass, including the cancelled and
re-run ones. `reward` is 0 in all twenty: it requires 49/49.

Two further trials scored a flat 0/49 and are excluded above. Both were
cancelled with `docker stop`, which kills the process tree so the adapter's
`finally` autocommit never runs and an empty patch is graded. A cancelled trial
is not a datapoint, and it does not look like one only if you check.

Head to head on the same day, same host, same model:

| | baseline | staged |
|---|---|---|
| f2p | 45/49 | 45/49 |
| failing tests | *the same four* | *the same four* |
| p2p | 51469/51469 | 51469/51469 |
| agent time | 33 min | 180 min |
| sessions | 1 | 15 |
| cost | — | $74.27 |
| patch | 54 KB | 115 KB |

Not comparable — identical, down to the test names, with the staged arm
producing twice the diff.

### Reading the numbers

Two ways this task's reporting misleads, both of which cost time here:

- **`partial` is meaningless.** With p2p outnumbering f2p a thousand to one, a
  `nop` that changes nothing scores `partial 0.999`. Quote `f2p_passed/49`.
- **Cancelled and errored trials are averaged in as zeros.** A job where two of
  four were killed reported `f2p 0.459` for trials that scored 0.898 and 0.939.
  The tell is a non-integral `f2p_total`; a test count cannot be fractional.
  Read `reward_stats.f2p_passed` per trial and `exception_stats` for losses.

## The failure fingerprint

Across all nineteen staged trials, split at the `pin` stage:

| Failing test | Before `pin` | After `pin` | Baseline |
|---|---|---|---|
| parse `for (using of of [1,2])` | **15/15** | **4/4** | fails |
| reject array destructuring in `using` | **15/15** | 1/4 | fails |
| reject array destructuring in `await using` | **15/15** | 1/4 | fails |
| parse `using` in switch case | 4/15 | 1/4 | fails |
| reject object destructuring in `await using` | 1/15 | 0/4 | passes |
| reject `await using` in sync function/arrow in module | 1/15 | 0/4 | passes |

**44 of the 49 pass in every trial ever run.** Exactly one has never passed
once, under any revision of the prompts: `for (using of of [1,2])`. Everything
else has been solved at least once, which is what makes the destructuring rows
worth reading — they were 0-for-15 and are now landing.

The score moving between 45 and 46 in the first fifteen trials is entirely the
incidental rows.

## The pipeline had the answer and removed it

This is the finding. Reconstructed from the `prior/` documents of a run that
routed back twice.

**Pass 1 implemented the requirement correctly.** Its build rejected
`{ using[a] = b; }` with `'using' declarations cannot have destructuring
patterns` — exactly what the hidden test asserts — and the project's suite came
back **94,751 passing, 0 failures**.

**Verify returned NOT VERIFIED and routed it back**, on evidence it generated
itself: a differential of 108 snippets it chose across three option sets, of
which six behaved differently. Every one of those six differed *only with
`next: true`* — the option the feature adds. The surface that must not move is
the one with the option off, and that surface was untouched and green.

**Pass 2 deleted the behaviour.** Pass 3 refined the deletion. The run shipped
45 where its first build was worth roughly 47.

Three separate mechanisms let that through, and each is visible in the
documents:

**1. Agreement between the design and the blind reading was read as
confirmation.** Both resolved `using [a]` by consulting Babel, TypeScript and
test262, and both got the same answer — which is the least surprising possible
outcome when two readers share a source. Verify recorded "converged" and closed
the point. Independence here is over the *reports*, not over what both readers
already knew.

**2. A stated criterion was narrowed by trimming its inferred input list.** The
request states the error in plain words. SCOPE spelled out which inputs it
covers and labelled that list inferred. Design dropped the array rows; verify
accepted, because only the list had been corrected and not the criterion. The
sentence survives and the criterion is empty.

**3. Both real defects were detected and then retracted.** The test stage caught
the withdrawal and escalated it by name. A later pass wrote *"now settled by
evidence, and I withdraw my caveat"*, and the record was gone. The same happened
to the invented switch-case restriction. Route-backs mean the stage that raises
a concern is rarely the last one to write.

Meanwhile the pipeline's own test count rose every pass — **94,751 → 94,789 →
94,859 passing** — while the score fell, because a share of that growth is tests
it wrote to match what it had just decided. A total that mixes project tests
with self-authored ones climbs as a run converges on its own beliefs.

## What the model believes, and why it matters

The model is not confused about JavaScript. By the language's own rules
`using [a] = b` *is* an index expression: Babel 7.29.7, TypeScript 5.9.3 and
test262 all agree, and test262 is the conformance suite that defines the
language. The task wants something else, because meriyah's maintainers chose a
helpful error over silent reinterpretation.

So the failure is expertise overriding a terse instruction, and the baseline
makes the identical errors — this is a property of the model, not of staging.

The pipeline had better evidence available and passed over it. Its own design
document reads:

> *"`[` is not a binding start at the gate — `let [` is one, `using [` is member
> access"*

`let [` is the same parser resolving the same ambiguity in favour of the
declaration reading. The project's own answer to the exact question, dismissed
in the sentence where the decision was made. **Information was never the
bottleneck; adjudication was.** Web search would not have helped, and the agent
container is `no-network` in any case.

## Putting evidence on the other side: the `pin` stage

Every stage after design can measure what a decision *costs*. Run a differential
and the programs that stop working are countable, reproducible, and impossible
to argue with. Nothing measures the other side: a criterion quietly narrowed to
a convenient subset of the inputs it named breaks no test, moves no number, and
leaves every remaining document agreeing the criterion is met.

So the evidence is one-sided and the side with evidence wins — not because
anyone cheats, but because a stage asked for proof can only produce it in one
direction. That is the whole mechanism behind the destructuring rows going
0-for-15.

A `pin` stage now sits between `explore` and `design`. It turns the criteria
carrying the request's own words into failing tests, each with a stable `PIN-n`
id and a one-line claim, *before* any design exists to defend. The engine
watches those ids, their claims, and the tests that carry them.

Destructuring went **0/15 → 3/4**. That is the mechanism working, and the one
miss is the more instructive half.

**The miss:** verify routed back, and a second design pass reversed the first —
relabelling a stated criterion as "SCOPE's own enumeration," then overruling
three pins by rewriting their *tests* while leaving `PINS.md` untouched. Every
id present, every claim intact, and a parser shipped accepting the input all
three pins said must be rejected. Every guard fired and every guard approved it,
because each was watching a document that never moved. The engine now watches
the assertions too.

## The ceiling

| Failure | Up against | Reachable? |
|---|---|---|
| array destructuring ×2 | convention; the in-repo `let [` precedent points the other way | **yes** — now landing 3 of 4 |
| switch case | nothing; unpinned, so each run guesses independently | **yes** — 15 of 19 already |
| `for (using of of [1,2])` | **a defect in the task** — see below | **not without shipping a regression** |

**So this task tops out at 48/49 on the merits, and `reward` is permanently 0.**
Score it on f2p and name the unreachable row. "48/49, one failure that requires
contradicting V8 and the upstream implementation of the same feature" is an
engineering result; `reward 0` is the number a run that did nothing gets.

## The last failure is the task's, not the pipeline's

This was checked by running code, not by reading documents — twice, after two
earlier readings of this same question turned out to be wrong.

The test asserts that `for (using of of [1,2])` parses as a `using` declaration
binding a variable named `of`. There are two readings of that line and both
parse, so "does it parse" cannot separate them; `of [1,2]` is also the ordinary
subscript `of[1,2]`. Runtime behaviour separates them.

| | task's reference | TC39 grammar | **meriyah 7.3.2, shipped** | V8 13.6 |
|---|---|---|---|---|
| `for (using of of [1,2])` | declaration, binds `of` | **not a declaration** | **identifier `using`** | identifier `using` |
| `for (using of list)` | **SyntaxError** | not a declaration | parses | parses |
| `for (using in obj)` | **SyntaxError** | — | parses | parses |
| `for (await using of of […])` | declaration | declaration | declaration | declaration |

Four things follow.

**The proposal's own grammar forbids it.** From TC39's
[`spec.emu`](https://github.com/tc39/proposal-explicit-resource-management),
where the `<ins>` marks what this feature adds to the language:

```
for ( [lookahead != `using` `of`] ForDeclaration[+Using] of AssignmentExpression ) Statement
```

Read plainly: in a for-of head, when the next two tokens are `using` then `of`,
this is not a declaration. The restriction exists because `for (using of list)`
was already valid before the feature — `using` being an ordinary variable — so
the carve-out is what stops the feature breaking working code. It is written on
the production the feature introduces, which is why there is no earlier reading
to have drifted from: the rule is part of what the feature *is*.

It is also the literal two-token sequence `using of`, which is exactly why
`await using of of` is unaffected. That asymmetry is not an implementation
quirk; it falls out of the grammar, and every implementation that follows the
grammar reproduces it.

**meriyah shipped this feature, and disagrees with the task.** Commit `d40161d`,
*"feat(parser): support explicit resource management declarations (#579)"*,
landed immediately after this task's base commit. It is the same feature in the
same repository, and on the failing input it gives the pipeline's answer. The
task's `solution.patch` is not that PR — the tests were authored separately, and
they encode a reading the real implementation does not have.

**V8 agrees with meriyah.** `for (using of of [null,null])` throws
`ReferenceError: of is not defined` — V8 looked up a variable named `of`, which
only happens under the identifier reading. The `await using` twin runs fine, so
the asymmetry is real and deliberate, and it is the half that test262 pins
(`await-using/syntax/await-using-valid-for-await-using-of-of.js`).

**The reference buys the test by breaking working code.** It has no `of`
lookahead in the for-head, so `for (using of list)` and `for (using in obj)` —
both valid today, both preserved upstream — become syntax errors. None of the
51,469 pass-to-pass tests covers either shape, so the regression is invisible to
the grader and the reference scores 49/49 while shipping it.

The pipeline reconstructs the shipped design independently, run after run, and
loses exactly one point for being right. Its own `DESIGN.md` states the trade in
one line:

> *`for (using of y);` **stays a `ForOfStatement` over the variable `using`**
> … Costs one `!== Token.OfKeyword` test.*

Reaching 49 means teaching the pipeline that a task's tests outrank the engine
every program actually runs on, and then shipping `for (using of list)` broken.
That rule would generalise in exactly the wrong direction, so this row stays
unreached deliberately.

**One caveat on the citation, because it cuts both ways.** Several runs
justified the guard by citing a shipped test262 fixture for the plain-`using`
case. That fixture does not exist — test262 contains exactly one `of-of` file
and it is the `await using` one. The conclusion was right and the evidence for
it was invented, and nothing downstream re-derived it. `explore` already
requires a claim about another implementation to be *run* before it counts;
that rule caught the acorn and V8 claims and does not cover "a conformance suite
ships a fixture saying X," which is a citation rather than a behaviour.

### Reproducing it

No Docker and no API key; about a minute.

```sh
git clone --filter=blob:none https://github.com/meriyah/meriyah && cd meriyah
git checkout d141eb14a40b79c04d1b1db5c20c6afa3844c0d9   # the task's base
git apply /path/to/tasks/meriyah-explicit-resource-declarations/solution/solution.patch
# parse `for (using of list) {}` with { next: true } -> SyntaxError
git checkout main                                        # what actually shipped
# same input -> ForOfStatement, left = Identifier `using`
```

And against V8 directly, which needs nothing checked out at all:

```sh
node -e "require('node:vm').runInNewContext('for (using of of [null,null]) {}', {})"
# ReferenceError: of is not defined   <- the identifier reading
```

## Defects this run exposed in the engine

- **A blind stage could read its own earlier document.** `handoff: none` withheld
  the reports and the transcripts and left, in the directory the stage writes to,
  the one file most able to defeat the barrier. Earlier outputs now move out of
  reach before the stage re-runs.
- **Escalations were retractable.** The record of what a run stopped promising
  lived in a document a later pass owns. It now lives in an append-only
  `ESCALATIONS.md` the engine restores if a stage rewrites it, and `integrate`
  must put every entry in front of a human with whatever was argued against it.
- **A self-authored differential outranked the suite.** Verify may no longer
  route a stated requirement back on evidence it generated, and behaviour that
  differs only under a new flag is the feature, not a regression.
- **Exhausting the hop budget skipped the checks.** A run out of budget jumped
  straight to the terminal stage, so the last revision it made — the one most
  likely to have reduced scope, because it was made to satisfy a route-back —
  was the single revision guaranteed never to be tested or verified. Exhaustion
  now closes routing and advances one stage instead. The number is logged for
  the human and never reaches a prompt: a stage that knew its remaining budget
  could plan around it, and what a stage decides should turn on the work rather
  than on the engine's accounting.
- **A pin could be withdrawn without touching the record.** `PINS.md` says what
  a run promises; the tests say whether it still asserts it, and a stage that
  rewrites the assertions and leaves the document alone moves nothing an
  id-and-claim watch can see. That is not hypothetical — see the miss above. The
  engine now watches both, and reports a rewritten assertion with the pin's
  claim quoted beside it, since the claim is what a reader needs in order to
  judge whether the new assertion still keeps the promise.
- **Provenance was re-adjudicable.** `stated` versus `inferred` is a label a
  later pass can reassign, and the collision rule, the pin scoping rule and the
  escalation record all key off it. Naming the move as out of bounds was not
  enough — a run performed it while quoting the rule. Overruling a pin now
  requires quoting the request's own clause first, which does not settle the
  argument but moves it from a label to the request.

## What the prompts cost

The feature prompts grew **+46% in three days** — 852 to 1,244 lines, with
`design` and `verify` both up 79%. The invented switch-case restriction appears
in **0 of 9** trials before the largest rule commit and **3 of 5** after it. No
single rule says to invent restrictions; the aggregate pushed past "handle the
stated errors" into manufacturing one.

Prompts are the load-bearing logic of this system and the only component with no
test suite. A regression in them surfaces days later at $51 a trial, by accident.
That is the binding constraint on improving the pipeline, more than any
individual rule.

`explore` then grew a further ~30% in one day, across four rules. One of them
landed and is worth two tests; two have yet to fire on any run; one was reverted
the day after it was written, when the diagnosis behind it turned out to be
wrong. A 1-in-4 hit rate on prompt edits is roughly what the growth figures
above would predict, and it is the argument for the decision-replay rig in
[`../decisions/`](../decisions/) rather than for more rules.

Two structural changes since then are worth separating from the prompt edits,
because they are testable and tested: the `pin` stage's effect is visible as
0/15 → 3/4 on a specific pair of tests, and the engine's pin watching has unit
tests reproducing the exact failure it was built for. Neither has fired on a
task other than this one.

## Limits

**This task can no longer test generalisation.** Its failures were diagnosed by
reading the failing tests, and several commits generalise those lessons into the
prompts. Later runs measure whether the fixes worked — a fair question, and a
different one. A third task is owed, and is now overdue twice over.

**The baseline is n=1** against the staged arm's n=19, and it predates the `pin`
stage. The 48 versus 45 gap is therefore staged-with-pins against a single
un-replicated baseline run, which is suggestive and not a result. Re-run the
baseline before quoting the two side by side.

**One task, and the one whose prompts were tuned against it**, which favours the
staged arm. The tuning is readable — the prompts are English and name nothing
about this task — but "written without naming the task" and "works on another
task" are different claims, and only the second is worth anything.

**The remaining failure cannot separate two arms.** Both fail it, and both are
right to. A task whose last point requires shipping a regression has no headroom
left to measure a process effect with.

## Next

1. **Pick tasks by headroom.** The comparison needs tasks where a single session
   fails for *process* reasons — dropped requirements, regressions, unfinished
   work — because that is what staging is theorised to fix. Both tasks so far
   were chosen ad hoc and one had nothing to show.
2. **The prometheus baseline.** That arm scored 4/4 with no control, and the
   board reports 25% for a single Opus 5 session at the same effort. It is the
   only place a positive result may still exist.
3. **A way to see what a prompt edit breaks**, short of a two-hour trial. Until
   then, every rule added is unverified, and the growth figures above say that
   is already costing more than it returns.
4. **Take the switch-case point, and stop at 48.** It has passed 15 of 19 times,
   conflicts with no reference, and fails because "any scope" was enumerated
   from memory rather than exhaustively — the same unenumerated-kind gap as the
   last row, minus the conflict. It is the only remaining point worth chasing
   here.
5. **Report the task defect upstream.** The reproduction above needs no API key
   and no container, and a benchmark row that can be shown wrong is more useful
   than one that is quietly worked around. Whether DeepSWE wants the fixture
   corrected or the test dropped is theirs to decide; the evidence is not.
