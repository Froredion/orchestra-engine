# Why the `code-review` pipeline is shaped the way it is

`code-review` reviews a *change* the developer has in front of them and produces
a report a human can act on. Its deliverable is verified, prioritized findings —
not a fix (that's `bugfix`/`refactor`) and not an answer to a question (that's
`investigation`). Like `investigation`, it is read-only on product code. The
stage decomposition is not invented; it follows the empirical software-
engineering literature on what code review actually delivers, plus the current
(mostly preprint) work on LLM-based review.

This file is provenance for maintainers. The engine ignores it — only numbered
`NN-name.md` files are loaded as stages — so it never reaches an agent at runtime.

Citations are marked **[peer-reviewed]** or **[preprint / practitioner]**. The
LLM-review area is young and largely un-refereed; those claims are design *leans*,
not settled results, and are labelled so.

## The backbone: understand → review-by-lens → judge → verify → report

The convergent architecture in both the research and production practice is
*generate → verify → judge*: independent finding-generation, then a pass that
grounds each finding in evidence before anyone acts on it. The pipeline is that
loop — `scope` (understand) → `review` (generate, one lens per agent) →
`consolidate` (judge/rank) → `verify` (ground each finding by running code) →
`report`.

## `scope`: understand the change first, and keep the review small

- **Bacchelli & Bird (2013), _Expectations, Outcomes, and Challenges of Modern
  Code Review_, ICSE** — [peer-reviewed]. The paper that named "modern code
  review." Its central finding: the hardest part of review is not judgment but
  *understanding the change*, and review's delivered value (knowledge transfer,
  design feedback, awareness) exceeds pure defect-finding. Shapes `scope`'s
  understand-the-intent-first mandate and `report`'s "value beyond defects."
- **Sadowski, Söderberg, Church, Sipko & Bacchelli (2018), _Modern Code Review:
  A Case Study at Google_, ICSE-SEIP** — [peer-reviewed]. Reviews at Google are
  small (median change ~24 lines, most touching few files), fast, usually one
  reviewer; effectiveness comes *from* the small batch size. Shapes `scope`'s
  "size the review to the change, don't tour the repo."
- **Kemerer & Paulk (2009), _The Impact of Design and Code Reviews on Software
  Quality_, IEEE TSE**, corroborated by the industrial **Cohen (2006), _Best
  Kept Secrets of Peer Code Review_** (SmartBear/Cisco, [practitioner]) —
  review effectiveness holds up only at bounded rates / small chunks and falls
  off on large ones. Same lesson, independent sources.

## `review`: independent generation, one specialized lens per agent

- **Porter, Siy & Votta (1997), _An Experiment to Assess the Cost-Benefits of
  Code Inspections_, IEEE TSE** (and the meeting-vs-individual experiments in
  that line of work) — [peer-reviewed]. Synchronous review *meetings* add little
  new true-defect discovery over independent individual preparation; their real
  value is filtering out false positives. This is why `review` agents work in
  parallel and blind (generation), and cross-checking is deferred to
  `consolidate`/`verify` (filtering).
- **Fagan (1976), _Design and Code Inspections to Reduce Errors in Program
  Development_, IBM Systems Journal** — [peer-reviewed], foundational. Formal
  inspection separated *finding* defects from *fixing* them, with a distinct
  verification/follow-up step. Modern review kept the find/fix separation and
  dropped the ceremony; this pipeline keeps it too — it finds, it never fixes.
- The specialized-generator split (one lens per agent, rather than one generalist
  reviewer) matches production practice: **cubic.dev** [practitioner] reports a
  ~51% false-positive reduction from decomposing one review agent into
  role-specific micro-agents; **CodeRabbit** [practitioner] runs role-split
  models with a separate judge pass.

## The lens set: maintainability is first-class, not an afterthought

- **Mäntylä & Lassenius (2009), _What Types of Defects Are Really Discovered in
  Code Reviews?_, IEEE TSE** — [peer-reviewed]. ~75% of what review finds is
  "evolvability" (maintainability, readability, design); only ~25% is functional.
- **Beller, Bacchelli, Zaidman & Jürgens (2014), _Modern Code Reviews in
  Open-Source Projects: Which Problems Do They Fix?_, MSR** — [peer-reviewed].
  Independently found essentially the same ~3:1 split on OSS data. Two unrelated
  studies converging is why the `maintainability` lens is weighted heavily and
  `report` refuses to be defects-only.
- **Chillarege et al. (1992), _Orthogonal Defect Classification_, IEEE TSE** —
  [peer-reviewed]. The type/trigger taxonomy (function, interface, checking,
  timing, algorithm, …) behind the lens categories, including the interface/
  compatibility lens.
- **Bosu, Greiler & Bird (2015), _Characteristics of Useful Code Reviews_, MSR** —
  [peer-reviewed]. Comment usefulness falls as a changeset's file count rises;
  much of review output is not useful to the author. Shapes the "don't pad,
  report only concrete findings" pressure and the changeset-size discipline.

## `consolidate` + `verify`: the false-positive problem is the whole point

The verification stages exist because *unverified LLM findings are, in the field,
mostly noise* — and noise doesn't just waste time, it destroys the review.

- **Chowdhury et al. (2026), _From Industry Claims to Empirical Reality: … Code
  Review Agents in Pull Requests_, MSR (accepted)** — [peer-reviewed]. On real
  GitHub PRs, changes reviewed only by code-review agents merged far less often
  than human-reviewed ones and most agents' findings sat in the lowest signal-
  quality band. Direct evidence that unfiltered agent review underperforms.
- **"Rethinking Code Review Workflows with LLM Assistance" (2025)** — [preprint].
  Reviewers "get flooded with false positives, and then you miss the real issues"
  — a false-positive-heavy stage trains users to stop reading, so it's worse than
  no stage. Also: findings must be anchored to file+line and kept short. Shapes
  `report`'s format and the whole cut-the-noise posture of `consolidate`.
- **McAleese et al. (2024), _LLM Critics Help Catch LLM Bugs_, OpenAI**
  (arXiv:2407.00215) — [preprint, high-credibility]. A dedicated critic separate
  from the generator catches more bugs than unaided human review — but critics
  can be gamed and *critiquing is easier than verifying a full fix*. Supports the
  separate `verify` gate rather than trusting the reviewer's own confidence.
- **Huang et al. (2024), _Large Language Models Cannot Self-Correct Reasoning
  Yet_** — [preprint]. Without *external* feedback, LLM self-critique can degrade
  accuracy. So `verify` grounds every verdict in real run output, not the model
  re-examining its own reasoning. (Same citation the `investigation` pipeline
  leans on for the same reason.)
- **"Uncovering Systematic Failures of LLMs in Verifying Code Against Natural
  Language Specifications" (2025)** — [preprint]. LLMs judging code have an
  *over-correction bias* — they flag correct code as broken — and, crucially,
  prompting the model to explain its reasoning or suggest a fix made accuracy
  *worse*, not better. This directly shapes `verify`: refute by *running the
  code*, default to the skeptical verdict, and don't try to reason a finding into
  being real. Treat as a design lean — it's one recent, un-refereed study.

## A fixed, specialized pipeline is a defensible architecture here

- **Xia et al. (2024 / FSE 2025), _Agentless: Demystifying LLM-based Software
  Engineering Agents_** — [preprint → conference]. A fixed sequence of stages
  with hierarchical context-narrowing is competitive with, and cheaper than,
  freeform agentic tool-use. Agentless is a *fixing* pipeline, so this transfers
  to review by analogy — the lesson is the fixed-stage architecture, which is
  what this engine is.

## The other direction: what the pipeline stops promising

The stages above are all aimed at one failure — a false finding reaching the
reader — and that emphasis is right, because it is the failure with the evidence
behind it. But it made the pipeline one-sided. Every guard pushes findings *out*,
nothing counts what left, and a finding dropped at `consolidate` was invisible:
the report's false-positive tally covers only what `verify` killed, and a
candidate cut before verification never reaches the thing that counts it.

The `feature` pipeline hit the same asymmetry from the opposite side and named
the mechanism: evidence is one-sided, and the side with evidence wins — not
because anyone cheats, but because a stage asked for proof can only produce it in
one direction. Here, `verify` can prove a finding false by running the code and
can never prove one was missed. So four guards borrowed from that pipeline,
using engine machinery that already existed and was unused:

- **`ESCALATIONS.md` at `consolidate`** — a candidate cut *despite* naming a
  concrete failure gets a line. The engine restores anything removed from that
  file and `report` copies it out, so the cut becomes auditable instead of
  invisible.
- **`prior/<stage>-N/` diffing at `consolidate`** — the stage routes back to
  `review` and `scope`, and its own rewritten shortlist replaces its predecessor.
  A re-run of the reviewers is exactly the event that makes a real finding fail
  to reappear, since a lens is not guaranteed to raise the same thing twice.
- **Merge-is-lossy at `consolidate`** — two lenses reach a problem from different
  sides and each names the site or consequence its own lens made visible.
  Collapsing them keeps one and drops the other, and the result reads exactly
  like a finding that never had two halves.
- **Sibling search at `verify`** — a confirmed finding is an instance, not a
  pattern. Nothing downstream of `verify` looks for the second call site: the
  reviewer stopped at the first example, `consolidate` merged on the diagnosis
  rather than the site, and `report` writes up what it is handed. One line gets
  fixed and the siblings ship, with every document accurate.

Plus one that is not about loss: `scope` now checks the change is *complete as
delivered* — untracked or ignored files the changed code needs. This is
`feature`'s "test the artifact, not the tree" ([07-verify.md](../feature/07-verify.md)),
and it is invisible from inside the workspace by construction.

Treat these as engineering judgement transferred from a sibling pipeline, not as
findings from the literature. The false-positive evidence below does not speak to
under-reporting, and no study cited here measures what review triage discards.

## Honest gaps

- **"More/deeper review → fewer defects" is contested, not settled.** McIntosh
  et al. (2014, MSR; 2016, EMSE) linked review coverage/participation to fewer
  post-release defects, but **Krutauz, Dey, Rigby & Mockus (2020), EMSE** could
  *not* replicate it — prior defects, size, and authorship dominated. No stage
  here is built on the claim that thoroughness alone reduces bugs; the pipeline's
  case for verification rests on the false-positive evidence above, not on this.
- **The LLM-review literature is young and mostly un-refereed.** The 2024–2026
  preprints above (over-correction, reviewer-fatigue, field studies of specific
  tools) are the best available evidence, not durable fact. Specific numbers in
  them should be checked against the primary source before being quoted; some
  classic papers' exact figures (Fagan's detection rates, Rigby & Bird's
  convergent parameters) were likewise not verified against the primary text and
  are used only qualitatively here.
- **Not established for review specifically:** that per-finding *parallel*
  verification beats a single serial verify pass; that fan-out-by-lens beats a
  single generalist reviewer for *this* engine (the cubic.dev/CodeRabbit evidence
  is production practice, not a controlled study). Those are engineering
  judgement, consistent with the evidence but not proven by it — don't cite them
  as if they were.
