# feature

‹ back to [README](../../README.md#pipelines)

**feature** — adding a feature to an existing codebase (brownfield):
explore → design → build → test → verify → integrate. No fan-out, but
self-routing — design and build loop back when the plan doesn't survive
contact with the code, and verify hops back to design, build, or test when
the feature doesn't hold. It leads with comprehension, because the code
already exists: the risks are fitting the existing conventions and not
breaking existing behavior, not inventing from scratch. Gated on the
workspace actually having code — pointed at an empty one it STOPs and says so
rather than pretending nothing was there. See
[PAPERS.md](../../orchestra_engine/pipelines/feature/PAPERS.md).
