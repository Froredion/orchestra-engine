# bugfix

‹ back to [README](../../README.md#pipelines)

**bugfix** — root-cause debugging: reproduce → localize → diagnose
(fans out, one agent per hypothesis) → fix → verify → prevent. Single-pass,
and self-routing: a stage that isn't satisfied loops the run back rather
than marching on — verify re-diagnoses (or re-localizes, or re-reproduces)
a fix that didn't hold; localize can hop back to reproduce.

```
reproduce ──▶ localize ──▶ diagnose (∥ per hypothesis) ──▶ fix ──▶ verify ──▶ prevent
             (single-pass; each stage decides whether to advance or loop back)
```

Point it at a bug report and a workspace containing the code. You'd normally
ask your agent to run it ([How to use it](../../README.md#how-to-use-it)); this is the command it runs:

```bash
./orchestra "timeout=0 is ignored; the client waits forever" \
    --pipeline bugfix --workspace ./my-project -y
```

It leaves `./my-project` holding the fix and the test that guards it, nothing
else; the reasoning that got there — `REPRO.md` through `POSTMORTEM.md` — lands
in the run directory. See [Where things get written](../../README.md#where-things-get-written).

1. **reproduce** (2 passes) — writes 2–3 *different* candidate reproduction
   tests, runs them all, and keeps the one that fails for the right reason
   (sampling and selecting, never iterating one until it fails), plus
   `REPRO.md`. Pass 2 checks determinism, minimizes, and proves the test
   *flips* — that it passes on fixed code, not just fails on broken code.
   Fixing is forbidden here. The verdict is tri-state, and the stage writes
   its decision to `verdict.txt` as `PROCEED` or `STOP`, which gates the
   repair stages below.
2. **localize** (3 passes, cumulative) — narrows file → symbol → line by
   running things, tracing the bad value back from the blow-up to where it was
   born. Writes `LOCALIZATION.md` and `hypotheses.txt`: candidate *root
   causes*, ranked best-first, one per line. Each pass sees every earlier one
   and is told to challenge them — a pass that fingered a symptom gets its
   hypothesis rewritten.
3. **diagnose** — one agent per hypothesis (top 3, parallel), each trying to
   *prove and refute* its own claim by execution, writing
   `diagnosis-N.md` with a CONFIRMED / REFUTED / INCONCLUSIVE verdict. Pass 2
   is adversarial: the pass-1 verdict is now the thing on trial. Differential
   diagnosis maps onto fan-out — hypotheses are cheapest to kill in parallel.
4. **fix** — *skipped entirely if `verdict.txt` says STOP*: with no
   reproduction there is no gate, and a fix nothing can check is worse than
   none. Otherwise it fixes the confirmed root cause with the smallest change
   that removes it, at the line where the wrong decision is made. If nothing
   was confirmed, it **abstains** and writes up what's ruled out instead of
   guessing.
5. **verify** (2 passes) — the distrust stage. Reproduction test passes *and*
   fails again when the fix is reverted; full suite vs. the baseline for
   regressions; then inputs the test never covered, to catch a patch that
   recognizes the test rather than removing the cause. Pass 2 assumes pass 1
   was too kind. Verdict is VERIFIED / NOT VERIFIED / PARTIAL, and a false
   VERIFIED is the worst thing the pipeline can produce.
6. **prevent** — always runs, and always writes `POSTMORTEM.md`, because a run
   has to end with something a human can act on. After a fix: lands the
   reproduction test as a permanent regression test, greps for siblings of the
   same root cause (logged, not drive-by fixed), and asks why nothing caught
   the bug earlier. After a `STOP`: writes the handback instead — what was
   tried, what the code alone revealed, and **the specific information that
   would unblock it**.

The shape follows the evidence:

- **Reproduce first — as a gate, not a lever.** Handing an agent a *perfect*
  human-written reproduction test moves resolution only ~51%→~54%
  ([IBM](https://arxiv.org/html/2511.16858v3)); Google's smaller BRT study
  (n=23) is more bullish at 74% vs 57%
  ([BRT Agent](https://arxiv.org/abs/2502.01821)), but the direction is the
  finding, not the magnitude. Reproduction does **not** make an agent better
  at fixing. It earns stage 1 because it is the only thing that tells you
  whether you are done, and the only way to *detect* overfitting.
- **The verdict is tri-state.** `REPRODUCED` / `NOT REPRODUCED` / `TEST
  INCONCLUSIVE` — the third state separates "the bug isn't there" from "my
  test is broken", and merging them is how a broken reproducer later
  masquerades as a fixed bug. ~22% of issues resist reproduction under *any*
  sampling budget, so the no-repro branch is normal, not exceptional: it
  needs new information, not more attempts.
- **Sample candidates; don't iterate one.** Tests used to *select* among
  candidates cost ~4–5% overfitting; tests used to *refine* one candidate
  until green cost 17–30%, and bolting a refinement loop onto a select-only
  pipeline *raised* overfitting 21.8%→25.5%
  ([IBM](https://arxiv.org/html/2511.16858v3)). "Iterate until the tests pass"
  is the documented harm, which is why no stage here loops to green.
- **Distrust the passing test.** 29.6% of *plausible* agent patches behave
  differently from the human fix, and 7.8% counted as correct while failing
  the project's own test suite
  ([SWE-bench correctness](https://arxiv.org/abs/2503.15223)) — which is why
  verify reverts the fix, leaves the reproduction test, and hunts the inputs
  the test never covered.
- **Structure beats autonomy.** [Agentless](https://arxiv.org/abs/2407.01489)
  hit 32% on SWE-bench Lite at ~$0.70/issue with a fixed
  localize→repair→validate pipeline, arguing that many-turn agent autonomy
  compounds its own mistakes. That is this engine's premise.
- Anthropic's own Claude Code docs give the same two instructions
  first-party: *"write a failing test that reproduces the issue, then fix
  it"* and *"address the root cause, don't suppress the error."*
