# Orchestra Engine

Staged agent orchestration engine, at `{repo_root}`. Cross-platform (macOS/Linux/Windows). **Setup is one command on every OS: `python bootstrap.py`** — it creates `.venv` with whatever Python ran it and installs the engine editable; re-running is safe. **Launcher:** `./orchestra` on macOS/Linux, `orchestra` (or `.\orchestra`) on Windows — both just exec the module through the repo's venv, so `<venv-python> -m orchestra_engine` also works directly (`.venv/bin/python` on macOS/Linux, `.venv\Scripts\python.exe` on Windows). Run it against any repo via `--workspace`.

A **pipeline** is just a folder of numbered prompt files (`01-plan.md`, `02-build.md`, …). The engine doesn't know what they mean — it runs them in filename order. Each stage is its own Claude Agent SDK session; when one finishes, its summary and session ID are handed to the next, so nothing is shared but that handoff and the files on disk. Frontmatter sets a stage's shape: `repeat: N`, `fan_out: FILE` (one parallel agent per line), `skip_if: FILE contains TEXT`, `decide: STAGE,...`. `./orchestra --list` describes the built-ins (it's the source of truth — the set grows); `UNDERSTANDING.md` explains the execution model from zero context.

## This block is guidance for *you*, and never reaches the stages

Stage agents do not read `~/.claude/CLAUDE.md`. The engine pins the settings layers it starts each session with to `project` only (`engine/session.py: DEFAULT_SETTING_SOURCES`), which resolves against the workspace — so a stage sees the target repo's own conventions and nothing from the operator's global memory.

That matters to you for two reasons. First, this block is *driver* guidance, and `--install-guidance` writes it into exactly that global file: a stage inheriting it would read "surface permission gates to the human" and defer instead of doing its work, which is the engine losing the ability to proceed on its own. Second, when the human asks why a stage ignored a standing rule of theirs — always call this MCP tool, always write tests this way — this is the answer. A rule that must hold inside a run belongs in the stage prompt, not in global memory.

If a pipeline genuinely needs something that lives at the user layer (a user-level MCP server, most often), pass `--setting-sources user,project` for that run rather than making it the default.

## To *run* a pipeline, launch it — don't read it

When asked to run/drive the engine, launch it and drive it over `--channel`. Do **not** open the stage files (`01-plan.md`, `02-build.md`, …) to "understand the pipeline" first — reading the pipeline is the engine's job, done stage-by-stage in fresh agent sessions, not yours up front. Doing it yourself burns context on prompts you'll never act on and desyncs you from what the run is actually doing. If you need to know what a pipeline does before running, `./orchestra --list` names the built-ins — that's enough to launch. Read the stage files only when the task is to **edit, design, or debug** a pipeline, never to execute one.

## Drive `-i` over `--channel` — no TTY, no FIFO

Interactive mode (`-i`) is **not** terminal-only. Drive it with `--channel DIR`: the engine writes one JSON event per line to `DIR/events.jsonl` — stages starting and finishing, escalations as they're raised, every prompt with an id — and each prompt (stage gate, agent question, permission y/n) waits for an answer you give **by id**. Works the same on macOS, Linux and Windows.

```sh
C="$SCRATCH/chan-$(date +%s)"   # fresh dir per run; the engine refuses a reused one
PYTHONUNBUFFERED=1 ./orchestra "task" -i --channel "$C" --workspace /path/to/repo --runs-dir "$SCRATCH/runs" > "$SCRATCH/run.log" 2>&1 &
# Monitor tool: ./orchestra --watch --channel "$C"     (one line per event; exits when the run does)
# per prompt:   ./orchestra --channel "$C" --answer p3 1      (a number, an option's value, "1,3" for a checkbox, or text)
```

`--answer` is refused unless its id is the prompt **actually waiting**, and refused for an out-of-range option — so a stale or mistyped reply fails loudly at your command instead of landing on the wrong question. That's the desync the old FIFO recipe had; don't go back to it. Each `PROMPT` line from `--watch` names its id, its kind (`gate` / `question` / `permission`) and the exact `--answer` command.

Keep the channel in `$SCRATCH`. The engine refuses one inside `--workspace`, `--runs-dir` or `--docs-dir`, because stage agents write there and whoever writes the channel answers its permission prompts. Only one agent asks at a time (fan-out stages force `interactive=False`), so prompts never interleave.

Why not Claude Code's `SendMessage`: it was checked (CLI 2.1.282, SDK 0.2.119). A stage is a headless SDK session — it has no `ListAgents`, its `SendMessage` reaches only agents inside its own process, and nothing can message into it; the engine is plain Python with no supported way to send. Re-test when either changes; `orchestra_engine/channel.py` routes everything through `emit`/`ask`, so a messaging transport slots in beside the file.

## The human watches in the live view — it opens by itself

Every run appears in the **live view** by default — an Orchestra Engine app window of its own on macOS and Windows (a browser tab on Linux, or wherever the window can't start), one page for all runs: each run in the sidebar marked running / needs you / finished, and the selected run's thinking, messages and tool calls streaming in as a chat, escalations as callouts, each prompt shown with buttons. The first run opens the page; later runs join it rather than opening more windows. You don't open anything for them — just say it's there and to keep an eye on it (`./orchestra --view` re-opens it). A run you drive over `--channel` starts without waiting for the page, which opens on its own once the viewer is up; if `run.log` says `Live view unavailable`, run `./orchestra --view` yourself rather than leaving the human with nothing to watch. It's served from the same channel you drive, so the page and your `--answer` see the same prompts, and an answer from either side is checked the same way.

That page is also where **permission prompts belong**: when `--watch` shows one marked for the human, tell them — they can click allow/deny right there. Pass `--no-ui` only when there's no human or no display (CI, SSH).

## Hand over the published folder, not the run directory

A pipeline whose deliverable is a document — `approach` is the clear case: it decides *how* a ticket should be solved and builds nothing — publishes the reader's copy to `--docs-dir` (default `./orchestra-docs/plans`), in a folder named for the ticket rather than the run: `orchestra-docs/plans/abc-123/{SPEC.md, DECISION.md, README.md}`. That is the path to give the human. The run directory holds the same spec plus a dozen intermediate files under a timestamp, and when you followed the advice above and pointed `--runs-dir` at `$SCRATCH`, it is *gone* when the session ends — pointing someone at it hands them a dead path for the one output they were waiting for.

So: keep `--runs-dir` in the scratchpad (the run tree is yours, and ephemeral is correct for it), and leave `--docs-dir` alone so the deliverable outlives the run. Only the stages whose prompt names `{docs}` can write there, so this costs nothing on pipelines that publish nothing. When the run declines, the same folder gets `DECLINED.md` — say so; a decline with a file is a result, and it's the answer that most often gets lost.

## Watch for escalations — a run will tell you when it gave something up

Most runs are non-interactive: you launch them in the background, there are no gates, and nothing pauses to tell you anything. That is exactly when a run can quietly stop promising what it was asked for. `artifacts/ESCALATIONS.md` is where a stage records that — a behaviour withdrawn, a criterion narrowed, a stated requirement that fights a convention and got built anyway. It is append-only and the engine puts back anything removed from it (`engine/executor.py`), so it's the one artifact a later pass can't tidy away.

Arm a watch on it whenever you launch a background run: pass `--channel "$SCRATCH/chan-…"` (with or without `-i`) and run `./orchestra --watch --channel …` under the **Monitor** tool. Each escalation arrives as an `ESCALATION raised:` line with the stage's text quoted `  | `, the moment the stage that raised it finishes — pins dropped or inverted included, since the engine records those on the same file. The watch exits on `RUN complete` (whose line says whether escalations are unresolved), and if the engine dies without finishing it says so and exits too, so silence never stands in for success.

**When it fires, judge it, then hand it over.**
- A conflict the design stage flagged and built anyway is usually informational — let the run finish. You still want the feature built and verified, and a completed run is far better input to "should we change the spec" than a half-finished one.
- A **withdrawal** — a behaviour this run had already proved, now un-asserted — is the other kind. Stop the run rather than let four more stages build on it, and say so.

Either way tell the human what was raised, and push a notification when it's the kind that changes what they'd do next. What you must not do is resolve it yourself by amending the task and relaunching: the file exists precisely because a run may not settle these on its own authority, and driving the run doesn't transfer that authority to you.

A run nobody watched still reports — the engine prints each escalation as it's raised and the whole record again at the end, so it's in `run.log` either way.

## The one hard rule when driving it

Prompts you hit fall into three kinds, and only one of them is off-limits.

**The agent's own questions** (AskUserQuestion — "which approach?", "what should I name this?") are fine to answer; that's the engine working.

**The stage gate** — `Next stage: 'plan'` / `1. run  2. skip  3. quit` (`interaction.py: stage_gate`) — is the prompt you'll hit most, once before every stage. Answering **run** is just driving; take it yourself and keep moving. **skip** and **quit** are not: they change what the human ends up with — a stage that never ran, or a pipeline abandoned partway — and a run quietly truncated by the agent driving it looks identical to one that finished. Surface those unless the human already told you when to stop.

**Permission gates are not yours to answer.** Two exist:
- `can_use_tool` allow/deny, for tool calls the permission mode didn't auto-approve (`orchestra_engine/interaction.py`).
- The `bypassPermissions` confirmation — `Run with every tool call auto-approved?` (`main.py: confirm_bypass`). Answering "yes" here hands agents unreviewed file and shell access to the whole workspace.

Never silently answer either. Doing so satisfies human-in-the-loop on paper while no human reviewed anything — privilege escalation, and it defeats the only reason the gate exists. Surface them to the human and let them decide. `auto` is the sensible default; prefer a narrow `--permission-mode` so gates stay rare and mean something when they fire. Never reach for `--dangerously-skip-permissions` on your own initiative — it's the human's call, for scratch workspaces they can afford to lose.
