--- HANDOFF: test ---
Session ID: (not available in a replay)

What it did:
Second pass. Both defects VERIFY routed in pass 1 are fixed and re-pinned:
node ranges now agree with acorn on `using` / `await using` statements, and the
`await using` async-context error no longer fires on non-declarations. Project
suite 94,696 passing, 0 failing. Every AC has at least one discriminating test;
eight independent mutations each turn the new tests red.

I widened the recorded blast radius on the open AC-12 conflict rather than
resolving it — writing `await using[0];` as a positive test for the `await using`
fall-through path, I found it does not parse under `next: true`. So the affected
population is any statement-initial `using[…]`, reads included, not only
assignments. That is on ESCALATIONS.md as `[testing, pass 2]`. I deliberately pin
neither direction for the `[` forms under `next: true`; the `next: false`
behaviour is pinned.
--- END HANDOFF ---
