# `approach+feature` on `meriyah-explicit-resource-declarations`

**Result: reward 0. f2p 47/49, p2p 51469/51469.** One trial, Opus 5.5, 1h17m.

## Why this task

On the leaderboard, Opus 5 passes this task 0 of 20 times. Our earlier runs
didn't pass it either:

- **`feature` alone:** 19 trials, best 48/49 (see
  [`../../feature/deepswe/FINDINGS-meriyah.md`](../../feature/deepswe/FINDINGS-meriyah.md)).
- **A single Claude Code session:** 45/49.

The test that blocked `feature` asserts that `for (using of of [1,2])` is a
`using` declaration. The TC39 grammar, V8 and meriyah's own shipped release all
reject that, so the test is probably a defect in the task.

## Configuration

This run used the same settings as
[FINDINGS-obsidian-linter.md](FINDINGS-obsidian-linter.md): `pipeline=approach+feature`,
`agent_timeout_sec=21000`, bypassPermissions, OAuth, and egress allowlisted to
`api.anthropic.com` only. It ran in job
`jobs/meriyah-approach-feature/2026-10-27__16-14-02`.

| Pipeline | Wall clock |
|---|---|
| `approach` (3 options, 10 reuse checks) | 08:15 → 08:43 (≈28m) |
| `feature` (one pass, no route-backs) | 08:43 → 09:30 (≈47m) |

Tokens: 21.6M prompt (20.2M of it cached), 448k completion.

## The two failures

```
✗ Using in for-of loops > should parse for-of with using and of as binding name
✗ Edge cases > should parse using in switch case
    'using' declarations are not allowed directly in a switch case clause
```

1. **`for (using of of …)`.** This is the expected failure. `approach`
   addressed it head-on. DECISION §6 says: "A, B and C (and acorn) all reject
   it, and A and C argue the spec's `[lookahead ≠ using of]` makes it
   invalid. Implement it as an error." The spec even lists it as a case to
   test. The ticket never mentions it, so the rule that the ticket wins never
   came into play. Every arm we've run reaches the same reading, which
   supports the view that the test itself is the defect.
2. **`using` directly in a `case` clause.** This is a **regression `approach`
   introduced.** The ticket lists it under "unstated cases with no AC". Spec
   §7 turned that open point into a new, sixth error, `UsingInSwitchCase`,
   and the hidden test expects the code to parse. The best `feature`-only
   trial passed this test, because it never added the restriction.

## What this says about the chain

- **`approach` again turned an open point into a firm rule, and the rule was
  wrong.** This is the same pattern as superjson
  ([FINDINGS-superjson.md](FINDINGS-superjson.md)). The ticket says nothing
  about switch cases. The spec closed the gap with a restriction, and `feature`
  built it faithfully. Verify checked the build against the spec, so it had no
  grounds to object.
- **This is the opposite of how obsidian went.** There, `approach` raised a
  trap (wiki targets that contain spaces) and *carried it as a note*, and the
  note turned out to be right. On superjson and meriyah, its interpretations
  were *written into the spec as requirements*, and they turned out wrong.
  A note left `feature` free to decide. A requirement didn't.
- **It scored lower than `feature` alone.** That is 47/49 here against a best
  of 48/49, but the feature-only number is the best of 19 trials and this is
  one trial, so it is not a fair comparison of rates.

## Possible change

Keep `approach`'s inferred answers to gaps the ticket leaves open out of the
spec's requirements. Hand them to `feature` as flagged notes, or escalate them,
and don't let them become new errors or invariants. The prompt to change would
be `spec`, and probably `compare` too (see the superjson findings).
