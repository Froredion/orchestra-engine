# investigation

‹ back to [README](../../README.md#pipelines)

**investigation** — answering a question about a system by running it:
frame → recon → hypotheses → investigate (fans out, one agent per hypothesis)
→ assess → answer. `recon` is an optional web-search fan-out (one agent per
external question `frame` flags, skipped when there are none) that surfaces
documented behavior as leads for the investigators to test — never as answers,
since only the running code confirms anything here. Self-routing: hypotheses
and assess can hop back to frame to widen the map before committing to an
answer. Read-only; the deliverable is a grounded, cited understanding, not a
code change. See [PAPERS.md](../../orchestra_engine/pipelines/diagnosis/investigation/PAPERS.md).
