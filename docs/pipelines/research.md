# research

‹ back to [README](../../README.md#pipelines)

**research** — noise-removing web research: search ×3 → findings →
deep-dive (fans out, one agent per lead link, ×2 passes each) → report →
understand. Passes compound and deep-dive fans out, but it's forward-only —
no loop-backs.

```
search ×3 ──▶ findings ──▶ deep-dive (∥ per lead, ×2) ──▶ report ──▶ understand
```

1. **search** (3 passes, cumulative) — each pass runs several web searches
   from a different angle, crawls the promising hits, and writes
   `search-findings-N.md`. Each pass receives *every* earlier pass's summary
   and session ID, so it can avoid covered ground, note where passes disagree,
   and open any earlier transcript that's too thinly summarized.
2. **findings** — consolidates all search findings, drops the noise, writes
   `FINDINGS.md` plus `leads.txt` (lead URLs, ranked best-first).
3. **deep-dive** — one agent per lead link (top 5, parallel), each focused
   entirely on that one site. Pass 1 crawls and writes findings; pass 2 gets
   those findings, re-crawls to verify, and rewrites the file with the noise
   cut.
4. **report** — merges everything into a cited `REPORT.md`.
5. **understand** — writes `UNDERSTANDING.md`: a zero-context, plain-language
   explainer of the topic itself. The report answers the task; this one
   teaches the subject — first principles, terms defined, the why behind
   things — so you can deeply understand what was researched.

Change the pass counts per run without touching the files (tell your agent
([How to use it](../../README.md#how-to-use-it)), or pass them yourself):

```bash
./orchestra "research the best X for Y" --pipeline research \
    --repeat search=5,deep-dive=3 --max-items deep-dive=10 -y
```
