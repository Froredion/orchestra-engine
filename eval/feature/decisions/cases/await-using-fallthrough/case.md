---
name: await-using-fallthrough
pipeline: feature
rules: 07-verify.md
task: request.md
documents: SCOPE.md, STATE.md, .verify-independent-reading.md
handoff: handoff.md
answers: hold, route-back
correct: route-back
observed: route-back
repo: https://github.com/meriyah/meriyah.git
commit: d141eb14a40b79c04d1b1db5c20c6afa3844c0d9
sparse: src
---

You are on **pass 1**. The workspace is meriyah's `src/` at `d141eb1` plus the
branch's changes as `STATE.md` describes them. Your artifacts directory holds
`.verify-independent-reading.md`, `SCOPE.md` and `STATE.md`.

Every stated behaviour in the request is present. The suite is green, and every
AC-9 / AC-13 / AC-19 input SCOPE lists reports the correct error.

Decide one point, and only that point. `STATE.md` §"How the `await using`
async-context check is implemented" describes where the check sits, and the
section after it has the measurements. Under `next: true`, `await` applied to an
ordinary identifier named `using` — `function f(){ await using; }`,
`function f(){ await using.x; }` — reports `'await using' declarations are only
allowed inside async…` where `main` reports its ordinary diagnostic. All of these
inputs error on `main` too; the message is what moved.

- **`hold`** — the branch is not routed back on it. It is recorded for a human
  and the branch proceeds.
- **`route-back`** — it is a defect. The branch goes back to the stage that owns
  it.

Do not open other defects, and do not re-decide the AC-12 `[`-form conflict that
`STATE.md` records as out of scope.
