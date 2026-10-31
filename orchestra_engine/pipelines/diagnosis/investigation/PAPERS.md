# Why the `investigation` pipeline is shaped the way it is

`investigation` answers a question about a system the developer has in front of
them — its deliverable is a grounded, cited *understanding*, not a code change.
It is deliberately distinct from `bugfix` (which commits to a fix) and `research`
(whose evidence is the web, not the running code). The stage decomposition is not
invented; it follows five decades of debugging and program-comprehension research
plus the current LLM-agent literature.

This file is provenance for maintainers. The engine ignores it — only numbered
`NN-name.md` files are loaded as stages — so it never reaches an agent at runtime.

## The backbone: investigation is a hypothesis-driven inquiry loop

Effective investigation is a loop — question → conjecture → gather evidence by
running the code → confirm/refute → repeat — not a one-shot answer. This is the
`frame → hypotheses → investigate → assess` cycle, with `assess` able to route
back.

- **Letovsky (1987), _Cognitive Processes in Program Comprehension_** —
  comprehension as "inquiry" episodes: pose a question, conjecture an answer,
  verify against the code.
- **Brooks (1983), _Towards a theory of the comprehension of computer
  programs_** — top-down hypothesis refinement into subsidiary hypotheses.
  (Shapes the `hypotheses` stage.)

## `frame` is broad and systematic, not narrow

- **Littman, Pinto, Letovsky & Soloway (1986/87), _Mental Models and Software
  Maintenance_** — a systematic, breadth-first reading strategy builds *causal*
  (not just static) knowledge and catches "delocalized plan" interactions; the
  narrow as-needed strategy misses them and produces more errors.
- **von Mayrhauser & Vans (1995), _Program Comprehension During Software
  Maintenance and Evolution_** — comprehension moves across three altitudes
  (code / execution & data flow / domain intent), and the scarcest, most-needed
  thing is *connected* domain-to-code knowledge. Shapes `frame`'s three-altitude
  map and `answer`'s connected deliverable.

## `investigate` runs discriminating experiments — localization alone is not enough

- **Parnin & Orso (2011), _Are Automated Debugging Techniques Actually Helping
  Programmers?_** — a ranked suspect list is insufficient: developers don't read
  it in order, "perfect bug understanding" is false, and root-cause understanding
  requires active investigation (re-running with varied inputs, navigating
  dependencies). This is why `investigate` runs experiments rather than ranking.
- **Jones & Harrold (2005), _Empirical Evaluation of the Tarantula … Technique_**
  — spectrum-based fault localization is a cheap, effective *opening* move
  (narrow the suspects), not the answer.
- **Xia, Bao, Lo, Li (2016), _"Automated Debugging Considered Harmful" Considered
  Harmful_** — localization tooling does help real developers, even when
  imperfect.
- **Pearson et al. (2017)** and **Widyasari et al. (2022)** — suspiciousness
  ranking alone falls short on real faults and is benchmark/language-dependent:
  don't stop at a ranked list; validate on realistic cases.

## `assess`/`investigate` lean on external evidence, not the model second-guessing itself

- **Huang et al. (2024), _Large Language Models Cannot Self-Correct Reasoning
  Yet_** — without external feedback, LLM self-critique can degrade accuracy. So
  verdicts are grounded in real run output, not introspection. (Suggestive, not
  a settled result — treat as a design lean, not a law.)

## `recon` grounds hypotheses in documented behavior, not the model's memory

Some root causes live in facts a model can't reliably supply from its own weights:
a library's version-specific behavior, a spec guarantee, a bug fixed three releases
ago. Parametric recall of these is stale (frozen at the training cutoff) and
version-blind, and rediscovering them by experiment is expensive. The optional
`recon` stage fans out one agent per external question the `frame` stage flags and
retrieves what authoritative sources actually say.

- **Lewis et al. (2020), _Retrieval-Augmented Generation for Knowledge-Intensive
  NLP Tasks_ (NeurIPS)** — grounding a generator in retrieved external text beats
  relying on parametric memory for knowledge-intensive questions. The lesson
  transfers by analogy: look the fact up, don't recall it.

The stage is fenced so it strengthens rather than dilutes the pipeline's
code-grounded identity: recon emits **leads, never verdicts**. A documented
behavior is a prediction to test against the running workspace, and `investigate`
stays the only stage that confirms anything. That is the same principle as the
Huang et al. lean above — trust external, checkable evidence over the model's own
say-so — extended from what the model *concludes* to what it *knows*: the doc is
checked against the running system, not accepted because it is written down.

## A fixed, specialized pipeline can beat a freeform agent

- **Xia et al. (2024 / FSE 2025), _Agentless: Demystifying LLM-based Software
  Engineering Agents_** — a fixed sequence of stages with hierarchical
  context-narrowing is competitive with, and cheaper than, freeform agentic
  tool-use. Agentless is a *fixing* pipeline, so this transfers to investigation
  by analogy — the lesson is the fixed-stage architecture, which is exactly what
  this engine is.

## Honest gaps

The research did **not** establish, for investigation specifically: sampling /
self-consistency versus iterate-one-attempt; adversarial self-verification of an
agent's own claims; or that "fixed pipeline beats freeform" holds for pure
investigation (Agentless proves it only for a fixing task). Those parts of the
design are engineering judgement, not evidence-backed — don't cite them as if
they were.

The `recon` stage is likewise judgement, not a result established for
investigation. Retrieval-augmentation (Lewis et al.) is shown for
knowledge-intensive QA, not for grounding a debugging investigation; we lean on it
by analogy. The stage is optional — skipped unless `frame` flags a genuinely
external question — precisely because most investigations are answered by the
running code alone. And its placement seeds hypotheses up front, so it only
re-fires when `assess` routes back through `frame`; an investigation that only
discovers an external cause later (via an `assess → hypotheses` loop) won't re-run
recon. Whether escalation-on-stall would catch more real causes is untested.
