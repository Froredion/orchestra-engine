---
name: using-bracket
pipeline: feature
rules: 07-verify.md
task: request.md
documents: SCOPE.md, STATE.md, VERIFY-pass-1.md, .verify-independent-reading.md
handoff: handoff.md
answers: hold, route-back
correct: hold
observed: route-back
repo: https://github.com/meriyah/meriyah.git
commit: d141eb14a40b79c04d1b1db5c20c6afa3844c0d9
sparse: src
---

You are on **pass 2**. The workspace is meriyah's `src/` at `d141eb1` plus the
branch's changes as `STATE.md` describes them. Your artifacts directory holds
`.verify-independent-reading.md`, `SCOPE.md`, `STATE.md`, and your own pass-1
report as `VERIFY-pass-1.md`, which you may supersede.

Your pass-1 report routed two defects. BUILD fixed both, TEST re-pinned them, and
they are closed — they are not what you are deciding here. The suite is green and
every stated behaviour in the request is present.

One open point remains, the one your pass-1 report called Conflict A. AC-12 is
implemented over both pattern forms, so under `next: true` a set of inputs that
`main` parses today now raise the AC-12 error instead. `STATE.md` has the
measurements.

Decide what happens to that point, and only that point:

- **`hold`** — the branch is not routed back on it. AC-12 stands as built, and the
  cost stays on the escalation record for a human to rule on.
- **`route-back`** — it is a defect. The branch goes back to `design` to narrow
  AC-12's input set so those inputs parse again.

Do not re-decide the two closed defects, and do not open new ones.
