# my-pipelines

Pipelines for the [Orchestra Engine](https://github.com/Froredion/orchestra-engine).
This repo is **only** its pipelines — it does not copy the engine. The engine
lives once, in its own repo; this repo reuses it as an *overlay*.

> Rename this file's title and rewrite this paragraph — everything below is
> general and can stay as-is.

## Setup

Clone this repo **next to an engine checkout**. Neither directory name matters:

```
~/code/
├── orchestra-engine/         # the engine (has its own .venv)
└── my-pipelines/        # this repo — pipelines only
```

The engine has to be set up once — its README covers that (`python bootstrap.py`).
There is nothing to install here.

If your engine checkout lives somewhere else entirely, point at it:

```bash
export ORCHESTRA_ENGINE_DIR=/path/to/orchestra-engine
```

## Usage

```bash
# run a pipeline from this repo by name
./orchestra.sh "your task" --pipeline example --workspace /path/to/some-repo

# everything available — this repo's pipelines plus the engine's built-ins
./orchestra.sh --list
```

`./orchestra.sh` finds the engine, exposes this repo's `pipelines/` to it, and hands
over. Every engine flag passes straight through, so `./orchestra.sh --help` is the
engine's own help.

**You get both pipeline sets, always.** The overlay only ever *adds*: the engine
searches overlay directories first and then always appends its own built-ins. So
`--list` shows this repo's pipelines and the engine's together, and a name
collision resolves to ours — deliberately, so an overlay can shadow a built-in
without deleting it.

## Adding a pipeline

A pipeline is a folder of numbered prompt files. Filename order is stage order:

```
pipelines/
└── my-pipeline/
    ├── 01-plan.md
    └── 02-build.md
```

Each stage is its own agent session. It gets `{task}` (what you asked for) and
`{handoff}` (the previous stage's summary and session ID), and it writes stage
documents to the artifacts directory for the next stage to read.

Frontmatter at the top of a stage file sets its shape — `repeat:`, `fan_out:`,
`skip_if:`, `decide:`. The engine's `docs/authoring-pipelines.md` is the
reference.

Check the parse before running it, which catches malformed frontmatter (silent
otherwise):

```bash
./orchestra.sh --list --pipeline my-pipeline
```

`pipelines/example/` is a working two-stage pipeline to copy from — delete it
once you have your own.
