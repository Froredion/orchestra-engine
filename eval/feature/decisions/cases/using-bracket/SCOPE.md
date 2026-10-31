Excerpts from `SCOPE.md`, as the `explore` stage wrote it. Verbatim; the acceptance
criteria not bearing on this decision are omitted.

---

**AC-4 [stated]** — "A UsingDeclaration requires no LineTerminator between `using` and the binding
identifier; if a line break appears, `using` is treated as an identifier."

**AC-12 [stated]** — "Destructuring pattern: `cannot have destructuring`".
A `using`/`await using` binding that is an array or object pattern throws with a message
containing `cannot have destructuring`.
Inputs: `using {a} = b`, `using [a] = b`, and the same two under `await using` in an async context,
and in a for-of head: `for (using {a} of b);`.

### Regression / integration

**AC-15 [inferred]** — basis: **measured**. Every one of the following parses today (verified by
running the built parser) and must continue to parse identically, with and without `next: true`,
because AC-4's "`using` is treated as an identifier" is only meaningful if `using` stays a normal
identifier. `using` must be *contextual*, never reserved:
`var using = 1;` · `using;` · `using = 1;` · `using.foo` · `using()` · `using => using` ·
`({ using })` · `({ using: 1 })` · `function using(){}` · `class using {}` · `label: using;` ·
`using + 1` · `let using = 1` · `for (using of y);` (`using` as the loop *variable*) ·
`import { using } from 'm'` · `x.using` · `use strict` mode variants of the above.

### Cross-checks against reference implementations

1. **AC-12 vs acorn.** I measured acorn accepting `using [a] = b` as an `ExpressionStatement`
   (`using` treated as an identifier, `[a] = b` as a member-then-assign). An inference from
   "match the reference implementation" would say *no error*. **AC-12 is stated and wins:** meriyah
   must raise `cannot have destructuring`. My cross-check is narrowed to "acorn is not the oracle
   for this input."

2. **AC-11 vs acorn.** acorn accepts `for (using x in y);` outright. **AC-11 is stated and wins:**
   `not allowed in for-in` is required.

### Note on the suite as a safety net

`using` appears as source text in exactly **one** pre-existing test file
(`test/parser/miscellaneous/commonjs.ts:12`). A green suite is therefore weak evidence about
anything to do with `using` as an ordinary identifier — that very fact means the suite is a *weak*
net here — hence AC-15.
