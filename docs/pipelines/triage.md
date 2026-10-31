# triage

‹ back to [README](../../README.md#pipelines)

**triage** — localizing a fault in data too large to read (the observability
case: logs, traces, spans where even a filtered query overruns one context):
scope → partition → investigate (fans out, one agent per data group) → triage
→ report. Self-routing and recursive: triage loops back to partition to re-cut
the unexplained residue on a fresh dimension, or back to investigate to go
deeper, until the residue resolves or a hop budget stops it. Aggregation-first
— it never reads a raw line before a `GROUP BY`, so context stays small no
matter the volume — and read-only; the deliverable is a localization with an
elimination trail, not a fix. The volume-bound sibling of `investigation` in
the `diagnosis/` group. See [PAPERS.md](../../orchestra_engine/pipelines/diagnosis/triage/PAPERS.md).
