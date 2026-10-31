# Benchmarking the `feature` pipeline on DeepSWE

[`pier_agent.py`](pier_agent.py) makes the staged Orchestra Engine an agent that
[Pier](https://github.com/datacurve-ai/pier) can run. That way the staged arm
and a plain single-session agent are measured by the same verifier, in the same
sandbox, on the same task text. Grade either arm by hand instead and you
reintroduce the confounds DeepSWE's isolated verifier exists to remove.

A single-task run is a mechanism illustration, not a result.

## Setup

```sh
git clone https://github.com/datacurve-ai/deep-swe
uv tool install git+https://github.com/datacurve-ai/pier
export ANTHROPIC_API_KEY=sk-ant-...
```

### Auth: API key, or a subscription token for demos only

The adapter forwards `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_BASE_URL` and `CLAUDE_CODE_OAUTH_TOKEN`, so a Claude Code
subscription works: `claude setup-token`, then `--ae CLAUDE_CODE_OAUTH_TOKEN=…`.

Use it for a demo run, not for a measured one. Subscription rate limits are
sized for interactive use, and the staged arm opens six sessions per trial
against the baseline's one, so it is more exposed to throttling. If arm 2 gets
throttled where arm 1 does not, the experiment records "staging is worse" when
it actually measured a rate limit, and the confound sits on the variable under
test. Subscription auth also yields no `total_cost_usd`, which §6 wants per arm.

Untested: the agent container is `no-network` plus an allowlist of
`api.anthropic.com`. A bearer token against that host should be fine. If OAuth
needs a refresh round-trip elsewhere it will fail closed, visible as an auth
error in `orchestra-engine-stdout.log`.

Pier resolves custom agents by import path, so this repo needs to be importable
and the engine needs to be pip-installable inside the container. The adapter
defaults to this checkout (`DEFAULT_ENGINE_SPEC = _repo_root()`) and uploads
`orchestra_engine/` with `shutil.copytree`, so whatever is in your working tree is
what runs, uncommitted edits included, and nothing needs pushing first. Point
`engine_spec` at a pip spec (`git+https://…@branch`) to test something else.

That cuts both ways: an experiment is only reproducible if you record the
commit, because the tree can differ from any commit at all.

**And the upload happens per trial, in `setup()`, not once per job.** With
`-k 4 -n 2` the second pair stages hours after the first, so a pipeline edit
made while the job runs reaches trials 3 and 4 and not trials 1 and 2 — one
job, two arms, no error and nothing in the output saying so. Treat the working
tree as frozen from launch until the last trial grades. If you need to change
the pipeline mid-flight, kill the job and restart it, and remember that a
partially-cancelled job's summary row is diluted by the trials you killed.

## The two arms

Both commands run from a scratch directory holding the `deep-swe` clone, with
`PYTHONPATH` pointing at this repo so Pier can import the adapter. Only those
two paths are machine-specific:

```sh
cd /path/to/scratch          # contains deep-swe/
export PYTHONPATH=/path/to/orchestra-engine
export CLAUDE_CODE_OAUTH_TOKEN=...   # or ANTHROPIC_API_KEY, see Auth above
```

Baseline, one long session:

```sh
pier run -p deep-swe/tasks/prometheus-transactional-reload-status \
  -a claude-code -m anthropic/claude-opus-5 \
  --agent-timeout-multiplier 4 \
  --ae CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  -k 4 -n 1 -o jobs/baseline
```

Orchestra Engine, staged. This is the exact invocation the recorded results below
were produced with:

```sh
pier run -p deep-swe/tasks/prometheus-transactional-reload-status \
  --agent-import-path eval.feature.deepswe.pier_agent:OrchestraEngineAgent \
  -m anthropic/claude-opus-5 \
  --agent-timeout-multiplier 4 \
  --ak pipeline=feature \
  --ak permission_mode=bypassPermissions \
  --ak dangerously_skip_permissions=true \
  --ak agent_timeout_sec=21000 \
  --ae CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  -k 4 -n 1 -o jobs/orchestra-engine
```

Four flags there are load-bearing, and all four were learned the hard way:

- `--agent-timeout-multiplier 4`. The staged arm needs 65–90 min against the
  baseline's 26–41, and a hop pushes it past three hours. At the task's own 1x
  budget two trials were killed mid-run at 5200s. Set it on both arms, or you
  are measuring the budget rather than the structure.
- `--ak agent_timeout_sec=21000`. A little under the task timeout ×4 (21600).
  See the `agent_timeout_sec` section below. Without it a timed-out trial loses
  the autocommit and scores a flat zero instead of partial credit.
- `-n 1`, sequential. Each container asks for 2 CPUs, 8 GB and grows its
  writable layer by 12–15 GB during a run. Three concurrently exhausts a 59 GB
  Docker VM disk, and a trial at 99% disk crawls. Sequential also self-cleans,
  since the task config sets `delete: true`.
- `-k 4`. See below.

Prune Docker between sessions. Per-trial images are ~7 GB each and are not
reclaimed automatically; stale ones squeezed a live trial down to 625 MB free
mid-run. `docker image prune -a -f` is safe while a trial runs, because the
running container's image is in use and protected.

### Run more than once: `-k` is not optional here

On the DeepSWE board this task is 2 passes out of 116 trials, and the two models
that solved it each did so 1 time in 4. A single run of either arm is a coin
flip, and a single green tick proves nothing. Use `-k 4` at minimum. The
comparison is between rates, not between two anecdotes.

### The permission decision is yours to make

`--ak permission_mode=bypassPermissions --ak dangerously_skip_permissions=true`
auto-approves every tool call the engine makes. The adapter refuses to start if
you ask for `bypassPermissions` without the explicit skip, rather than hanging
for 90 minutes on a confirmation prompt nothing in a container can answer.

That friction is deliberate. Auto-approval is defensible here, since the
container is disposable, single-task, and network-restricted to the API, but it
is a choice you make and not a default the adapter makes for you.
`permission_mode=auto` is the alternative; expect occasional gates, which in an
unattended run means a stalled trial.

## What the adapter handles for you

- Committing. Grading reads `git diff <base_commit>..HEAD`, so uncommitted work
  scores zero however good it is. The adapter commits residual changes after the
  run, including when the pipeline raised or timed out, because partial
  committed work is a datapoint and silence is not.

  It commits with `git add -A`, which is load-bearing rather than incidental: a
  stage forbidden from committing can finish a feature whose central new file is
  untracked, and a `git diff`-based delivery of that tree does not compile. If
  your pipeline's last stage commits for itself, pass `--ak autocommit=false`;
  if it does not, the harness must not lose untracked files.
- Keeping the diff clean. Stage documents go to `--runs-dir` (`/logs/loop-run`),
  never the workspace, so they cannot pollute the graded patch.
  `.git/info/exclude` is belt-and-braces on top of that.
- Not perturbing the task. The engine installs into its own virtualenv. Many
  DeepSWE tasks are Python, and installing the harness into the system
  interpreter could break the task's own dependency resolution and produce
  failures that look like the agent's fault.
- Trajectory and cost. Each stage is its own Claude session, so a run produces
  one session directory per stage. Pier's stock `ClaudeCode` agent gives up
  whenever it finds more than one and silently reports no trajectory and no
  metrics, which would cost you token spend and step counts, two of the
  evaluation's primary measures. The adapter merges them, renumbering step IDs
  so the result is a valid ATIF document.

## Reading the result

Per-trial output lands under `-o` (`jobs/…`), including the graded outcome, the
merged `trajectory.json`, and `orchestra-engine-stdout.log` (the engine's own stage
progress).

For the single-task demo the number to write down is not the binary reward. Use
`reward.json`, which carries partial credit alongside it:

| Field | What it tells you |
|---|---|
| `reward` | Binary 0/1 — 1 iff every f2p passes and no p2p regresses |
| `f2p_passed / f2p_total` | How much of the spec landed (15 tests here) |
| `p2p_passed / p2p_total` | Whether the arm broke anything existing |
| `partial` | Combined fraction |
| `apply_failed` | The patch did not apply — grade is meaningless, investigate |

Two ways that summary misleads, both of which have cost time here:

- **`partial` is only readable when f2p and p2p are the same order of
  magnitude.** On a task with 49 f2p tests and 51,469 p2p tests, a `nop` that
  changes nothing scores `partial 0.999`. Check the ratio before quoting the
  fraction; if p2p dominates, quote `f2p_passed/f2p_total` and the p2p count.
- **Cancelled and errored trials are averaged in as zeros**, because the summary
  row divides by trials launched. A job where two of four were killed reports
  roughly half the real rate. The tell is a non-integral `f2p_total` — a test
  count cannot be fractional. Read `reward_stats.f2p_passed` for the per-trial
  breakdown and `exception_stats` for what was lost.

`f2p` is the interesting one. This task's instruction states nine requirements,
materialized as 15 fail-to-pass tests, so the fraction is requirement coverage
with no rubric needed. Pair it with the trajectory to ask where the arms
diverged: did `02-design` enumerate all nine, did `05-reread` derive one the
scope had bent, did `06-verify` catch a drop the single session shipped? A
binary flip at n=1 is noise; a coverage gap at a named stage is a mechanism.

### How grading sees a staged run

It doesn't. The verifier runs in a separate container against
`git diff <base>..HEAD`, so an agent that internally used six sessions and one
that used a single session hand over identical artifacts. Session structure is
invisible to the score. It shows up only in the trajectory record, which is why
this adapter merges per-stage sessions.

Two things follow. The whole pipeline shares the task's single
`agent.timeout_sec` (5400s here), the same budget the baseline's one session
gets. That is the parity you want, but it averages ~15 min per stage and a slow
early stage can starve a later one. And because only the diff of `/app` crosses
into the verifier, the engine's own virtualenv never reaches grading, so the
harness cannot contaminate the score.

## Useful kwargs

| `--ak` | Default | Why change it |
|---|---|---|
| `pipeline` | `feature` | `bugfix` for a bug-shaped task; DeepSWE is ~95% feature specs. `approach+feature` chains two pipelines, with approach's SPEC handed to feature ([its arm](../../approach-feature/deepswe/README.md)). It is also what this directory is named after — a run under another pipeline is a different arm, so record it in a sibling `eval/<pipeline>/deepswe/`, not here |
| `engine_spec` | this checkout (working tree, uncommitted included) | Test a branch or a fork via a pip spec |
| `permission_mode` | `auto` | See above |
| `dangerously_skip_permissions` | `false` | Required for `bypassPermissions` |
| `max_turns` / `max_hops` | engine defaults | Match the budget across arms, or you measure budget rather than structure |
| `autocommit` | `true` | Off only if a pipeline is committing for itself |
| `agent_timeout_sec` | unset | Set it. See below |
| `repo_dir` | `/app` | Where DeepSWE checks the repo out |

### Set `agent_timeout_sec` a little under the task's own timeout

```sh
--ak agent_timeout_sec=5200      # task declares agent.timeout_sec = 5400
```

Pier enforces its agent timeout with `asyncio.wait_for`, which cancels the agent
coroutine, and a cancelled coroutine cannot reliably `await` anything more,
including the autocommit that makes partial work gradeable. Hit that path in a
90-minute trial and you lose everything the pipeline built but had not
committed, scored as a flat zero.

Passing `agent_timeout_sec` wraps the engine in a container-side `timeout`, so
it ends as an ordinary non-zero exit instead. The command returns, the commit
runs, and Pier grades the partial work, since it treats a non-zero agent exit as
a gradeable outcome rather than a crash. Leave ~200s of headroom for the commit.

## Tests

```sh
.venv/bin/pytest tests/test_pier_agent.py
```

They skip unless Pier is installed, since it is a benchmarking dependency and
not an engine one. To run them, install Pier and this engine into one
environment.

## Checking your setup before you spend anything

Three cheap runs, in this order. The first two need no API key, and together
they tell you whether a bad score later is the pipeline's fault or yours:

| Run | Expected | Proves |
|---|---|---|
| `-a nop` | `reward 0`, f2p **0/15**, p2p **82/82** | Env builds, collect hook emits `model.patch`, separate verifier grades. The harness detects failure. |
| `-a oracle` | `reward 1`, f2p **15/15**, p2p **82/82**, `partial 1.0` | The grader detects *success* and the task is solvable (36.9 KB reference patch). |
| `OrchestraEngineAgent` with a deliberately bad key | Installs, runs, autocommits, exits non-zero | Upload → venv → `pip install` → `orchestra_engine --list` all succeed in-container; the `finally` autocommit runs on the failure path; Pier records `NonZeroAgentExitCodeError` as gradeable. |

A `nop` that grades is the single most useful check here: it exercises the whole
path except the agent, in about five minutes, and it is what catches an
environment fault that would otherwise surface hours later — see the CRLF gotcha
below for the failure mode it saves you from.

## Results

This file covers how to run the thing. What came out lives per task, because a
score only means anything next to the task that produced it:

| Task | Findings |
|---|---|
| `prometheus-transactional-reload-status` | [`FINDINGS.md`](FINDINGS.md) — the task the prompts were revised against |
| `meriyah-explicit-resource-declarations` | [`FINDINGS-meriyah.md`](FINDINGS-meriyah.md) — a task nobody here had read the tests for |

The chained `approach+feature` arm uses this same adapter, but its results are
in [`../../approach-feature/deepswe/`](../../approach-feature/deepswe/).

## Gotchas

### The Windows line-endings gotcha

If you clone `deep-swe` on Windows with `core.autocrlf=true` (git's default
there), the task's `tests/test.sh` lands with CRLF endings, its shebang becomes
`#!/bin/bash\r`, and the verifier container fails with:

    bash: line 1: /tests/test.sh: cannot execute: required file not found

The file is present. What bash cannot find is the interpreter `/bin/bash\r`. It
surfaces as `RewardFileNotFoundError` about ten seconds into verification, after
the agent phase has already run to completion, so it costs a full trial (two
hours and ~$34 for the staged arm) to learn. Clone with:

```sh
git clone https://github.com/datacurve-ai/deep-swe
git -C deep-swe config core.autocrlf false
git -C deep-swe config core.eol lf
rm -rf deep-swe/tasks && git -C deep-swe checkout -- .
```

Verify with `head -c 12 deep-swe/tasks/<task>/tests/test.sh | od -c`. You want
`#!/bin/bash \n`, not `\r \n`. The cheap end-to-end check is `-a nop`: no API
key, ~5 min, and it should return `reward 0`, f2p 0/15, p2p 82/82. A `nop` that
grades is a verifier that works.

### Re-grading a patch you already have

A trial that dies in verification still leaves `artifacts/model.patch`, and the
agent phase that produced it is not implicated — a verifier that fails to start
says nothing about the work. Rather than hand-grading it, which reintroduces
exactly the confounds this setup exists to remove, replay it through Pier's own
verifier with a stub agent that applies the patch and commits, so the harness
produces `reward.json` by its normal path:

```sh
pier run -p deep-swe/tasks/<task> \
  --agent-import-path eval.feature.deepswe.replay_agent:PatchReplayAgent \
  --ak patch_path=jobs/<job>/<trial>/artifacts/model.patch \
  -k 1 -n 1 -o jobs/replay
```

Costs ~5 min and no API spend. Apply with `git apply --binary` (the collect hook
writes the patch with `git diff --binary`) and commit with `git add -A`. An
applied-but-uncommitted patch scores zero, and so does one that drops the run's
untracked files.

What replay cannot recover is the trajectory. A trial whose verification never
started records `RewardFileNotFoundError`, so its `agent_result` — token counts,
step counts — is null and stays null. The reward comes back; the metrics do not.

### The root-user gotcha

Task containers run the agent as root, and Claude Code refuses
`--dangerously-skip-permissions` under root, so the pipeline dies in stage 1
with an empty patch. The adapter sets `IS_SANDBOX=1` when (and only when)
`dangerously_skip_permissions` is on. Verified in-container: without it the CLI
exits on the root guard; with it, it proceeds.

Pier's stock `claude-code` agent sets `IS_SANDBOX=1` unconditionally and runs
`--permission-mode=bypassPermissions` (`pier/agents/installed/claude_code.py`),
so the baseline arm clears the same guard on its own and both arms run under the
same permission posture. Worth checking, because a baseline that dies on the
root guard would hand the staged arm a win it did not earn.

Verification takes ~2–4 min, so the cost is entirely in the agent phase. Note
that the base state fails with `Unknown option for --enable-feature
option=transactional-reload-config`, so the f2p tests genuinely fail before a
fix.

The task image is 6.71 GB; the first run pulls it, later runs reuse it.

## Known gaps

- No baseline arm run here. The single-session `-a claude-code` run is the other
  half of the comparison and has not been executed on this host. The DeepSWE
  board's heatmap gives Claude Code on Opus 5 at `xhigh` effort 1 of 4 trials on
  this task, which is the right comparator to aim at — but it was produced under
  the task's own budget, and every trial recorded here ran at four times that.
  Run the baseline at a matched `--agent-timeout-multiplier` before quoting the
  two side by side.
- One task, and a number that is iterated-on. The `feature` prompts were revised
  against this task's own output while it was being measured, so a rate quoted
  from it is optimistic in the plain way any measurement you tuned against is
  optimistic. [`FINDINGS.md`](FINDINGS.md) states what the current number does
  and does not support. [`FINDINGS-meriyah.md`](FINDINGS-meriyah.md) is the
  task nobody here had read the tests for, which is what that gap called for —
  though diagnosing its failures spent the same clean-task status, so a third
  one is now owed.

  This is not a contamination argument. The prompts are English you can read, so
  whether an edit encodes a general rule or this task's answer is settled by
  reading the diff — `grep -rniE 'prometheus|reloader|_ms|int64|milli'
  orchestra_engine/pipelines/feature/` returns nothing. But "written without naming
  the task" and "works on another task" are different claims, and a benchmark
  only ever demonstrates the second one.

  Comparing runs' own `DESIGN.md`s against each other is how you tell which you
  have: it shows what each stage searched and what it concluded, with the
  verifier switched off. A run reporting that no precedent exists, next to one
  that found the codebase's existing helper, is a difference in search radius —
  visible in the stage documents, and readable without reference to any
  particular task's tests.
- `prometheus-transactional-reload-status` reports `language = "typescript"` in
  its own `task.toml` for a Go repository. Confirmed in the task data itself,
  not just the website. Harmless to the run, but do not quote DeepSWE language
  statistics without checking.
