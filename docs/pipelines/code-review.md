# code-review

‹ back to [README](../../README.md#pipelines)

**code-review** — reviewing a change and reporting verified findings: scope
→ review (fans out, one agent per lens) → consolidate → verify (fans out,
one agent per finding) → report. Fans out twice — once per review lens, once
per finding to verify — and self-routes: consolidate can hop back to
re-scope or re-review. Read-only; it finds problems, it never fixes them,
and every finding is checked by running the code before it reaches the
report. See [PAPERS.md](../../orchestra_engine/pipelines/code-review/PAPERS.md).
