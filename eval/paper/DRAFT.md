# Deliberation Helps With Omissions and Hurts With Underspecification: Case Studies of a Staged Coding Agent on DeepSWE

*Working draft, 2026-10-28. Every number in it comes from one or a few
trials. Read it as a set of hypotheses with worked evidence, not as measured
effects. Citations marked [cite] are still to be filled in.*

## Abstract

We studied a staged coding agent on four hard DeepSWE tasks, where the
public leaderboard shows Opus 5 passing 0–15% of the time. The agent chains
eight to sixteen sessions of a single model. Each session plays a fixed
engineering role: it explores, pins requirements as tests, designs, builds,
tests, verifies, and optionally runs an upstream design phase first. We
compared it against one long Claude Code session on the same model (Opus 5.5),
in the same sandbox and with the same budget.

The staged agent solved a task the single session missed. The single session
had failed on an edge case that the staged agent's research phase had
predicted in writing. On the other tasks the staged agent tied the single
session, or did worse than its own simpler configuration. Every one of those
losses came from a decision the staged agent *added*: an extra error, a merged
interpretation of two conflicting sentences, or a stricter reading than the
reference solution's. We traced each lost test to the stage that made the
decision. Our account has two parts.

1. **Staging reduces errors of omission.** Repeated passes by different roles
   catch details that a single pass drops.
2. **Staging increases errors of commission on underspecified requirements.**
   Wherever the ticket leaves a point open, deliberation expresses the model's
   priors (standards compliance, protective defaults) more strongly than one
   quick reading would. Review by the same model cannot correct those priors,
   because the reviewer shares them.

We then changed only *where* a decision about an open point gets made. When an
upstream stage settled open points, it lost tests that the downstream stage
had won when the point was left open. Moving the decision downstream fixed one
of the two contested points. The other, a privacy-flavoured reading, went the
same way in all three staged runs, whichever stage decided it. We argue that
for benchmarks with all-or-nothing hidden tests, ambiguity can set a ceiling
that no pipeline structure can lift. We also argue that "flagged the exact
ambiguity the failing test hinges on" should be scored as a result in its own
right.

## 1. Introduction

Agent scaffolds for software engineering increasingly break work into stages:
plan, implement, test, review [cite]. The usual justification is the one for
code review: a second look catches what the first missed. In our runs that
holds for one class of errors and reverses for another, and the boundary
between the two classes is predictable.

DeepSWE tasks grade a patch against hidden fail-to-pass tests written
alongside a reference solution. A trial passes only if *every* new test
passes. In the tasks we studied, the ticket text leaves some behaviours
unstated or self-contradictory. The hidden tests still pin one answer for
each, and that answer is whatever the reference patch happens to do. A model
can understand the domain perfectly and still lose a test by resolving such a
point differently from the reference's author.

Our contributions:

- **A case study of four tasks.** Each lost test is traced to the stage that
  produced the losing decision, using the pipeline's own stage documents.
- **A distinction between omission and commission.** It explains when staging
  helps and when it hurts.
- **A small intervention study** that varies where open points get decided.
  It shows that the locus of a decision matters, and that one bias did not
  move whatever the locus.
- **An observation about the benchmark.** It rewards guessing an unstated
  preference, which the staged design deliberately defers to a human. We
  propose scoring "escalated the decisive ambiguity" separately.

## 2. System

The engine runs a *pipeline*: a folder of numbered prompt files, executed in
order. Each stage is a fresh Claude Agent SDK session. Stages share nothing
except files on disk and a short handoff (the previous stage's summary).
`verify` can send the run back to an earlier stage, up to a hop cap. Two
pipelines matter here.

- **`feature`** runs explore → pin → design → build → test → reread → verify
  → integrate. The `pin` stage turns every criterion stated in the ticket into
  a failing test before any design exists. The engine then watches those pins,
  so later stages cannot quietly weaken them.
- **`approach`** runs intake → ground → frame → option (parallel, blind) →
  compare → reuse (parallel) → reconcile → spec. It writes no code. It
  publishes a `SPEC.md` that labels every criterion *stated* or *inferred*,
  and it records unresolved conflicts in an append-only `ESCALATIONS.md`.

The **chain** arm runs `approach` and hands its spec to `feature`, with the
instruction that wherever the spec and the ticket disagree, the ticket wins.
`approach` gets at most a third of the time budget. All stages use the same
model.

## 3. Setup

- **Benchmark.** DeepSWE v1.1 via Pier. Each trial runs in Docker, with egress
  allowlisted to the model API only, and is graded by hidden tests:
  f2p (fail-to-pass) and p2p (pass-to-pass). Reward is 1 only if f2p and p2p
  both pass in full.
- **Model.** Opus 5.5, with subscription auth (no cost figures).
- **Arms.**
  - single session: Pier's stock `claude-code` agent;
  - `feature`;
  - chain: `approach+feature`.

  All arms ran with `--agent-timeout-multiplier 2`, except the earlier
  `feature` runs, which used ×4 on prometheus.
- **Tasks.**

  | Task | Opus 5 on the leaderboard | Why we chose it |
  |---|---|---|
  | `obsidian-linter-link-format-conversion` | 1/20 | most models fail it |
  | `superjson-error-stack-serialization` | 3/20; `max` 0/4 | misses cluster at 77–79/80 |
  | `meriyah-explicit-resource-declarations` | 0/20 | never solved by our `feature` arm |
  | `prometheus-transactional-reload-status` | (see the `feature` findings) | first task the `feature` arm was built on |

  The leaderboard figures are for Opus 5 on a different scaffold
  (mini-swe-agent). We cite them only to show the tasks are hard. They are not
  a baseline.

## 4. Results

| Task | Arm | Trials | f2p | Reward | Wall clock | Tokens (prompt / completion) |
|---|---|---|---|---|---|---|
| obsidian | chain | 1 | 60/60 | **1** | 2h31m | 35.7M / 624k |
| obsidian | single | 1 | 59/60 | 0 | 6.5m | 957k / 21k |
| superjson | chain, original prompts | 1 | 79/80 | 0 | 1h29m | 21.4M / 522k |
| superjson | chain, variant P ("plainest reading") | 1 | 78/80 | 0 | 1h57m | 19.3M / 519k |
| superjson | chain, variant O ("open points") | 1 | 79/80 | 0 | ≈2h15m awake | 28.0M / 630k |
| superjson | single | 1 | 79/80 | 0 | 5.5m | 643k / 29k |
| meriyah | chain | 1 | 47/49 | 0 | 1h17m | 21.6M / 448k |
| meriyah | `feature` | 19 | best 48/49 | 0 | 1h50m–3h01m | — |
| meriyah | single | 1 | 45/49 | 0 | 33m | — |
| prometheus | `feature` | 4 | 15/15 ×4 | **1 ×4** | 1h35m–3h17m | — ($203 total) |

p2p was 100% in every trial. Most of the prompt tokens were cache hits (over
90% in every chain run).

### 4.1 Where each lost test came from

| Task | Arm | Failing test | Type | Decided by |
|---|---|---|---|---|
| obsidian | single | wiki link with spaces in target is preserved | omission | — (`approach` had predicted this case in writing) |
| superjson | single | step order: redactPaths → maxStackLines → stripInternalFrames | omission | — (every chain run got it right) |
| superjson | chain ×3 | sanitizeMessage not applied to causes that fail classFilter | commission | `compare` (twice), `feature` design (once) |
| superjson | chain P | hook fires with no errorStack option | commission | `compare` |
| meriyah | chain | `using` directly in a `case` clause | commission | `spec` added a sixth error |
| meriyah | all arms | `for (using of of …)` is a declaration | task defect (§5.4) | — |

Every lost test that the single session alone failed is an omission. Every
lost test that the chain alone failed is a commission.

## 5. Analysis

### 5.1 Omission and commission

An **omission** leaves out something the ticket asks for, or mishandles a case
the ticket implies. Stages catch omissions because each pass reads the ticket
again with a different job. In obsidian, `approach` noted that "a literal
wiki→markdown conversion breaks targets that contain spaces". It carried that
as a note rather than a requirement, and `feature` handled the case. The
single session converted literally and failed exactly that test.

A **commission** adds a decision that the ticket does not make. Stages
*produce* commissions, because roles such as "compare the options" and "write
a spec" are asked to settle things. Each loss in this column was locally
defensible:

- **The meriyah switch-case error.** Real tooling has this restriction; we
  believe TypeScript reports it, though we have not verified that.
- **The superjson union rule.** `compare` merged the two readings so as to
  "never leave PII that either reading would remove."
- **The superjson hook gate.** `compare` justified it with "Omitting it leaves
  existing Error behavior unchanged."

Each is a choice a careful engineer might make, and none is the choice the
reference solution made.

### 5.2 Reviewing yourself does not remove your biases

Verify checks the build against the spec it was given. Once a commission
reaches the spec, every downstream check confirms it. The `feature` arm's
history on meriyah shows the same dynamic *inside* one pipeline. Pass 1 built
a stated error correctly. Verify then routed the run back, on evidence it had
generated itself. Pass 2 deleted the behaviour, and the pipeline's own test
count rose while the hidden score fell. "The pipeline had the answer and
removed it."

Independent passes catch slips. They cannot catch beliefs, because every pass
holds the same belief. Across three chain runs on superjson, the
cause-sanitization point was decided by two different stages under three
prompt variants. Two runs produced the union and one produced "root decides",
and all three sanitize causes the reference leaves alone. The single session
was the only run that chose the reference's reading.

### 5.3 Where a decision is made matters, up to a point

We varied only how `approach` treats open points.

| Variant | Instruction | Hook point | Sanitize point |
|---|---|---|---|
| original | (none) | spec flagged it as an open question; `feature` chose → ✅ | `compare` merged → ❌ |
| P | pick the plainest reading; never merge | `compare` decided → ❌ | `compare` decided ("root decides") → ❌ |
| O | list the readings; decide none; spec carries them only as notes | open; `feature` chose → ✅ | open; `feature` chose the union → ❌ |

On the hook point, every run in which an upstream stage decided it lost the
test, and every run in which it was left open won. "Pick the plainest
reading" failed because *both* readings were literal. The heuristic could not
choose between them, and it turned a previously open question into a
commitment. Variant O tied the original's score for a sounder reason, and we
kept it.

The sanitize point did not move whatever the locus. We read this as a model
prior: more deliberation over a privacy-related sentence favours the
protective reading. We do not believe prompt structure can fix it without
coaching the model toward the answer, which would be fitting the benchmark.

### 5.4 Reference solutions are minimal, and the tests encode them

The reference solutions are deliberately minimal.

- **meriyah.** The reference treats `using` followed by any same-line
  identifier as a declaration and has five errors, none of them about switch
  cases. Its hidden test that `for (using of of [1,2])` declares a binding
  contradicts the grammar's `[lookahead ≠ using of]` restriction, V8, and
  meriyah's own shipped release. No arm can pass that test without shipping
  a regression, so the task cannot be passed on its merits.
- **superjson.** The reference checks each error's own `.name` for both stack
  processing and sanitization.

A pipeline that adds rigour therefore disagrees with a minimal reference at
more points. In a code review, many of those disagreements would be
improvements. Under this grader, each one is a lost test.

### 5.5 What a human-in-the-loop design means for this benchmark

The staged design answers ambiguity by escalating it to a person. On all three
superjson chain runs, `approach` identified exactly the contradiction on which
the remaining test hinges. In deployment, that contradiction is a one-line
question to the ticket's author. The benchmark has no author to ask, so the
agent must guess, and the guess is where a single model's prior decides the
outcome. We suggest reporting, alongside reward, whether the agent *flagged*
the ambiguity that decides each failed test. A failure that names its own
cause is a different outcome from one that does not.

## 6. Threats to validity

- **Sample size.** Most cells are a single trial. Variance is visibly large:
  the same configuration on meriyah ranged over 45–48. Nothing here is a
  rate.
- **Task selection.** Tasks were chosen *because* models fail them, which
  over-samples ambiguity-limited tasks. The prometheus `feature` runs have no
  same-host baseline.
- **One model, one author.** All stages and all baselines are Opus 5.5. The
  authors of the pipeline also ran and analysed the evaluation.
- **Analysis used the reference solutions.** We read the reference patches to
  explain failures (§5.4), after the runs. No agent saw them. The prompt
  variants were motivated by superjson and evaluated on superjson, so §5.3
  overfits by construction.
- **Uncontrolled factors.**
  - Subscription auth, which gives no cost figures and may throttle.
  - One run paused for about six hours while the host slept.
  - Budgets varied: ×2 here and ×4 on prometheus.
  - The engine code was uncommitted at run time. Commit and cite hashes
    before quoting any number.

## 7. Related work

[To be written: multi-agent and self-review scaffolds for code (plan /
implement / review loops) [cite]; self-consistency and debate among copies of
one model and their limits when the errors are correlated [cite];
underspecification and ambiguity in SWE benchmarks and in requirements
engineering [cite]; benchmark validity studies that find defective or
over-specific hidden tests [cite].]

## 8. Next steps

1. **Freeze the pipeline** (variant O) and record its commit.
2. **Pre-register a task set** of 5–10 DeepSWE tasks, chosen from leaderboard
   failure patterns that look like correctness errors rather than ambiguity
   errors. Run them before looking at the results.
3. **Run 4 trials per arm**: single session, `feature`, and chain. Report the
   pass rate, f2p, cost, and an *escalation-hit* metric (§5.5).
4. **Test one general intervention** across the whole set, never per task:
   answer each open point with a quick first reading of the ticket before any
   design discussion. This tests the deliberation-prior account in §5.2.

## Appendix: evidence

- **Per-task findings, with stage-by-stage traces:**
  - `eval/approach-feature/deepswe/FINDINGS-obsidian-linter.md`
  - `eval/approach-feature/deepswe/FINDINGS-superjson.md`
  - `eval/approach-feature/deepswe/FINDINGS-meriyah.md`
  - `eval/feature/deepswe/FINDINGS-meriyah.md`
  - `eval/feature/deepswe/FINDINGS.md`
- **Job directories** (outside the repo):
  `~/Documents/GitHub/deepswe-scratch/jobs/{obsidian,superjson,meriyah}-*`.
- **Adapter:** `eval/feature/deepswe/pier_agent.py` (the chain is
  `pipeline=approach+feature`).
- **Prompt variants:** `orchestra_engine/pipelines/approach/05-compare.md` and
  `08-spec.md`. Variant O is the current working tree, and variant P is
  described in `FINDINGS-superjson.md`.
