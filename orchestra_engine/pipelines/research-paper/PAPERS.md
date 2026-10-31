# Why the `research-paper` pipeline is shaped the way it is

`research-paper` turns a research direction a human gives it into an empirical
systems / ML paper draft, plus an honest account of what a human still has to
supply. Its deliverable is a *draft and a handback* — not a submission, and not a
claim of a finished paper. The stage decomposition is not invented; it follows
the small but now peer-reviewed body of work on autonomous "AI scientist"
systems, and it is deliberately built around that work's single clearest
finding: the hard part is not writing the paper, it's having a real, novel,
honestly-substantiated contribution to write about.

This file is provenance for maintainers. The engine ignores it — only numbered
`NN-name.md` files are loaded as stages — so it never reaches an agent at
runtime.

Citations are marked **[peer-reviewed]**, **[preprint / primary source]**, or
**[journalism / practitioner]**. The autonomous-research area is young; the
un-refereed claims are design *leans*, not settled results, and are labelled so.

## The headline, and the honest ceiling it sets

- **The AI Scientist team, _Towards end-to-end automation of AI research_,
  Nature (2026)** — [peer-reviewed]. An agent that ran the full ML research
  lifecycle; one fully AI-generated paper scored an average **6.33** in blind
  peer review at an **ICLR 2025 workshop**, surpassing the average human
  acceptance threshold. This is the existence proof that the task is possible at
  all. It is also the ceiling: the paper passed *barely*, at a **workshop** bar.
- **TechCrunch, _Sakana claims its AI paper passed peer review — but it's more
  nuanced_ (2025)** — [journalism]. The caveats that keep this pipeline honest:
  the workshop's acceptance rate was ~60–70%; the authors themselves said **none**
  of their submissions cleared their internal bar for an ICLR *conference-track*
  paper; the paper was **withdrawn before meta-review**. So the pipeline's target
  is "a sound, honestly-scoped draft a human can finish," never "an accepted
  paper," and `finalize` is required to state the standing honestly.

The design consequence is `scope`'s gate and `finalize`'s STOP handback: the most
valuable thing an autonomous paper-writer can do is **refuse to manufacture a
paper that isn't warranted**. Producing paper-shaped text with no real
contribution is exactly the paper-mill failure mode, and generating new ideas and
text solely from an LLM without substantive human vetting is treated as
misconduct by most venues. Abstaining with a handback is the same policy `bugfix`
uses when a bug won't reproduce — and it is on-thesis for this engine.

## The backbone: scope → novelty → design → experiment → analyze → draft → review → finalize

The autonomous-scientist systems converge on one architecture: **ideate → check
novelty against the literature → run experiments → analyze → write → review**,
with the model's *scaffolding* (search, tool use, code execution, an automated
reviewer, iteration) doing the work, not a single prompt.

- **The AI Scientist-v2, _Workshop-Level Automated Scientific Discovery via
  Agentic Tree Search_ (arXiv 2504.08066, 2025)** — [preprint / primary source].
  Names the components the working system needed: literature search and reading,
  parallelized **agentic tree search** over experiment directions, code
  implementation and execution, vision models to judge figures, and an
  **automated reviewer**. Each maps to a stage here: `novelty` (search + read),
  the `ideas.txt`/`experiments.txt` fan-outs (parallel exploration), `experiment`
  (execution), `analyze` (figure planning), and `review` (the automated
  reviewer). The Nature paper is explicit that **humans defined the broad
  research direction** and kept oversight — which is why the pipeline's input is a
  human-set direction and `finalize` hands work back to a human.

- **_From prompt engineering to agent engineering_, BioData Mining / Springer
  (2025)** — [peer-reviewed]. The field-level finding behind this whole pipeline:
  for real scientific work the leverage is in the *architecture of cognition* —
  planning, memory, tool use, reflection — not in crafting one ideal instruction.
  A prompt is now a scaffold, not a script. That is the premise of building this
  as a staged loop rather than one "write me a paper" call.

## `scope`: honest gate first, adaptive mode second

The gate exists because of the ceiling above. The **EXPERIMENTAL vs WRITEUP**
split exists because a systems/ML claim earns its place by being *measured*; when
the workspace can't run the experiment, the paper may only argue from material
already in hand, and may never invent a result to fill the gap. `scope` derives
the single `run-experiments.txt` gate so the experiment fan-out has one
unambiguous skip condition (the engine allows one `skip_if` per stage).

## `novelty`: the documented weak point gets its own adversarial stage

- **_Prompt-to-Paper: Agentic AI System for Bioinformatics_ (arXiv 2607.05456,
  2026)** — [preprint]. An end-to-end agentic paper-writer scored only in the
  **high-60s / low-70s on novelty and contribution** — it "recombines existing
  knowledge well but rarely proposes a genuinely new analytical move." Novelty is
  the thing these systems are worst at, so it gets a dedicated stage, one agent
  per candidate angle, each told to **default to the skeptical verdict** and to
  name the closest prior work. Overclaimed novelty is the fastest desk-reject.

## `review`: verify every citation, and be the hostile reviewer

- **_Can LLMs Identify Critical Limitations within Scientific Research?_, ACL
  2025** — [peer-reviewed]. LLMs frequently **fail to surface a paper's real
  limitations**, often for lack of domain depth. So limitations are made a
  first-class, required section in `draft`, and `review` is charged with hunting
  overclaims the draft's own author-agent would miss.
- **Inaccurate and fabricated citations** are the single most-cited concrete
  failure of AI-written papers (flagged in the Nature paper's own limitations).
  `review` therefore must **fetch and verify every citation against its real
  source** — the same generate-then-verify discipline the `code-review` pipeline
  applies to findings, applied here to references. A citation that doesn't resolve
  or doesn't support its sentence is a defect, not a detail.

## The human-in-the-loop channel: `human_needs.txt`

The pipeline stops short of faking what a human does better. Hand-drawn
architecture and conceptual diagrams, figures that encode design intent, real
screenshots, expert-labelled data, and results needing hardware the workspace
lacks are flagged — by `design`, by blocked `experiment` agents, and by
`analyze`'s figure plan — into a single `human_needs.txt` that `draft` turns into
labelled placeholders and `finalize` hands back as a to-do list. A clean
placeholder with a spec is worth more than a mediocre agent-drawn figure a human
has to find and remove. To keep the file race-free, the parallel `experiment`
agents only note human-needs *inside their own results files*; the single-session
`analyze` stage consolidates.

## What this pipeline is not

It is not a shortcut to a publishable paper, and it does not claim to be one. The
peer-reviewed evidence says a fully-autonomous draft clears, at best, a workshop
bar and needs a human for direction, figures, and final judgment. This pipeline
is built to get an honest draft to that point and to be candid about the rest —
including declining to write a paper when there isn't one to write.
