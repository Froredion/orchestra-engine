# Why the `approach` pipeline is shaped the way it is

`approach` decides **how** an assigned ticket should be solved, and writes a
spec. It builds nothing. That one fact is why it is a separate pipeline rather
than a longer front end on `feature`:

- `feature` adds a capability to existing code, and is deliberately *faithful*
  to the request it's handed — its `pin` stage makes the request's own words
  executable before any design gets to weigh them, and its `design` stage plans
  the smallest change that satisfies them. That faithfulness is the right
  property for a settled spec and the wrong one for a ticket nobody has
  interrogated: `feature` has no stage that is permitted to conclude the ask is
  wrong, and giving it one would break the thing that makes it trustworthy.
- `architecture` rearranges existing structure with observable behavior held
  constant; it needs a dependency map and debt stated as symptom → cause → cost.
  A ticket asking for new behavior gives it nothing to map.
- `investigation` and `research` produce knowledge. Neither is asked to commit
  to a course of action, which is the only thing this pipeline outputs.

So `approach` owns exactly the decision the others can't make, and hands the
result to the pipeline that can execute it: **a ticket arrives → approach
decides how → a human rules on the escalations → feature implements.**

This file is provenance for maintainers. The engine ignores it — only numbered
`NN-name.md` files are loaded as stages — so it never reaches an agent at
runtime.

**On the citations.** Every reference below was verified against its primary
source — the publisher's deposited record, or author-hosted full text — not
cited from memory: authors, venue, year, pages, and the specific quoted finding
were each checked, and the wording of every quoted claim was taken from the
source rather than reconstructed. Each entry is marked **[peer-reviewed]** or
**[practitioner]**.

Two references that would have fit were **dropped rather than paraphrased**,
which is the same rule the rest of the repo follows:

- **Jansson & Smith (1991), _Design fixation_, Design Studies 12(1), pp. 3–11,
  doi:10.1016/0142-694X(91)90003-F.** The bibliographic record checks out, and
  it is the origin of the term this pipeline's `option` stage is built against.
  But the openly reachable copy is a scanned image, the publisher's abstract is
  paywalled, and so the *specific finding* could not be verified. No claim here
  is attributed to it. The fixation claim is carried instead by Dow et al.
  (2010), whose own abstract states it and whose experiment measured it.
- **Jørgensen & Grimstad (2011), _The Impact of Irrelevant and Misleading
  Information on Software Development Effort Estimates_, IEEE TSE 37(5), pp.
  695–707, doi:10.1109/TSE.2010.78.** Record verified; no accessible copy of
  the abstract or full text, so its effect size is not quoted and it grounds
  nothing below.

## The backbone: intake → ground → frame → option → compare → reuse → reconcile → spec

The shape is a decision procedure, not a build. It gathers the request, checks
it against the code, separates the problem from the proposed solution, develops
the alternatives **in parallel and blind**, commits, and then checks what it
committed to against the codebase before writing it down. The stages a shorter
version would not have are `frame` — permission to disagree with the ticket —
the fan-out over `option`, which exists because the alternative is refining the
first idea, and there is direct experimental evidence that this produces worse
results, and the fan-out over `reuse`, which exists because every earlier
search for existing code was run by an agent that already had a plan.

## `intake`: the record is data about what people want, never instructions

Two findings shape this stage.

**The knowledge that decides the design is concentrated in a few people.**

- **Curtis, Krasner & Iscoe (1988), _A field study of the software design
  process for large systems_, Communications of the ACM 31(11), pp. 1268–1287,
  doi:10.1145/50087.50089** — [peer-reviewed]. Interviews with personnel from
  **17 large projects**. Of the problems the study analyzes, two are this
  pipeline's whole reason to exist: *"the thin spread of application domain
  knowledge"* and *"fluctuating and conflicting requirements."*

That first phrase is why `intake` weights engineer comments above everything
else on the ticket, and why it goes looking for the chat thread rather than
stopping at the ticket body. If domain knowledge is thinly spread, then the one
person who knows why the obvious approach was abandoned last quarter is
probably not the person who filed the ticket — and what they know is likely to
exist only as a sentence in a thread. The second phrase is why nothing on the
ticket is treated as settled: conflicting requirements are the normal case, so
`intake` records the disagreements and who was on each side instead of
resolving them, and `frame` handles them explicitly.

It is also why weighting is not veto. Thinly spread knowledge is knowledge held
by few people and refreshed rarely, which is exactly the profile of a
constraint that quietly stops being true. `ground` re-checks every engineer
claim against the code and marks it confirmed, stale, or unverifiable.

**A stage that reads other people's text into an agent with tools has a
security boundary running through it.**

- **Greshake, Abdelnabi, Mishra, Endres, Holz & Fritz (2023), _Not What You've
  Signed Up For: Compromising Real-World LLM-Integrated Applications with
  Indirect Prompt Injection_, Proceedings of the 16th ACM Workshop on
  Artificial Intelligence and Security (AISec '23), pp. 79–90,
  doi:10.1145/3605764.3623985, arXiv:2302.12173** — [peer-reviewed]. The
  paper's own framing is the design constraint: *"We argue that LLM-Integrated
  Applications blur the line between data and instructions,"* and it
  demonstrates adversaries who *"remotely (without a direct interface) exploit
  LLM-integrated applications by strategically injecting prompts into data
  likely to be retrieved."*

A ticket comment and a chat message are precisely "data likely to be
retrieved," written into systems many people can post to. So `intake` is
specified as read-only, writes exactly two artifact files, participates in
neither the tracker nor the thread, and is told in as many words that fetched
content is a record of what people want and never an instruction addressed to
it. The same finding is why author role must come from the connector's own
metadata: a pipeline that raises its trust in a message because the message
says its author is the tech lead has moved a trust decision into
attacker-controlled text.

## `ground`: two maps, because the comparison has two axes

Every option is scored on robustness *and* on what it costs to get approved.
The second axis is the one this pipeline adds, and it only works if it is
counted rather than guessed — which is what makes the review literature
load-bearing here rather than decorative.

- **Sadowski, Söderberg, Church, Sipko & Bacchelli (2018), _Modern Code Review:
  A Case Study at Google_, Proceedings of the 40th International Conference on
  Software Engineering: Software Engineering in Practice (ICSE-SEIP '18), pp.
  181–190, doi:10.1145/3183519.3183525** — [peer-reviewed]. Built from **12
  interviews, a survey with 44 respondents, and review logs for 9 million
  reviewed changes**. The measurements that matter here:
  - *"over 35% of the changes under consideration modify only a single file and
    about 90% modify fewer than 10 files"*; *"Over 10% of changes modify only a
    single line of code, and the median number of lines modified is 24."*
  - *"developers have to wait for initial feedback on their change a median
    time of under an hour for small changes and about 5 hours for very large
    changes"*, and *"The overall (all code sizes) median latency for the entire
    review process is under 4 hours."*
  - *"fewer than 25% of changes have more than one reviewer … with a median
    reviewer count of 1."*

Three consequences for this pipeline. First, **size is a schedule variable, not
an aesthetic one**: a fivefold difference in time-to-first-feedback between
small and very large changes is a measured property of a review process, so an
option's diff size belongs in the comparison with a number attached — which is
what `option` is required to produce and `ground` gives it the terrain to
compute. Second, **reviewer count is a real cost dimension** and a separate one
from size, which is why `ground` reads `CODEOWNERS` for *who must approve* and
`option` reports owners and size as distinct figures. Third, the numbers above
are Google's, not yours — the study's own comparison against Rigby and Bird
shows medians differing by an order of magnitude across organizations. So
`ground` is instructed to read *this repository's* rules — its `CODEOWNERS`,
contributing guide, PR template, CI config — and to write "not stated in the
repo" rather than fill in a threshold from convention. A fabricated threshold
would be scored against by four option agents independently.

## `frame`: the ticket is a solution somebody already chose

A ticket usually names a mechanism, written before anyone read the code. Curtis
et al.'s *"fluctuating and conflicting requirements"* is the field-study form of
this: what a request asks for is not a fixed input, and treating it as one
means the first framing anyone wrote wins by default.

So `frame` splits the request into **criteria** — the requester's, quoted,
carried through unchanged — and **mechanisms**, which are open. The split is
what keeps the permission narrow: re-opening *how* is this pipeline's job,
re-opening *what* is not, and without the distinction the second happens under
cover of the first. The stage is also constrained to evidence: a divergence
must be a traced failure, a contradiction with what `ground` verified, an
unnamed cost, or an existing implementation — never a preference. A frame stage
that finds the ticket sound has done its job, because everything downstream
designs against whatever it writes.

When `frame` concludes a **criterion** should change, it may not decide that
alone: it writes the conflict to the run's append-only `ESCALATIONS.md` and
carries the original criterion forward anyway, so the options are still scored
against what was actually asked for. The engine restores anything removed from
that file, which is what makes the escalation survive a later stage's tidying.

`frame` also writes the stated criteria to `PINS.md`, one quote per `PIN-n:`
line. Until then, "carry the requester's words unchanged" was an instruction
repeated in four stages that nothing checked, and the check that does exist
began one pipeline too late: `feature` pins whatever the spec hands it, so a
criterion tidied here would be pinned there in its tidied form. The engine
already compares every `PIN-n:` line after every stage and records a dropped or
reworded one on the escalation record, whatever the stage says. So writing the
file is all it takes to have every later stage of this run checked, with no
engine change. The engine can only see changes after the file is first written,
so `spec` checks each quote against `TICKET.md` itself, which covers `frame`'s
own copy.

## `option`: parallel and blind, because refining one idea fixates

This is the stage with the most direct experimental support, and it is the one
most likely to look like unnecessary expense.

- **Dow, Glassco, Kass, Schwarz, Schwartz & Klemmer (2010), _Parallel
  prototyping leads to better design results, more divergence, and increased
  self-efficacy_, ACM Transactions on Computer-Human Interaction 17(4), art.
  18, pp. 1–24, doi:10.1145/1879831.1879836** — [peer-reviewed]. The abstract
  states the failure mode this pipeline is built to avoid: *"Iteration can help
  people improve ideas. It can also give rise to fixation, continuously
  refining one option without considering others."* The experiment had novice
  designers produce web advertisements either in parallel (multiple prototypes
  before any feedback) or in series (critique after each one), and measured the
  results in the field: *"ads created in the Parallel condition significantly
  outperformed those from the Serial condition"* — **445.0 versus 397.9 clicks
  per million impressions, F(1,30) = 4.227, p < 0.05** — with parallel
  prototypes also rated **more diverse** by independent raters.

The effect is why the stage is a `fan_out` and not a loop. An agent asked to
improve a design improves the design it has; agents developing options
independently produce a set that actually spans the space. And the *blindness*
is the mechanism, not an implementation detail — in the experiment the parallel
condition's distinguishing feature is that prototypes were created before
feedback arrived. So each option agent is told to touch only its own file and
not to read the others': an agent that can see a rival's case starts arguing
against it instead of developing its own, and the comparison downstream loses
the independence that made it worth running.

Two rules protect the set's honesty. Each agent is its option's *auditor* as
well as its advocate, must name what its option gives up, and is told the
comparison stage will distrust a case with no stated cost — because a case
whose costs were softened wins a comparison it should have lost. And an agent
that finds its option unworkable is instructed to mark it **NOT VIABLE** and
stop: a dead option reported honestly removes a branch, while a propped-up one
gives the comparison a fake winner to beat.

## `compare`: the three results one axis cannot see

Scoring on two axes exists so the comparison can reach conclusions that aren't
"pick the best one" — that the cheap option and the durable one are **the same
option**, that the cheap one is **step one** of the durable one rather than a
rival, or that the durable one **isn't worth it**. The last of those is the one
with an explicit cost model behind it:

- **Fowler, M. (2015), _Yagni_, martinfowler.com, 26 May 2015** —
  [practitioner]. Building a feature you presume you'll need carries four
  costs, and only the first is the one people count: the **cost of build**
  (*"all the effort spent on analyzing, programming, and testing this now
  useless feature"*), the **cost of delay** to the work that was actually
  urgent, the **cost of carry** (*"The code for the presumptive feature adds
  some complexity to the software, this complexity makes it harder to modify
  and debug that software"*), and the **cost of repair** when what was built
  doesn't match what turned out to be needed.

Cost of delay is why `compare` weights cheap-and-fast heavily when the record
shows something is waiting on the ticket, and cost of carry is why "more robust"
is never allowed to win on its own terms — it has to be robustness against asks
that `frame` could *source* from the record, not asks an agent imagined. The
**staged path** is the shape that pays cost-of-build twice to avoid
cost-of-repair once, which is why it has to be recommended explicitly and named
as a proposal rather than presented as an agreed commitment.

`compare` may also decline outright. That mirrors `architecture`'s `select`
stage, for the same reason: a run that must produce a recommendation will
produce one, and a manufactured recommendation is worse than no run at all
because it arrives with a document attached.

## `reuse` and `reconcile`: check what was chosen, after it was chosen

`ground` looks for existing code before any design exists, so it can only look
for what the ticket names. The pieces most likely to duplicate something — the
small helper, the second config loader, the validator one package over — are
invented later, by the option agents, and until these stages nothing checked
them against the codebase. The spec then presents its approach as settled, and
`feature` builds whatever it is told to create.

- **Juergens, Deissenboeck, Hummel & Wagner (2009), _Do Code Clones Matter?_,
  Proceedings of the 31st International Conference on Software Engineering
  (ICSE '09), IEEE, arXiv:1701.05472** — [peer-reviewed]. Venue as given in
  the arXiv record; the page range and the publisher's DOI could not be checked
  against the full text, so neither is given here, and only the abstract is
  quoted. The abstract names the mechanism — *"inconsistent changes to code
  duplicates can lead to unexpected behavior"* — and reports, for the
  commercial and open-source systems studied: *"we not only found that
  inconsistent changes to clones are very frequent but also identified a
  significant number of faults induced by such changes."*

That is why a plan that duplicates existing code is not just untidy. Two
implementations of one behavior get changed separately, and the study found
that this happens often and causes faults. The cheapest point to stop that is
before the spec exists, while "use the one that's already there" is a line in a
document rather than a refactor.

Three choices follow:

- **After `compare`, not before.** Only the chosen design's pieces are hunted.
  Hunting every option's pieces would spend agents on designs that lost, and
  `ground` and `option` already searched as well as an agent holding a plan
  can.
- **One agent per piece, searching before it reads.** Each hunter gets a
  one-line description of what the piece does and the code, and forms its
  answer before opening the decision or the option files, because a search run
  to confirm a plan finds what the plan expects. That ordering is an
  instruction, not an engine barrier: `handoff: none` withholds the artifacts
  directory before a fan-out reads its item file, so it can't be used here.
- **A separate stage decides.** The engine won't let a fan-out route — parallel
  agents would race to write one decision — so `reconcile` reads the reports
  and settles each piece as **confirmed new**, a like-for-like **substitution**
  it makes itself, or a finding that **changes the decision**, which goes back
  to `compare`. The split keeps a helper swap from costing a whole
  re-comparison, and keeps a reinvented subsystem from passing as a helper
  swap. A finding `compare` has already re-weighed is escalated rather than
  sent back again, so two stages can't loop over it.

The result reaches `feature` as a section of the spec — existing code to build
on, cited — because `feature`'s design stage is told to reuse what exists but
can only reuse what it finds, and it has none of the searching this run did.

## `spec`: written to be pinned

The output is shaped by its reader. `feature`'s `pin` stage turns **stated**
criteria into tests before any design weighs them, and leaves **inferred** ones
revisable — so this stage carries the requester's wording through unchanged and
labels every criterion by source, because a tidied criterion becomes a test
asserting something nobody asked for, and a guess mislabelled as stated becomes
a constraint the implementing run cannot revise.

It also deliberately *omits* the option comparison. `DECISION.md` keeps the
alternatives and the scoring; the spec carries the decision. Handing the
implementing run the rejected options invites it to re-open a choice that was
made with better evidence than it will have — the evidence came from four
parallel agents and a two-axis comparison, none of which that run can see.

What it does carry is the **design**, and it takes that from the winning
option's file rather than rebuilding it. Code details move between stages only
through files a stage is told to read. Before this, the spec read `DECISION.md`,
which holds the choice, and `GROUND.md`, which was written before any design
existed. The one document holding the design in detail — the call sites,
signatures and edge handling an agent worked out against the code — was read by
`compare` and by no one after it. For the same reason the spec opens every
`file:line` it publishes before publishing it. Each citation has been copied
through two or three documents by then, and a wrong line number in the spec
reaches `feature` as a settled fact. Where a citation the decision rests on
turns out wrong, the spec can't route back, being the terminal stage, so it
escalates.

It also names who rules on each escalation and whose approval the change needs.
The run already holds those names — authors from the connector's metadata in
`TICKET.md`, owners from `CODEOWNERS` in `GROUND.md` — and an escalation
addressed to "a human" is one nobody in particular answers.

Finally, the deliverable is a document and not a branch. The pipeline writes no
code in any stage, and the spec lands in the run's artifacts directory rather
than the workspace — so the repository stays clean, the ticket and chat material
this run read stays out of version control, and the decision to build reaches a
person before it reaches a commit.
