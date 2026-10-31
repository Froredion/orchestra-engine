# Orchestra Engine

Staged agent orchestration engine: chain Claude Agent SDK sessions, handing each stage's
**summary + session ID** to the next. A pipeline is just a folder of numbered
prompt files (`01-plan.md`, `02-build.md`, …); the engine runs them in filename
order. Cross-platform (macOS/Linux/Windows).

## Setup

**Read [README.md](README.md) for the full step-by-step** — its `## Setup` and
`## Usage` sections are the source of truth. In short, one command on every OS:

```bash
python bootstrap.py
```

It creates `.venv` with whatever Python ran it, installs the engine editable,
and prints the launcher for your platform (`./orchestra` on macOS/Linux, `orchestra` on
Windows). Needs Python 3.10+; re-running is safe.

Then, once per machine:

```bash
./orchestra --install-guidance
```

That installs [orchestra_engine/agent_guidance.md](orchestra_engine/agent_guidance.md) —
how to *drive* the engine as an agent (launch pipelines instead of reading
them, `-i` over a pipe, permission gates are the human's to answer) — into
`~/.claude/CLAUDE.md` as one `<orchestra-engine>` block. Idempotent, and it leaves
the rest of that file untouched. Re-run it whenever the guidance changes.

## Learn how it works

- **[README.md](README.md)** — how to *run* it: setup, usage, the built-in
  pipelines, and every flag. `./orchestra --list` describes the live pipelines.
- **[UNDERSTANDING.md](UNDERSTANDING.md)** — how to *think* about it: the
  execution model explained from zero context.

## Working in this repo

- Tests: `.venv/bin/pytest` (installed by `bootstrap.py`; the suite is scoped
  to `tests/` via `pyproject.toml`). Run it after any change to the engine.
- The engine lives in `orchestra_engine/`; pipelines are data under
  `orchestra_engine/pipelines/`. Adding a pipeline is just dropping a folder of
  numbered `.md` files — no code registration.
