# Why the `architecture` pipeline is shaped the way it is

`architecture` deliberately changes the *structure* of an existing system across
module boundaries — it moves, splits, or merges components and **changes their
internal contracts** — while the behavior an outside caller can observe stays
the same. That one fact is why it is a separate pipeline and not a preset of
another:

- `refactor` is behavior-preserving *and* contract-preserving on a small
  surface; its own report stage writes the boundary-moving ideas it refuses to
  auto-apply out as `PROPOSALS.md` — "the module that wants splitting, the
  abstraction that's wrong at the root, the boundary in the wrong place." That
  file is precisely this pipeline's input. `architecture` is the pipeline that
  takes on the STRATEGIC bucket `refactor` hands back.
- `feature` adds a capability; `investigation` is read-only. Neither rearranges
  existing structure across boundaries.

It is also the most dangerous pipeline in the set, because moving boundaries is
exactly where automated agents do the most damage (see the 2025 LLM evidence
below). Its entire value is therefore *discipline*: establish the real structure
from the code, choose the new boundary against principled criteria, execute as a
sequence of small behavior-preserving steps behind seams, and leave behind a
guard so the improvement can't quietly erode back. The stage decomposition
follows the software-architecture and safe-restructuring literature rather than
the intuition that architecture work is mostly "drawing the better diagram."

This file is provenance for maintainers. The engine ignores it — only numbered
`NN-name.md` files are loaded as stages — so it never reaches an agent at
runtime.

**On the citations.** Each reference was verified against its primary source
(publisher record, author-hosted text, or thesis record), not cited from memory:
authors, venue, and year were checked. Each entry is marked **[peer-reviewed]**,
**[primary source]** (a foundational book/essay/technical report by its
originator, not refereed), or **[peer-reviewed / thesis]**. One caveat carried
from the research: **Perry & Wolf (1992)**, though the most-cited "foundations of
architecture" paper, appeared in *ACM SIGSOFT Software Engineering Notes* — an
informal bulletin, not a refereed archival venue — so it is marked **[primary
source]**, not peer-reviewed.

## The backbone: scope → map → design → select → migrate → verify → report

The arc is deliberately longer than `refactor`'s because the change is bigger
and the failure is worse. Two shapes make it *architecture*-shaped rather than
`refactor`-shaped, and both are grounded, not stylistic:

1. **`design` fans out over candidate target-states.** Design-space breadth is
   the concern this pipeline owns that its brownfield sibling does not —
   `feature`'s own PAPERS.md records that generating parallel candidate designs
   is "more a greenfield/architecture concern than a brownfield one." Evaluating
   several structural options against their tradeoffs before committing is the
   core of architecture evaluation (ATAM), and — separately — running multiple
   candidates and ranking them is the validated mitigation for LLM structural
   hallucination (Bellur et al., below). The fan-out does both jobs at once.
2. **`verify` proves three things, not one.** A refactor proves behavior held.
   An architecture change must prove behavior held, that the structure
   *measurably* improved, and that something now *guards* the improvement — the
   three failure modes the literature says structural change is prone to.

Like `bugfix`, `feature`, and `refactor`, it is self-routing (`verify` can hop
back to `select` or `migrate`) rather than a fixed march, because a structural
plan discovers it was wrong *during* the migration more often than a forward walk
can accommodate.

## Why the default is to plan, not execute

The pipeline stops after `select` with a finished plan unless the run explicitly
asks it to apply the change; `migrate` and `verify` are opt-in. This is not
timidity — it is the only conclusion the evidence actually supports. Every
credible source below grounds one of two things: the *analysis* (what the
structure is, where the debt is, which target is better) or the *safe-execution
method* for a human-supervised change (characterization, small steps behind
seams, revert-on-block, a guard). What none of them establish is that an agent
can be trusted to drive an end-to-end boundary move unsupervised — and the
peer-reviewed LLM evidence points the other way: up to 80% hallucinated
structural refactorings (Bellur et al. 2025), degraded competence on exactly the
context-dependent changes structural work is made of (Cordeiro et al. 2025), and
measurable gaps in an LLM's grasp of a real system's architecture (Soliman &
Keim 2025). So the load-bearing deliverable is the thing the research *does*
back — an evidence-based plan plus a guard — and executing it is a gated bonus
for the small, mechanical moves where the safe-execution methods can carry it.
This mirrors `feature`'s and `refactor`'s honest-handback instinct: when the part
a pipeline is least sure of is also its riskiest, it should hand that part to a
human by default rather than reach for it.

## `scope`: prove behavior can be held *before* touching structure

- **Feathers, M. (2004), _Working Effectively with Legacy Code_, Prentice Hall
  PTR.** — [primary source]. The precondition mechanism: "legacy code is code
  without tests," and the way to de-risk changing it is **characterization
  tests** — pin what the code does today (bugs included) at a level you can
  observe — plus **seams**, the places you can change behavior without editing in
  place. `scope`'s demand for an *observable* safety net, and its allowance to
  write characterization tests to close a coverage gap before proceeding, is this
  technique made a gate.
- **Perry, D.E. & Wolf, A.L. (1992), _Foundations for the Study of Software
  Architecture_, ACM SIGSOFT Software Engineering Notes 17(4):40–52.** — [primary
  source, see caveat]. Architecture = elements + form + rationale, a thing
  distinct from a program's code-level structure. This is the definitional basis
  for `scope`'s gate that asks "is this genuinely architecture work, or a
  `refactor`/`feature` job in disguise?" — the pipeline only earns its
  machinery on a change to architecture in this sense.
- **Lehman, M.M. (1980), _Programs, Life Cycles, and Laws of Software
  Evolution_, Proceedings of the IEEE 68(9):1060–1076.** — [peer-reviewed]. The
  laws of evolution (continuing change, increasing complexity) say structural
  degradation is an inherent property of systems under real-world change, not an
  accident — the reason this pipeline exists at all, and the reason `verify`
  insists on a guard.

## `map`: extract the real structure, measure the debt

- **Parnas, D.L. (1972), _On the Criteria To Be Used in Decomposing Systems into
  Modules_, Communications of the ACM 15(12):1053–1058.** — [peer-reviewed]. The
  decomposition criterion: modules should hide a design decision behind an
  interface, minimizing what one side must know about the other. This is what
  `map` means by "what the boundary hides vs. leaks" — the yardstick for whether
  a boundary is good.
- **Stevens, W.P., Myers, G.J. & Constantine, L.L. (1974), _Structured Design_,
  IBM Systems Journal 13(2):115–139.** — [peer-reviewed]. Originates coupling and
  cohesion as named design properties — `map`'s vocabulary for stating *why* the
  current boundary is bad (high coupling across it, low cohesion within a side).
- **Steward, D.V. (1981), _The Design Structure System…_, IEEE Transactions on
  Engineering Management EM-28(3):71–74** — [peer-reviewed], and **MacCormack,
  A., Rusnak, J. & Baldwin, C.Y. (2006), _Exploring the Structure of Complex
  Software Designs_, Management Science 52(7):1015–1030** — [peer-reviewed].
  The Design Structure Matrix and **propagation cost** — a repeatable number
  computed from the dependency graph that measures how change ripples. This is
  the model for `map`'s "before-measurement" and `verify`'s "after-reading": make
  the structural problem a number where you can, so "improved" is a claim.
- **van Gurp, J. & Bosch, J. (2002), _Design Erosion: Problems and Causes_, JSS
  61(2):105–119; de Silva, L. & Balasubramaniam, D. (2012), _Controlling
  Software Architecture Erosion: A Survey_, JSS 85(1):132–151; Li, W. et al.
  (2022), _Understanding Software Architecture Erosion: A Systematic Mapping
  Study_, J. Software: Evolution and Process 34(3).** — all [peer-reviewed]. How
  the implementation drifts from intended architecture over successive changes —
  the "intended vs. actual" gap `map` extracts and `verify`'s guard defends.
- **Kruchten, P., Nord, R.L. & Ozkaya, I. (2012), _Technical Debt: From Metaphor
  to Theory and Practice_, IEEE Software 29(6):18–21; Besker, T., Martini, A. &
  Bosch, J. (2018), _Managing Architectural Technical Debt: A Unified Model and
  SLR_, JSS 135:1–16; Verdecchia, R., Kruchten, P., Lago, P. & Malavolta, I.
  (2021), _Building and Evaluating a Theory of Architectural Technical Debt_,
  JSS 176:110925.** — all [peer-reviewed]. Debt has a principal and an interest;
  architectural debt has causes and triggers for repayment. This grounds `map`'s
  "symptom → cause → cost" framing and `select`'s willingness to *not* repay when
  the interest doesn't justify the disruption.
- **Soliman, M. & Keim, J. (2025), _Do Large Language Models Contain Software
  Architectural Knowledge? An Exploratory Case Study with GPT_, IEEE
  International Conference on Software Architecture (ICSA) 2025.** —
  [peer-reviewed]. Documents measurable gaps between an LLM's answers about a real
  system's architecture and ground truth. This is the direct evidence behind
  `map`'s hard rule: **extract the structure from the code, do not recall it** —
  the model's belief about the wiring is not evidence.
- **Murphy, G.C., Notkin, D. & Sullivan, K.J. (2001), _Software Reflexion
  Models: Bridging the Gap between Design and Implementation_, IEEE TSE
  27(4):364–380** (extending their FSE 1995 paper). — [peer-reviewed]. Computes
  the divergence between a declared architecture and the actual source
  dependency graph. It grounds both `map`'s extract-and-compare method and
  `verify`'s guard.

## `design`: candidate target-states in parallel, judged on tradeoffs

- **Baldwin, C.Y. & Clark, K.B. (2000), _Design Rules, Volume 1: The Power of
  Modularity_, MIT Press.** — [primary source]. Modularity splits a design into
  hidden (module-local) and visible (interface) information, and that split
  creates real option value by letting the sides evolve independently. This is
  *why* moving a boundary — not just tidying code within one — is the lever worth
  pulling, and why each candidate is judged on what independence it buys.
- **Parnas (1972)** — [peer-reviewed], reused here: each candidate is scored on
  what decision it hides behind its new interface.
- **Evans, E. (2003), _Domain-Driven Design_, Addison-Wesley.** — [primary
  source]. The **bounded context** — a boundary inside which a model and its
  language stay consistent — is the principled answer to *where* a new boundary
  should go: at a genuine seam in the domain, not at an arbitrary file-count or
  org line. Grounds `design`'s "prefer a boundary at a genuine seam" and
  `select`'s "can the boundary hold."
- **Kazman, R. et al. (1998), _The Architecture Tradeoff Analysis Method_,
  ICECCS '98, pp. 68–78, doi:10.1109/ICECCS.1998.706657** — [peer-reviewed];
  operational handbook: **Kazman, Klein & Clements (2000), _ATAM: Method for
  Architecture Evaluation_, CMU/SEI-2000-TR-004** — [primary source]. ATAM
  evaluates an architecture by eliciting quality-attribute scenarios and
  surfacing **sensitivity points** and **tradeoff points** — where one attribute
  is bought at another's expense. `design`'s "tradeoff named honestly" and
  `select`'s comparison are a lightweight adaptation of this reasoning (see
  honest gaps on the "lightweight" part).
- **Bellur, A. et al. (2025), _Leveraging LLMs, IDEs, and Semantic Embeddings for
  Automated Move Method Refactoring_, IEEE ICSME 2025.** — [peer-reviewed]. Their
  formative study found **up to 80% of LLM-suggested Move Method refactorings were
  hallucinations** — a cross-module structural refactoring, the closest published
  analogue to this pipeline's job. Their mitigation is static-analysis filtering
  plus **self-consistency / critique-and-rank over multiple samples**. The
  fan-out-then-`select` shape is that mitigation structurally: develop several
  candidates independently, then rank. It also grounds `design`'s "attack your own
  candidate."

## `select`: choose the boundary, plan the safe path — or decline

- **ATAM (Kazman et al. 1998)** — [peer-reviewed]: choose against sensitivity and
  tradeoff points, not diagram elegance.
- **Conway, M.E. (1968), _How Do Committees Invent?_, Datamation 14(5):28–31**
  — [primary source]; empirically tested by **MacCormack, A., Baldwin, C.Y. &
  Rusnak, J. (2012), _Exploring the Duality Between Product and Organizational
  Architectures_, Research Policy 41(8):1309–1324** — [peer-reviewed]; related
  DSM evidence in **Sosa, M.E., Eppinger, S.D. & Rowles, C.M. (2004), _The
  Misalignment of Product Architecture and Organizational Structure_, Management
  Science 50(12):1674–1689** — [peer-reviewed]. Structure mirrors the
  communication structure that produces it; the mirroring is strong but
  incomplete. This is `select`'s "can the boundary hold" criterion and the
  caution in `report`'s proposals: a boundary that fights how the system is
  actually owned and changed erodes back, so code-only fixes have a ceiling.
- **Kruchten/Nord/Ozkaya (2012)** — [peer-reviewed]: the principal/interest view
  is why `select` is explicitly allowed to choose "leave it."
- The migration plan is grounded in the incremental-change playbook:
  - **Fowler, M., _StranglerFigApplication_ (bliki, 2004, rev. 2024)** and
    **_BranchByAbstraction_ (bliki, 2014; Fowler credits Paul Hammant and Stacy
    Curl with the technique)** — [primary source]. Build the new structure
    *around* the old and redirect incrementally; put an abstraction in front so
    old and new coexist on trunk during the swap.
  - **Sato, D., _ParallelChange_ (bliki, 2014)** — [primary source]. Expand →
    migrate → contract: the safe sequence for changing a contract with many
    callers — directly the internal-interface move this pipeline makes.
  - **Ellnestam, O. & Brolund, D. (2014), _The Mikado Method_, Manning.** —
    [primary source]. The sequencing algorithm: attempt the change, when it
    breaks something record the prerequisite and **revert to the last known-good
    state**, recurse on prerequisites first. This is `select`'s "prerequisites
    come first" and `migrate`'s revert-on-block.
  - **Opdyke, W.F. (1992), _Refactoring Object-Oriented Frameworks_, PhD thesis,
    Univ. of Illinois Urbana-Champaign (adv. Ralph Johnson).** — [peer-reviewed /
    thesis]. The academic origin of refactoring: restructuring operations with
    **preconditions that make each behavior-preserving**. With **Fowler, M.
    (1999/2018), _Refactoring_, Addison-Wesley** — [primary source] — it grounds
    the rule that a large structural change is only safe decomposed into small,
    individually behavior-preserving, individually verified steps.

## `migrate`: execute green-to-green, verify every move

- **Opdyke (1992)** + **Fowler, _Refactoring_ (2018)** — the discipline `migrate`
  enforces: one behavior-preserving step at a time, tests between each, never mix
  a structural step with a behavioral one.
- **Strangler Fig / Branch by Abstraction / Parallel Change** — coexist-then-
  switch so the tree is never broken between steps.
- **Ellnestam & Brolund (2014)** — revert to last-known-good rather than leave a
  half-done migration.
- **Bellur et al. (2025)** — [peer-reviewed]: the 80%-hallucination result is why
  `migrate` runs the code after *every* structural move and never advances on an
  edit it only reasoned about.
- **Cordeiro, J., Noei, S. & Zou, Y. (2025), _An Empirical Study on the Code
  Refactoring Capability of Large Language Models_, ACM TOSEM 2025.** —
  [peer-reviewed]. LLMs handle mechanical smells well but "reliably underperform
  on complex, context-dependent refactorings requiring real program
  comprehension" — i.e. exactly where structural change lives. Corroborates the
  small-steps-plus-verification insistence over big confident leaps.
- **Feathers (2004)** — seams let a step be tested in isolation.

## `verify`: behavior + structure + guard

- **Feathers (2004)** — the observable characterization suite green and unchanged
  proves behavior held.
- **MacCormack/Rusnak/Baldwin (2006)** + **Murphy/Notkin/Sullivan (2001)** —
  re-extract the structure and compare to `map`'s before-reading: the debt is
  measurably gone, or the run didn't earn its risk.
- **Ford, N., Parsons, R. & Kua, P. (2017), _Building Evolutionary
  Architectures_, O'Reilly** (2nd ed., with Sadalage, 2022). — [primary source].
  Originates the **fitness function**: an executable, automatically-run check
  that fails when an architectural goal is violated. Its rigorous peer-reviewed
  ancestor is the reflexion model (Murphy/Notkin/Sullivan). Together they ground
  `verify`'s "install a guard" — because Lehman's laws and the erosion surveys say
  the boundary drifts back otherwise.

## `report`, and the seam back to `refactor`

`report` mirrors `refactor`'s ending exactly — an always-runs handback plus a
`PROPOSALS.md` of the deeper work a human should weigh. This closes a loop:
`refactor` hands its boundary-moving ambition *to* this pipeline; this pipeline
hands *its* deferred ambition (the next boundary, the org change Conway implies)
onward, to a human or a future `architecture` run. The debt framing
(Kruchten/Nord/Ozkaya) is how `report` states what principal was paid down.

## The premise, and shapes deliberately taken

- **Xia, C.S., Deng, Y., Dunn, S. & Zhang, L. (2024), _Agentless: Demystifying
  LLM-based Software Engineering Agents_, arXiv:2407.01489; published at FSE
  2025.** — [peer-reviewed]. A fixed localize → repair → validate process beats
  far more complex autonomous agents. Agentless is a *fixing* pipeline, so this
  transfers to architecture work by analogy — the lesson is the fixed-stage
  architecture, which is this whole engine's premise.
- **Fan-out is taken here, unlike in `feature`.** `feature` considered and
  rejected parallel candidate designs as its default, explicitly flagging
  design-space breadth as an architecture concern. This pipeline is where that
  concern lives, and the fan-out doubles as the self-consistency defense Bellur
  et al. validate. Recording the contrast keeps the two pipelines' choices
  legible.

## Honest gaps

Stated plainly so no one cites judgment as evidence:

- **There is no peer-reviewed evidence for end-to-end LLM-driven architectural
  migration.** The 2025 papers each study *one* refactoring type (Bellur:
  Move Method; Cordeiro: a smell catalogue) or *one* comprehension task
  (Soliman & Keim) — not the whole arc this pipeline runs. The pipeline as a
  whole is engineering judgment scaffolded on the individually-grounded
  safe-execution methods, not a validated procedure. Treat any claim that "this
  pipeline is proven to work" as unsupported.
- **The fan-out-as-self-consistency mapping is an analogy.** Bellur et al.
  validated critique-and-rank for Move Method suggestions, not for choosing among
  whole target architectures. The transfer is plausible and deliberate, not
  demonstrated.
- **Propagation cost / edge counts are one proxy for "improved," not a
  universal one.** Some real improvements — replacing a wrong abstraction,
  clarifying an interface — barely move a file-level number. That is exactly why
  `verify` accepts a *qualitative* property (cycle broken, leak plugged)
  alongside a metric, rather than demanding a number.
- **ATAM is borrowed in spirit, not in ceremony.** Full ATAM is a facilitated
  multi-stakeholder workshop. This pipeline takes its *reasoning* — quality-
  attribute tradeoffs and sensitivity points — into a single agent's analysis. A
  useful adaptation, not the method as its authors run it.
- **"Fitness function" is a practitioner term.** The load-bearing peer-reviewed
  anchor for the mechanism is the reflexion model; Ford/Parsons/Kua is cited as
  its named, popular form, marked [primary source].
- **The "fixed pipeline beats freeform" premise (Agentless) is proven for a
  fixing task,** and transferred to architecture work by analogy, not by direct
  study.
