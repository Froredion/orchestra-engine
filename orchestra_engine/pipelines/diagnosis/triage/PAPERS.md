# Why the `triage` pipeline is shaped the way it is

`triage` localizes a fault inside data too large to read — the observability case
(logs/traces/spans where even a filtered query overruns any one context). Its
deliverable is a *localization with an elimination trail*, not a fix. It sits in
the `diagnosis/` group beside `investigation` because both answer without changing
code, but they attack different bottlenecks: `investigation`'s hard part is
**comprehension** (hypothesis-driven inquiry over the running code); `triage`'s
hard part is **volume** (partition the data, rule out the clean groups, recurse
into the unexplained residue). It is distinct from `bugfix`, which commits a fix.

This file is provenance for maintainers. The engine ignores it — only numbered
`NN-name.md` files load as stages — so it never reaches an agent at runtime.

## The backbone: triage is combinatorial group testing over data

The `partition → investigate (fan-out) → triage (recurse) → report` shape is not
invented; it is the classic structure of **combinatorial group testing** — pool a
population into subsets, test each subset as a unit, clear a whole clean pool in
one test, and recurse only into pools that test positive.

- **Dorfman (1943), _The Detection of Defective Members of Large Populations_,
  Annals of Mathematical Statistics** (peer-reviewed) — the origin of pooled/group
  testing; a clean pool clears all its members at once, which is exactly why ruling
  out a group is cheaper than inspecting its members. Its efficiency assumes
  *sparsity* (few defectives) and that a pool's result is a clean OR of its
  members' status — the same assumption whose failure drives our cross-group caveat
  below.

This frames the method precisely, and tells us which neighbors *not* to copy:
delta debugging minimizes a single failure-inducing input; git bisect needs a
total order with one monotone transition. Group testing is the parallel-friendly,
recurse-on-positive pattern this pipeline actually is.

## `partition` is aggregation-first — the highest-leverage move

The rule "never let a raw log line into context before a `GROUP BY`" is the whole
reason the method scales: grouped counts are the one representation whose size is
~constant (a table of N groups) regardless of underlying volume. This is
overwhelming *industry practitioner convergence* rather than a controlled result —
it is what every major platform's triage workflow does.

- **He, Zhu, Zheng & Lyu (2017), _Drain: An Online Log Parsing Approach with Fixed
  Depth Tree_, ICWS** (peer-reviewed) — log-template extraction collapses millions
  of raw lines into a small enumerable set of patterns, the cheapest general first
  partition axis. Shapes "log template/pattern" as a candidate dimension.
- **Datadog, _Building Bits AI SRE_** (industry engineering post — PRIMARY SOURCE)
  — a production incident-RCA system that forms a hypothesis, queries only the
  telemetry relevant to it, and recursively expands sub-hypotheses; they explicitly
  re-architected *away* from firing ~12 parallel tool calls into one context
  because unrelated signals bloated it and degraded reasoning. Direct evidence for
  aggregate-first, scope-narrow-then-drill.

## `investigate` fans out into isolated per-group agents

One fresh, small context per group — rather than one long session accumulating
every group's data — is the design's context-management bet, and it is grounded.

- **Liu, Lin, Hewitt, Paranjape, Bevilacqua, Petroni & Liang (2024), _Lost in the
  Middle: How Language Models Use Long Contexts_, TACL** (peer-reviewed) — a
  U-shaped attention curve: evidence mid-transcript is systematically
  under-weighted, exactly where a cause would land by the end of one long
  accumulating investigation. Isolation keeps each group's evidence near the front
  of its own small window.
- **Chroma (2025), _Context Rot_** (technical report — PRIMARY SOURCE) — across 18
  models, accuracy degrades well before the nominal context limit, and
  *well-structured* input degrades performance *more* than shuffled noise, so a
  tidy accumulating transcript is not safe merely for being readable. Suggestive,
  vendor-run; treat as a design lean, not a law.
- **Anthropic, _Building multi-agent systems_** (industry engineering post —
  PRIMARY SOURCE) — orchestrator plus isolated subagents to prevent "context
  pollution"; also the honest caveat that multi-agent is a poor fit for *tightly
  coupled* reasoning, which is why the residue (where cross-group coupling lives)
  recurses into a new small round rather than fanning out blindly.

## Ruling out needs negative evidence, and pruning is soft and reversible

A group is cleared only by *cited negative evidence*, and the ledger keeps a score
so a clearance can be undone — because a group can look clean by coincidence.

- **Jones & Harrold (2005), _Empirical Evaluation of the Tarantula Automatic
  Fault-Localization Technique_, ASE** (peer-reviewed) — spectrum-based
  suspiciousness is a cheap, effective *opening* move (rank/score, don't hard-cut),
  and correlational scoring is more noise-robust than binary elimination. Shapes
  the suspicion score and soft pruning.
- **Liblit, Naik, Zheng, Aiken & Jordan (2005), _Scalable Statistical Bug
  Isolation_, PLDI** (peer-reviewed) — isolating causes from sparsely-sampled
  predicates over a large population; the relevant lesson is statistical rule-out
  over many samples, not one-shot verdicts. The known failure mode — *coincidental
  correctness*, where a faulty element runs in a passing sample without
  manifesting — is exactly why a low-confidence CLEAR stays resurrectable.
- **Wang et al. (2023), _Self-Consistency Improves Chain-of-Thought Reasoning_,
  ICLR** (peer-reviewed) — aggregating independent reasoning paths beats a single
  path; the logic generalizes to re-checking a contested group with a second
  independent agent before a permanent drop. (We apply this only as an optional
  cross-check on contested groups, not by default.)

## The sharpest pitfall: cross-group interaction

A fault whose cause is split across two groups tests *clean in isolation in every
group* and disappears — this is group testing's OR-assumption failing, and it is
the same hazard as delta debugging's unambiguity assumption.

- **Zeller & Hildebrandt (2002), _Simplifying and Isolating Failure-Inducing
  Input_, IEEE TSE** (peer-reviewed) — ddmin's formal assumptions (monotonicity,
  unambiguity, consistency) name exactly how divide-and-conquer over a fault
  silently misleads: multiple independent causes, or an interaction whose parts sit
  in different subsets, break minimality. This is why `triage`'s cross-group check
  treats "everything cleared but the symptom persists" as the *signature* of an
  interaction fault — a finding, not an all-clear — and recurses on a different
  axis rather than concluding "no cause."

## Honest gaps

The research did **not** establish, for this pipeline specifically:

- That a **fixed-stage group-testing pipeline beats a freeform long-context agent**
  at data-triage. `Agentless` (Xia et al., FSE 2025) shows fixed-stage
  context-narrowing wins for a *fixing* task; that transfers here only by analogy.
- That the **cross-group interaction mitigation** (resurrect low-confidence
  clearances, re-cut on a new axis) actually recovers interaction faults at a good
  rate. Group-testing theory says pooling *cannot* see an untested interaction; the
  specific recovery heuristic here is engineering judgement, not a validated result.
- A validated **suspicion-scoring formula** — the score is SBFL-*inspired*, not a
  benchmarked metric, and its calibration is unproven.
- That **agent-chosen partition axis** converges better than a fixed dimension
  order — chosen on the reasoning that a mis-aligned axis never separates the fault
  (ddmin's "1-minimal but meaningless" residue), but not measured.

Several load-bearing sources here are vendor technical reports or engineering posts
(Datadog, Anthropic, Chroma), not peer-reviewed studies; they are marked PRIMARY
SOURCE and should be read as industry practice and design leans, not settled
findings. The recent agentic-RCA literature (2023–2026) is largely preprint or
industry and is deliberately kept out of the load-bearing claims above.
