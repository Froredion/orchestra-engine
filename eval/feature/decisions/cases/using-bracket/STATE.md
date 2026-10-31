What is on the branch, and what has been measured. Head `7138b62`, branched from
`d141eb1` (`main`). Measurements are from your own pass-1 probes and BUILD's; nothing
here is a conclusion, only what was run and what came back.

## How AC-12 is implemented

`src/parser.ts` gates the declaration reading on the token after `using`:

```ts
function isUsingDeclarationStart(parser: Parser): boolean {
  return (
    (parser.flags & Flags.NewLine) === 0 &&
    (parser.getToken() & (Token.IsIdentifier | Token.IsPatternStart)) !== 0
  );
}
```

`Token.IsPatternStart` covers `[` and `{`. When the gate opens and the binding is a
pattern, the parser raises `'using' declarations cannot have destructuring patterns`.

## Suite

- Project suite: **94,696 passing, 0 failing.**
- All new tests written this run pass. Eight independent mutations of the feature
  each turn them red, so they discriminate.
- `using` appears as source text in one pre-existing test file, which SCOPE recorded.

## The flag-on differential

A probe of 45 sources × {script, module, commonjs} × {`next` absent, `next: false`,
`next: true`}. Under `next: true`, **12 parse-status changes** against `main`, all of
one shape:

| input | `main` (`next: true`) | head (`next: true`) | acorn 8.16 | V8 |
|---|---|---|---|---|
| `using[a] = b;` | `MemberExpression` assignment | `'using' declarations cannot have destructuring patterns` | `ExpressionStatement` | OK |
| `using [a] = b;` | `MemberExpression` assignment | same error | `ExpressionStatement` | OK |
| `using[0];` | `MemberExpression` statement | same error | `ExpressionStatement` | OK |
| `function f(){ using[0]; }` | parses | same error | parses | OK |
| `for (using[a] of y);` | `ForOfStatement.left = MemberExpression` | same error | parses | OK |
| `await using[0];` (module) | `AwaitExpression` over `MemberExpression` | `'await using' declarations cannot have destructuring patterns` | parses | OK |
| `using {a} = b;` | `Unexpected token: '{'` | destructuring error | error | error |

`using [a] = b` and `using[a] = b` are the same token sequence — the lexer discards
whitespace — so no gate can accept one and reject the other.

## The flag-off differential

Two error messages changed wording; no parse status moved. `await using x = null`
(script) and `export using x = 1` (module) report `Unexpected token: 'using'` where
`main` reports `Unexpected token: 'identifier'`. Same span, same error class. Already
on the escalation record.

## Closed since pass 1

Both defects your pass-1 report routed are fixed and re-verified:

1. `using` / `await using` statement nodes ended one token early — ranges now agree
   with acorn.
2. The `await using` async-context error fired on non-declarations — now gated.
