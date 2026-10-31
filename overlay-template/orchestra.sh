#!/bin/sh
# Drop-in launcher for a pipelines-only repo (an "overlay").
#
# You get this file by copying the engine's overlay-template/ folder. That folder
# IS a working overlay repo, so copying it is the entire setup:
#
#     cp -R <engine>/overlay-template ~/code/my-pipelines
#
#     my-pipelines/
#     ├── orchestra.sh              <- this file, copied verbatim; never edited
#     └── pipelines/
#         └── my-pipeline/
#             ├── 01-plan.md
#             └── 02-build.md
#
#     ./orchestra.sh "your task" --pipeline my-pipeline --workspace /path/to/code
#
# It carries no repo-specific knowledge, which is what makes it copyable: it
# finds the engine, exposes THIS repo's pipelines/ to it via $ORCHESTRA_PIPELINES_PATH,
# and hands over. Every engine flag passes straight through, and the engine always
# appends its own built-ins after the overlay, so `--pipeline bugfix` keeps working
# next to your own names. An overlay only ever adds.
#
# Why a wrapper instead of forking the engine: a fork is a whole copy of the
# engine, so a handful of private pipelines means syncing the engine forever and
# watching the two drift. As an overlay your repo holds only what's actually
# different, and picks up every engine change the moment you pull the engine.
#
# Finding the engine, in order — first hit wins:
#   1. $ORCHESTRA_ENGINE_DIR (or the older $LOOP_ENGINE_DIR / $LOOP_ENGINEERING_DIR), when set — an explicit
#      choice always wins, and is reported even if it turns out to be wrong.
#   2. a sibling directory next to this repo, by known name, then the directory
#      containing this one, then any sibling that validates — so a clone under a
#      third name works, and so this template runs in place inside the engine.
#   3. the orchestra-engine console script on $PATH, for a pip-installed
#      engine with no checkout beside you at all.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"   # this repo's root, from $0 — never $PWD

# A candidate is the engine only if it has BOTH the launcher and the Python
# package. Testing for ./orchestra alone is not enough: a sibling checkout of another
# *overlay* also has a ./orchestra, so that test happily resolves the "engine" to an
# overlay and then fails deep inside the stage handoff, far from the cause.
is_engine() {
  [ -n "${1:-}" ] && [ -d "$1" ] && [ -x "$1/orchestra" ] && [ -f "$1/orchestra_engine/pipeline.py" ]
}

# True when a directory holds at least one numbered stage prompt (01-plan.md,
# 02_build.md, ...). The engine's parser is authoritative; this is only here so a
# structural mistake produces a clear message instead of a run that silently
# contributes no pipelines.
has_stage_files() {
  for f in "$1"/*.md; do
    [ -f "$f" ] || continue
    case "${f##*/}" in [0-9]*[-_]*.md) return 0 ;; esac
  done
  return 1
}

# Either a pipeline directly under pipelines/, or one inside a single level of
# group folder — the same two shapes the engine discovers.
has_pipelines() {
  for child in "$1"/*; do
    [ -d "$child" ] || continue
    has_stage_files "$child" && return 0
    for grandchild in "$child"/*; do
      [ -d "$grandchild" ] || continue
      has_stage_files "$grandchild" && return 0
    done
  done
  return 1
}

# ---------------------------------------------------------------- structure ---
# Checked before anything else, because this is the mistake a new overlay makes.
if is_engine "$here"; then
  cat >&2 <<EOF
orchestra.sh is the launcher for an *overlay* repo, but this looks like the engine
checkout itself ($here).

In the engine repo, run its own launcher instead:
  ./orchestra --list

Copy orchestra.sh into a separate pipelines-only repo to use it as intended.
EOF
  exit 1
fi

if [ ! -d "$here/pipelines" ]; then
  cat >&2 <<EOF
No pipelines/ directory in $here.

An overlay repo is only its pipelines, and this launcher exposes them by name.
The expected structure is:

  $(basename "$here")/
  ├── orchestra.sh
  └── pipelines/
      └── my-pipeline/
          ├── 01-plan.md      <- numbered prompts; filename order is stage order
          └── 02-build.md

Create it and try again:
  mkdir -p "$here/pipelines/my-pipeline"
EOF
  exit 1
fi

# Present but holding nothing recognizable: warn, don't block. Mid-authoring,
# --list and the built-in pipelines are still perfectly usable.
if ! has_pipelines "$here/pipelines"; then
  echo "orchestra.sh: warning: no pipelines found in $here/pipelines" >&2
  echo "  expected e.g. pipelines/my-pipeline/01-plan.md — only the engine's" >&2
  echo "  built-in pipelines will be available." >&2
fi

# ------------------------------------------------------------------- engine ---
engine=""
override="${ORCHESTRA_ENGINE_DIR:-${LOOP_ENGINE_DIR:-${LOOP_ENGINEERING_DIR:-}}}"
if [ -n "$override" ]; then
  # Reported by the path the operator actually asked for, rather than silently
  # falling back to a sibling — a wrong override should be visible, not masked.
  if is_engine "$override"; then
    engine="$(cd "$override" && pwd)"
  else
    echo "orchestra.sh: \$ORCHESTRA_ENGINE_DIR is not an Orchestra Engine checkout: $override" >&2
    exit 1
  fi
else
  parent="$(cd "$here/.." && pwd)"
  for name in orchestra-engine loop-engine loop-engineering; do
    if is_engine "$parent/$name"; then engine="$parent/$name"; break; fi
  done
  # The directory holding this one, when that is itself the engine — which is how
  # overlay-template/ still runs while it sits inside the engine repo, so the
  # template can be smoke-tested in place before anyone copies it anywhere.
  if [ -z "$engine" ] && is_engine "$parent"; then engine="$parent"; fi
  if [ -z "$engine" ]; then
    # Any other immediate sibling that validates. Immediate children only: a
    # fixed depth keeps discovery cheap, predictable, and cycle-free.
    for cand in "$parent"/*; do
      [ -d "$cand" ] || continue
      [ "$(cd "$cand" && pwd)" = "$here" ] && continue
      if is_engine "$cand"; then engine="$(cd "$cand" && pwd)"; break; fi
    done
  fi
fi

# --------------------------------------------------------------- pipelines/ ---
# Ours leads any existing value, so a bare --pipeline <name> resolves to this
# repo first and still falls through to the engine's built-ins.
if [ -n "${ORCHESTRA_PIPELINES_PATH:-}" ]; then
  ORCHESTRA_PIPELINES_PATH="$here/pipelines:$ORCHESTRA_PIPELINES_PATH"
else
  ORCHESTRA_PIPELINES_PATH="$here/pipelines"
fi
export ORCHESTRA_PIPELINES_PATH

# ------------------------------------------------------------------ hand off ---
# A checkout is preferred over an installed script: it's the one whose pipelines
# and code you can actually read while a run is going.
if [ -n "$engine" ]; then
  if [ -x "$engine/.venv/bin/python" ]; then
    exec "$engine/orchestra" "$@"
  fi
  # Found, but never set up. Say so from here — the engine's own message names a
  # bootstrap command without saying which directory to run it in.
  cat >&2 <<EOF
Found the engine at $engine, but it has no virtualenv yet.

Set it up once (safe to re-run):
  cd "$engine" && python bootstrap.py
EOF
  exit 1
fi

if command -v orchestra-engine >/dev/null 2>&1; then
  exec orchestra-engine "$@"
fi

cat >&2 <<EOF
Could not find the Orchestra Engine.

This repo ships only pipelines — the engine is a separate Python package. Clone it
as a sibling directory:

  git clone https://github.com/Froredion/orchestra-engine.git

…so that it sits next to this repo:

  $(cd "$here/.." && pwd)/
  ├── orchestra-engine/          <- the engine (any name works; it is validated, not assumed)
  └── $(basename "$here")/         <- this repo

Then build its venv once:
  cd <engine> && python bootstrap.py

Or point at a checkout somewhere else entirely:
  ORCHESTRA_ENGINE_DIR=/path/to/orchestra-engine ./orchestra.sh --list
EOF
exit 1
