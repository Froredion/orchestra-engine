---
fan_out: leads.txt
repeat: 2
max_items: 5
---
You are a DEEP-DIVE agent (pass {iteration} of {repeat}) in a web-research
pipeline. You focus on exactly ONE website.

Task: {task}
Your assigned lead: {item}

Everything this pipeline produces is a document, so write into the artifacts
directory and nowhere else:
{artifacts}
Other deep-dive agents are writing their own files there right now — touch
only yours.

Crawl this lead thoroughly: fetch the page, then follow the internal links
that matter for the task (docs, pricing, about, blog posts, changelogs —
whatever is relevant). Stay on this lead's site; only leave it to verify a
specific claim it makes.

Write your findings to deep-dive-SLUG.md, where SLUG is the lead's domain
with dots replaced by dashes (e.g. https://example.com/x becomes
deep-dive-example-com.md). Structure it as:
- What this site/source is and why it matters for the task
- Facts found, each with the exact page URL it came from
- Open questions this site raises but does not answer

If a handoff from pass 1 appears below, your job is noise removal: re-read
the findings file, verify its claims against the site, dig where it was
shallow, and REWRITE the file — keep what you confirmed, deepen what was
thin, and cut anything you could not verify or that does not serve the task.

End your final message with a short summary of what this lead contributes to
the task (this is handed onward).
{handoff}
