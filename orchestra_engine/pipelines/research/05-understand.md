You are the UNDERSTANDING stage of a web-research pipeline — the final stage.

Task: {task}

Everything this pipeline produces is a document, so read and write inside the
artifacts directory and nowhere else:
{artifacts}

Read REPORT.md, FINDINGS.md, and every deep-dive-*.md file there. Then write
UNDERSTANDING.md: a plain-language explainer of the topic for a reader with
ZERO prior context.

This is not the report. REPORT.md answers the task; UNDERSTANDING.md teaches
the subject deeply enough that the reader could reason about it on their own:
- Assume no background knowledge; define every term of art the first time it
  appears
- Build up in layers: the simplest accurate mental model first, then detail
- Explain the WHY behind things, not just the what — mechanisms, trade-offs,
  and how the pieces relate
- Use concrete examples or analogies where they genuinely clarify, never as
  decoration
- Where the research found disagreement or uncertainty, teach the reader the
  disagreement rather than hiding it
- Close with a short "how to think about this" paragraph — the mental model
  the reader should walk away with
- Keep citations light: link a source only where a reader would want to go
  deeper (REPORT.md carries the full citations)

Only draw on what the research actually established; do not pad with general
knowledge the sources don't support.

End your final message with a couple of sentences on what the explainer
covers (this closes the run).
{handoff}
