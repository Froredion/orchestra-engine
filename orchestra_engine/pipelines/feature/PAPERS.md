# Why the `feature` pipeline is shaped the way it is

`feature` adds a capability to a codebase that **already exists**. That one fact
is what shapes it, and why it is not a generic "build software" pipeline with a
different first stage. Building from nothing walks forward once and invents its
own constraints; `feature` drops into a living system, where the empirical
software-engineering literature is consistent about where the cost and the risk
actually are — *understanding* the code you're changing, and *not breaking* the
behavior that's already there. The stage decomposition follows that evidence
rather than the intuition that feature work is mostly "writing the new code."

This file is provenance for maintainers. The engine ignores it — only numbered
`NN-name.md` files are loaded as stages — so it never reaches an agent at
runtime.

**On the citations.** Every reference below was verified against its primary
source (publisher record or author-hosted full text), not cited from memory:
authors, venue, year, and the specific quoted finding were each checked, and the
numbers a source could not actually support were dropped rather than rounded
into place. Each entry is marked **[peer-reviewed]**, **[practitioner]**, or
**[practitioner/foundational]**. Two entries were preprints when this pipeline
was first drafted and have since appeared at refereed venues; they're marked
with the published venue and carry the arXiv id for open access.

## The backbone: explore → design → build → test → verify → integrate

The shape is a brownfield inversion of the usual forward march. Its distinctive
stages are the two a build-from-nothing workflow has no reason to own: an
`explore` stage at the front, because on existing code comprehension *is* the
work, and a separate adversarial `verify` stage before the end, because on
existing code the dominant failure is a regression a green suite doesn't show.
Like `bugfix` and `refactor`, it is
self-routing (`design`/`build`/`verify` can loop back) rather than a fixed
forward march, because feature work discovers that its plan was wrong *during*
building far more often than a greenfield build does.

## `explore`: comprehension first, because that is the actual work

The evidence that developers spend most of their time understanding code, not
writing it, is strong, empirical, and recent enough to trust with numbers —
which is why this pipeline opens with a stage that does nothing but understand.

- **Minelli, Mocci & Lanza (2015), _I Know What You Did Last Summer — An
  Investigation of How Developers Spend Their Time_, ICPC, pp. 25–35,
  doi:10.1109/ICPC.2015.12** — [peer-reviewed]. An instrumented-IDE study (740
  sessions, 18 developers, ~200 hours, ~5M IDE events) measured the split
  directly: *"Program understanding is … the dominant activity … reaching a
  value of roughly 70%,"* against ~5% spent editing. If understanding is where
  the time goes, a feature pipeline that opens by editing has skipped its most
  expensive step; `explore` makes it stage one.
- **Xia, Bao, Lo, Xing, Hassan & Li (2018), _Measuring Program Comprehension: A
  Large-Scale Field Study with Professionals_, IEEE TSE 44(10):951–976,
  doi:10.1109/TSE.2017.2734091** — [peer-reviewed]. The larger, multi-company
  corroboration (79 professionals, 7 industrial projects, 3,244 hours, tracking
  the browser and docs as well as the IDE): developers spend *"up to ∼58% of
  their time on program comprehension."* Two independent studies putting the
  number between ~58% and ~70% is why `explore` is not optional.
  *(The oft-quoted "10:1 reading-to-writing" ratio, by contrast, traces to
  Martin's_ Clean Code _as an unsourced heuristic — not used here; the measured
  studies above say the same thing with real data.)*
- **von Mayrhauser & Vans (1995), _Program Comprehension During Software
  Maintenance and Evolution_, IEEE Computer 28(8):44–55, doi:10.1109/2.402076**
  — [peer-reviewed] (IEEE Computer is a peer-reviewed magazine, lighter-weight
  than an archival journal — flagged for honesty). A comparative survey of six
  code-cognition models: developers *"build a mental model of the software"*
  top-down from expectations, bottom-up from the code, and by an integrated
  strategy between them. `explore`'s "trace how the area works today, from entry
  point to seam" is that process made a deliverable.
- **Ko, Myers, Coblenz & Aung (2006), _An Exploratory Study of How Developers
  Seek, Relate, and Collect Relevant Information during Software Maintenance
  Tasks_, IEEE TSE 32(12):971–987, doi:10.1109/TSE.2006.116** — [peer-reviewed].
  Maintenance is dominated by *seeking and relating* information, and incomplete
  search is expensive: *"An average of 88 percent … of developers' searches led
  to nothing of later use in the task."* Shapes `explore`'s mandate to find the
  integration points and constraints *precisely* — a partial map of where the
  feature lands is the documented failure mode, not a minor imperfection.
- **Bacchelli & Bird (2013), _Expectations, Outcomes, and Challenges of Modern
  Code Review_, ICSE, pp. 712–721, doi:10.1109/ICSE.2013.6606617** —
  [peer-reviewed]. Its central finding — *"code and change understanding is the
  key aspect"*, and *"understanding the code takes most of the reviewing time"* —
  is why `explore` must produce a real map before `design` runs, the same way it
  grounds the `code-review` pipeline's understand-first `scope`.

## The STOP gate: an empty workspace is the wrong pipeline, said honestly

`explore` writes a `PROCEED`/`STOP` verdict, and `STOP` on a greenfield
workspace routes straight to `integrate`, which hands back saying there is
nothing here to fit into. This is the same honest-handback pattern `refactor`
and `code-review` use when there's no green baseline or nothing to review: a
pipeline whose entire value depends on a precondition — here, existing code and
conventions to fit and existing behavior to preserve — should refuse cleanly
rather than fabricate one. A `feature` run on an empty repo could *produce*
code, but every stage that justifies this pipeline would be acting on nothing,
and the handback is worth more to the human than the output would be.

## `design`: fit the conventions, keep the slice small

- **Brooks (1975/1995), _The Mythical Man-Month_, ch. 4** —
  [practitioner/foundational] (a monograph, never refereed, but among the most
  cited works in the field). The source of *conceptual integrity*: *"I will
  contend that conceptual integrity is the most important consideration in
  system design."* A feature that ignores the house style buys local optimality
  at the cost of the system's coherence; `design`'s "make it look like it
  belonged there all along" is conceptual integrity applied to one change, and
  grounds the instruction to reuse existing patterns and helpers before
  inventing parallel ones.
- **Sadowski, Söderberg, Church, Sipko & Bacchelli (2018), _Modern Code Review:
  A Case Study at Google_, ICSE-SEIP, pp. 181–190, doi:10.1145/3183519.3183525**
  — [peer-reviewed]. Changes at Google are small: *"the median number of lines
  modified is 24,"* 90% touch fewer than 10 files, and >75% have a single
  reviewer. The paper *documents* that small changes are the norm and that
  review is fast; it does not claim small size is the sole *cause* of
  effectiveness (that step is this pipeline's design lean, not Sadowski et al.'s
  finding). Either way it grounds `design`'s "smallest vertical slice" and the
  explicit YAGNI list — the feature is sized to be reviewable, and speculative
  generality is recorded as deliberately excluded rather than built.
- **Middleton & Joyce (2012), _Lean Software Management: BBC Worldwide Case
  Study_, IEEE Transactions on Engineering Management 59(1):20–32** —
  [peer-reviewed]. A quantified case study of a software team: reducing **batch
  size** (among other lean changes) improved lead time by 37% and cut
  customer-reported defects by 24% over 12 months. The peer-reviewed,
  numbers-backed instance of the mechanism that the practitioner DORA/*Accelerate*
  research (Forsgren, Humble & Kim, 2018 — [practitioner], survey-scale, not
  refereed) argues at industry scale: thin slices ship better than big ones.

## `test` and `verify`: prove the new behavior *and* that the old behavior held

The split between a `test` stage (write the coverage, run the suite) and a
separate adversarial `verify` stage (distrust the green result) is the same
find/prove separation `bugfix` (fix → verify) and `refactor` (refactor →
verify) already make. Three verified lines of evidence justify it.

**Modifying existing code is where regressions come from.**
- **Śliwerski, Zimmermann & Zeller (2005), _When Do Changes Induce Fixes?_,
  MSR 2005, ACM SIGSOFT SEN 30(4):1–5** — [peer-reviewed]. The foundational SZZ
  work linking version history to bug fixes to identify *fix-inducing changes* —
  the methodological root of "changes are how defects enter." (Its per-project
  bug-inducing percentages are widely re-quoted; this pipeline cites the paper
  for the method and does **not** repeat a figure it could not confirm from the
  primary text.)
- **Purushothaman & Perry (2005), _Toward Understanding the Rhetoric of Small
  Source Code Changes_, IEEE TSE 31(6):511–526** — [peer-reviewed]. Even the
  smallest change is not free: there is a nonzero (*"less than 4 percent"*)
  probability that a *one-line* change introduces a fault. Small does not mean
  safe — which is why `verify` re-runs the full suite against the baseline even
  for a tidy feature.
- **Yin, Yuan, Zhou, Pasupathy & Bairavasundaram (2011), _How Do Fixes Become
  Bugs?_, ESEC/FSE 2011** — [peer-reviewed]. Across Linux, OpenSolaris, FreeBSD
  and a commercial OS, **14.8–24.4%** of sampled post-release fixes were
  themselves incorrect or incomplete. If code written *specifically to be
  careful* regresses that often, a feature deserves an explicit regression gate,
  not trust — hence `explore`'s baseline capture and `verify`'s baseline diff.

**A green suite does not prove the change is correct.**
- **Smith, Barr, Le Goues & Brun (2015), _Is the Cure Worse Than the Disease?
  Overfitting in Automated Program Repair_, ESEC/FSE 2015, pp. 532–543,
  doi:10.1145/2786805.2786825** — [peer-reviewed]. The paper that named
  *overfitting* in automated repair: evaluations *"do not discriminate between
  correct patches and patches that overfit the available tests and break
  untested but desired functionality,"* and patch quality is *"proportional to
  the coverage of the test suite used during repair."* This is the peer-reviewed
  anchor for `test`/`verify`'s refusal to tune code to the letter of a test, and
  for `verify` exercising inputs the tests never covered.
- **Wang, Pradel & Liu (2025), _Are "Solved Issues" in SWE-bench Really Solved
  Correctly? An Empirical Study_, arXiv:2503.15223; accepted at ICSE 2026** —
  [peer-reviewed]. Direct measurement on agent-generated changes: *"29.6%"* of
  *plausible* patches induce behavior different from the reference fix, and
  *"7.8% of all patches … count as correct while failing the developer-written
  test suite."* A green run is not proof of a correct change — the exact reason
  `verify` re-checks each acceptance criterion by running it rather than
  trusting the `test` report.

**Define the criterion and work in small steps — not dogmatic test-first.**
- **Fucci, Erdogmus, Turhan, Oivo & Juristo (2017), _A Dissection of the
  Test-Driven Development Process: Does It Really Matter to Test-First or to
  Test-Last?_, IEEE TSE 43(7):597–614** — [peer-reviewed]. The measured benefit
  tracked the *granularity and uniformity* of the develop-test cycle, not the
  ordering: *"Sequencing, the order in which test and production code are
  written, had no important influence."* (Corroborated by the same group's
  independent ESEM 2016 multi-site replication.) So this pipeline commits to
  what the evidence supports — define observable acceptance criteria up front
  (AC-n in `explore`), work in a small traceable slice, test each criterion —
  without mandating test-before-code.

## The premise, and one shape deliberately *not* taken

- **Xia, Deng, Dunn & Zhang (2024), _Agentless: Demystifying LLM-based Software
  Engineering Agents_, arXiv:2407.01489; published at FSE 2025** —
  [peer-reviewed]. A fixed localize → repair → validate process — *"without
  letting the LLM decide future actions or operate with complex tools"* —
  reached 32.67% on SWE-bench Lite at ~$0.68/issue, competitive with and beating
  far more complex autonomous agents. The paper argues the point by
  demonstration rather than by an explicit claim that autonomy "compounds
  mistakes," but the takeaway — a bounded, structured pipeline beats
  unstructured many-turn autonomy — is this whole engine's premise, and
  `feature`'s explore → design → build → test → verify walk is it applied to
  feature work.
- **No fan-out — considered and rejected as the default.** The other pipelines
  fan out where the work is a set of *independent candidates* cheapest to pursue
  in parallel: `bugfix` over hypotheses, `research` over leads, `code-review`
  over lenses and findings. Feature work's dominant risks — fitting existing
  conventions and preserving existing behavior — are *serial and cumulative*,
  not a breadth of independent bets, so parallelism buys little. Generating N
  candidate designs in parallel and picking one was the tempting exception, but
  design-space breadth is more a greenfield/architecture concern than a
  brownfield one, so it isn't the default here. It remains an easy override:
  give `design` a `fan_out` file of candidate approaches when a specific feature
  genuinely warrants exploring several, the same way `research`'s pass counts are
  tunable per run. Recording the non-choice, as `refactor`'s report does, keeps
  the next maintainer from re-litigating it by accident.
