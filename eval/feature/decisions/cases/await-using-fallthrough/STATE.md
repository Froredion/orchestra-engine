What is on the branch, and what has been measured. Head `f75faea`, branched from
`d141eb1` (`main`). Nothing here is a conclusion, only what was run and what came
back.

## How the `await using` async-context check is implemented

`src/parser.ts:3492-3509`. The async-context check is placed **before** the
`isUsingDeclarationStart(parser)` test, so it runs on any `await` followed by
`using` on the same line, whether or not a declaration follows:

```ts
if (parser.getToken() === Token.UsingKeyword && !(parser.flags & Flags.NewLine)) {
  // async-context check runs here, before the predicate below
  if (!inAsyncContext && !atModuleTopLevel) {
    parser.report(Errors.AwaitUsingNotInAsync);
  }
  if (isUsingDeclarationStart(parser)) {
    /* … parse an `await using` declaration … */
  }
  /* … otherwise fall through to an ordinary await expression … */
}
```

`DESIGN.md` §4.3 requires that the check run **"before the declaration is
parsed"**.

## What the placement does to inputs that are not declarations

Measured against `main`, `next: true`:

```
function f(){ await using; }      main: [1:20-1:25] Unexpected token: 'identifier'
                                  head: [1:14-1:25] 'await using' declarations are only allowed inside async…

function f(){ await using.x; }    same divergence

function f(){ await using }  (module)  main: Await is only valid in async functions
                                       head: 'await using' declarations are only allowed inside async…

function f(){ await of; }         main and head agree — Unexpected token: 'of'
```

Every one of these inputs is an error on `main` and an error on the branch. No
input in this set changes parse status; what changes is which message comes out.
acorn 8.16 and V8 give the ordinary diagnostic on all three.

An illustrative fix — moving the check inside the `isUsingDeclarationStart`
branch — was tried: every AC-9 / AC-13 / AC-19 case still reports the correct
error, all 205 new tests stay green, and all 94,676 suite tests stay green. That
move also skips the ordinary await-context validation on the fall-through path,
so it is not the finished fix — but it localises the defect and shows the design
does not need revisiting.

## Suite

- Project suite: **94,676 passing, 0 failing.**
- Every AC has at least one discriminating test. All AC-9, AC-13 and AC-19 cases
  pass, in every listed input.
- `using` appears as source text in one pre-existing test file, which SCOPE recorded.

## Also open on this branch, and not what you are deciding here

`using[a] = b` and `using [a] = b` stop parsing under `next: true`, because AC-12
is implemented over both pattern forms. Stated AC-12 against inferred AC-15;
recorded on `ESCALATIONS.md` for a human. Out of scope for this decision.
