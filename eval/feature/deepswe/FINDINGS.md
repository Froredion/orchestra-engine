# Findings: the `feature` pipeline on `prometheus-transactional-reload-status`

What the staged Orchestra Engine scores on one DeepSWE task, under what
configuration, and what the score does and does not support.

Setup, flags, and how to reproduce: [`README.md`](README.md).

## Configuration under test

| | |
|---|---|
| Pipeline | `feature` — explore → design → build → test → reread → verify → integrate |
| Model | `claude-opus-5` |
| Thinking | `{"type": "adaptive", "display": "summarized"}` |
| Effort | CLI default (`xhigh`) — see below |
| Permissions | `bypassPermissions` + `dangerously_skip_permissions` |
| Budget | `--agent-timeout-multiplier 4`, `--ak agent_timeout_sec=21000` |
| Host | Windows 11, Docker on WSL2, native amd64 |

Thinking type and effort are independent settings, and the engine sets only the
first. [`session.py`](../../../orchestra_engine/engine/session.py) passes
`thinking={"type": "adaptive", "display": "summarized"}` and leaves `effort`
unset. The SDK appends `--effort` to the CLI command only when the option is not
`None`, so the flag never reaches the CLI and Claude Code's own default applies:
**`xhigh`**. Note this differs from the API-level default for an unset effort,
which is `high`.

`reread` is the fifth stage and runs under `handoff: none` — it receives the
request and the workspace, and neither the earlier stages' reports nor pointers
to their sessions. Its job is to derive the requirements a second time,
independently, so `verify` has something to check the first reading against.

## Results

Harness validation, both without an API key:

| Arm | Result | Establishes |
|---|---|---|
| `-a nop` | `reward 0`, f2p **0/15**, p2p **82/82** | The verifier runs and detects failure |
| `-a oracle` | `reward 1`, f2p **15/15**, p2p **82/82** | The task is solvable and the grader detects success |

Staged arm, `OrchestraEngineAgent`, four trials at two concurrent:

| Trial | Sessions | Engine time | Cost | Patch | Result |
|---|---|---|---|---|---|
| 1 | 7 | 1h 34m 37s | $29.72 | 92 KB | `reward 1`, f2p **15/15**, p2p **82/82**, `partial 1.0` |
| 2 | 7 | 1h 41m 34s | $38.90 | 82 KB | `reward 1`, f2p **15/15**, p2p **82/82**, `partial 1.0` |
| 3 | 12 | 2h 31m 30s | $58.24 | 119 KB | `reward 1`, f2p **15/15**, p2p **82/82**, `partial 1.0` |
| 4 | 15 | 3h 16m 37s | $76.15 | 112 KB | `reward 1`, f2p **15/15**, p2p **82/82**, `partial 1.0` |

**Four of four**, every fail-to-pass test green and nothing regressed. The batch
took 9h 14m of wall-clock and **$203**.

### Hops are the cost variable

Seven sessions is a straight run. Trials 3 and 4 opened twelve and fifteen —
`verify` routed back, and each hop re-runs the stages downstream of its target.
That is the entire spread in the table: trial 4 cost 2.6× trial 1 for the same
score, and took twice as long.

`--ak agent_timeout_sec=21000` bounds this at 5h50m, and trial 4 came within
2.5 hours of it. A trial that hops late can be killed mid-fix and score worse
than one that never looked, so a hop cap (`--ak max_hops=N`) is worth setting on
any run where a timeout would waste the whole trial.

### What the stage documents show

The score is the weaker half of the result. All four trials produced
`.verify-independent-reading.md`, and all four resolved the request's
underspecified points the same way — landing on the option that still works if
the consumer turns out to expect the other one.

The reread documents reason in those terms explicitly, e.g. *"not equally
survivable — which direction survives being wrong"*, and apply it past the
obvious field: timestamp format, a boolean, membership of an enum. Trial 2's
design elected its value type citing an existing helper in the codebase
(`openapi_helpers.go:69`) rather than picking on expressiveness.

This is legible with the verifier switched off, which is what makes it worth
more than the four green ticks: it would read the same whether or not the tests
happened to agree.

## What is not measured

**The baseline arm has not been run here.** The single-session `-a claude-code`
run has not been executed on this hardware, under this budget, by this harness.

The closest external comparator is the DeepSWE board's own heatmap, which
records **Claude Code on Opus 5 at `xhigh` effort passing 1 of 4 trials (25%)**
on this task — same model, same agent, and the same effort level this pipeline
runs at by default. That is a far better reference point than the board's
all-models figure (2 passes / 116 trials), and it is the number worth beating.

**But the budgets are not comparable, and the gap is not small.** Every trial
here ran with `--agent-timeout-multiplier 4`. The task declares
`agent.timeout_sec = 5400` — 1h 30m — and **all four trials exceeded that on
engine time alone**: 1h 35m, 1h 42m, 2h 32m, 3h 17m. Under the task's own budget
the two fast trials would have been cut just past the line and the two that
hopped would have been cut in half. A 4/4 that depends on four times the
declared budget is not the same result as 4/4 within it, and none of it is
comparable to a board figure produced under different limits.

So the honest reading is: the staged arm clears a task where a single Opus 5
session at the same effort clears it a quarter of the time, using up to four
times the budget, on unstated-but-different hardware. Whether the structure or
the budget is doing the work is exactly the open question, and one run settles
it — `-a claude-code` at `--agent-timeout-multiplier 4`, same host, same day.

Measuring the baseline costs about a quarter of what the staged arm does (~30
min a trial):

```sh
pier run -p deep-swe/tasks/prometheus-transactional-reload-status \
  -a claude-code -m anthropic/claude-opus-5 \
  --agent-timeout-multiplier 4 \
  --ak reasoning_effort=max \
  --ae CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  -k 4 -n 2 -o jobs/baseline
```

Pier's stock agent exposes `reasoning_effort` (CLI `--effort`, env fallback
`CLAUDE_CODE_EFFORT_LEVEL`), so a specific effort level can be measured rather
than inferred. `--agent-timeout-multiplier 4` goes on both arms, or the
experiment measures the budget instead of the structure.

## Limits on the number

**The rate is iterated-on.** The `feature` prompts were revised against this
task's own output while it was being measured. That is the ordinary reason a
measurement you tuned against comes out optimistic, and it applies here in full:
quote this as *4/4 on the task the rules were written for*, not as a pass rate.

The mechanism evidence sits on firmer ground than the score, because it is
readable in the stage documents rather than inferred from the grade. But
`grep -rniE 'int64|float|integer|milli|timings|prometheus|reloader'
orchestra_engine/pipelines/feature/` returning nothing only establishes that the
rules carry no task-specific content — "written without naming the task" and
"works on another task" are different claims, and only the second one matters.

**One task is one task.** A perfect score here says the pipeline reliably solves
`prometheus-transactional-reload-status`. It is not a DeepSWE number and does
not belong beside one.

**Trials are not commit-pinned.** `engine_spec` defaults to the working tree, so
reproducing a trial means knowing what was on disk, not just the branch. Record
the commit — or pass a pip spec — for anything meant to be repeated.

## Next

1. **The baseline arm at matched budget**, per the command above. The board's
   25% for the same model and effort makes this the highest-value hour left:
   run it at `--agent-timeout-multiplier 4` and the budget confound disappears,
   leaving a genuine two-arm comparison on one host.
2. **An un-iterated-on task**, where the rules would be tested rather than
   confirmed. [`FINDINGS-meriyah.md`](FINDINGS-meriyah.md) is the first of
   these, and it ties with its own baseline: 45/49 both arms, the same four
   tests failing, at five times the cost. It also records the run where the
   pipeline built a requirement correctly and then argued itself out of it. A
   third task is owed, because diagnosing that fed back into the prompts — and
   it should be chosen for headroom, since a task both arms fail identically
   cannot separate them.
3. **An effort sweep.** Everything here ran at the CLI default. Whether the
   staged structure still wins at `medium`, where each stage is cheaper,
   separates "staging helps" from "more tokens help".
