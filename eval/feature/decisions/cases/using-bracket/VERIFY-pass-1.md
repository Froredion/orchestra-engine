Your own pass-1 report on this point, verbatim. You may supersede it.

---

### Conflict A — `using[a] = b` is now a syntax error under `next: true`

This is the reading's #1 ranked asymmetry (§4.5) and DESIGN.md §8.1 / ESCALATIONS.md §1.

Measured, all three:

| input | `main` | head, `next:true` | acorn 8.16 | V8 |
|---|---|---|---|---|
| `using[a] = b;` | `MemberExpression` assignment | **`'using' declarations cannot have destructuring patterns`** | `ExpressionStatement` | OK |
| `using [a] = b;` | `MemberExpression` assignment | **same error** | `ExpressionStatement` | OK |
| `for (using[a] of y);` | `ForOfStatement.left = MemberExpression` | **same error** | OK | OK |
| `using {a} = b;` | `Unexpected token: '{'` | destructuring error | error | error |

So previously-valid JavaScript stops parsing under `next: true`. The request requires *a*
`cannot have destructuring` error but **names no input for it**; SCOPE.md chose the input set, and
`[a]` is SCOPE's addition, not the request's word. The `{` half satisfies the requirement on its own
and costs nothing.

I am **not** routing on this, and I want to be explicit about why. The collision is
stated (AC-12) vs inferred (AC-15, identifier preservation), and SCOPE.md resolved it for the stated
one, which is the correct precedence — I do not get to overturn it because two references disagree
with the request. The request is the specification; acorn and V8 describe a different artifact. But
the cost is real, it is borne by users who never wrote a `using` declaration, and it is already on
the escalation list for a human. It stays there, now with the `for (using[a] of y)` case measured
too.
