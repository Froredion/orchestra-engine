# architecture

‹ back to [README](../../README.md#pipelines)

**architecture** — deliberate structural change across module boundaries, the
strategic counterpart to `refactor`: scope → map → design (fans out, one agent
per candidate target-state) → select → migrate → verify → report. **Analysis-
first**: by default it produces an evidence-based restructuring plan and stops
there — mapping the real dependency structure, weighing candidate target-states,
and planning the migration for a human to decide on; it executes the change
only when the run explicitly asks. It takes on exactly the boundary-moving work
`refactor` refuses to auto-apply (that pipeline's `PROPOSALS.md` is a natural
input), and unlike `refactor` it *does* move internal contracts. Fans out once
over candidate structures and self-routes — when execution is requested,
`verify` proves three things before anything lands (observable behavior held,
the structure *measurably* improved, a fitness function now guards it against
eroding back) and can hop back to re-select the target or redo a step. Gated on
a provable observable baseline; if the change is really a local refactor, a
feature, or greenfield, it STOPs and points there. See
[PAPERS.md](../../orchestra_engine/pipelines/structural/architecture/PAPERS.md).

## How it composes with refactor

**Pipelines compose — and that's where `architecture` earns its place.** You
rarely reach for `architecture` cold; its natural trigger is `refactor`. Run
`refactor` routinely — after a feature, after a bugfix, on the diff that just
landed — and it keeps flagging boundary problems it won't touch (a module doing
two jobs, a leaked dependency, a seam in the wrong place) into `PROPOSALS.md`.
`architecture` is what consumes those: it turns a flagged boundary into an
evidence-based restructuring plan a human decides on, which `feature`/`refactor`
then execute. So the loop is **refactor finds → architecture plans → a human
approves → feature/refactor do**. Standalone, `architecture` is an occasional
design-doc drafter for a structural question you already have; pointed at a
codebase whose structure is sound, it correctly declines rather than manufacture
a restructure. It's the strategic half of `refactor`, not a tool you run on a
whim.
