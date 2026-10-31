Excerpts from `SCOPE.md`, as the `explore` stage wrote it. Verbatim; the acceptance
criteria not bearing on this decision are omitted.

---

**AC-4 [stated]** — "A UsingDeclaration requires no LineTerminator between `using` and the binding
identifier; if a line break appears, `using` is treated as an identifier." `using` is contextual,
never reserved.

**AC-9 [stated]** — "Await using outside async/module: `only allowed inside async`".
`await using` outside an async context and outside module top level throws with a message
containing `only allowed inside async`.
Inputs: `await using x = null` as script; `function f(){ await using x = null }`;
`for (await using x of y);` as script.

**AC-13 [stated]** — "`await using` at script top-level should report the async-context error
(`only allowed inside async`), not the script-global error."
`await using x = null` parsed as a script throws the AC-9 message, **not** the AC-8 message. This
is an explicit ordering constraint on two checks that both apply.

### Regression / integration

**AC-15 [inferred]** — basis: **measured**. Every one of the following parses today (verified by
running the built parser) and must continue to parse identically, with and without `next: true`,
because AC-4's "`using` is treated as an identifier" is only meaningful if `using` stays a normal
identifier. `using` must be *contextual*, never reserved:
`var using = 1;` · `using;` · `using = 1;` · `using.foo` · `using()` · `using => using` ·
`({ using })` · `({ using: 1 })` · `function using(){}` · `class using {}` · `label: using;` ·
`using + 1` · `let using = 1` · `for (using of y);` (`using` as the loop *variable*) ·
`import { using } from 'm'` · `x.using` · `use strict` mode variants of the above.

**AC-19 [inferred]** — basis: composition of AC-7 and AC-13 on an input neither states outright.
`for (await using x of y);` at **script top level** raises AC-9's `only allowed inside async`, not
AC-8's global-scope error — same precedence rule as AC-13, applied inside a for-head.

### Note on the suite as a safety net

`using` appears as source text in exactly **one** pre-existing test file
(`test/parser/miscellaneous/commonjs.ts:12`). A green suite is therefore weak evidence about
anything to do with `using` as an ordinary identifier.
