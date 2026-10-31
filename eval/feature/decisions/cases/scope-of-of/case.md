---
name: scope-of-of
pipeline: feature
rules: 01-explore.md
task: request.md
answers: include, exclude
correct: include
observed: exclude
repo: https://github.com/meriyah/meriyah.git
commit: d141eb14a40b79c04d1b1db5c20c6afa3844c0d9
sparse: src
---

You are writing `SCOPE.md` for this request, against meriyah's `src/` at `d141eb1`.
You have reached one acceptance criterion and are enumerating the inputs it covers.

The criterion carries the request's own words:

> "For-of and for-await-of accept both `using` and `await using` in their heads."

`using` is contextual, so a for-of head can also bind a variable *named* `of`, which
spells the head `for (using of of y)` — a `using` declaration whose binding is `of`,
iterating `y`. The same source is also readable as the existing variable `using`
being iterated over `of y`.

Decide what this criterion's input list does with that form:

- **`include`** — it is one of the forms the criterion covers, and goes in the
  input list with the other for-of head shapes.
- **`exclude`** — it is out of scope, listed under what SCOPE deliberately leaves
  out, and no design traces it or test covers it.

Decide that one point. You do not need to write the rest of SCOPE.md, and should
not — reason about this criterion's input list only.
