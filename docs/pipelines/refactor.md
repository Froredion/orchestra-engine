# refactor

‹ back to [README](../../README.md#pipelines)

**refactor** — behavior-preserving structural cleanup, made on green tests:
scope → assess → refactor → verify → report. Self-routing: verify hops back
to redo the assessment or the refactor when behavior didn't hold. Stops
unless there's a green baseline to prove behavior held. The boundary-moving
changes it deliberately *won't* auto-apply are written out as `PROPOSALS.md` —
the input [architecture](architecture.md) picks up.
