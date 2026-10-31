"""Install the engine's agent guidance into a Claude Code memory file.

The guidance an agent needs to *drive* this engine — launch a pipeline instead
of reading it, drive `-i` over a FIFO, never answer a permission gate itself —
lives in `agent_guidance.md` next to this module, version-controlled with the
code it describes. This installs it into `~/.claude/CLAUDE.md` as one
`<orchestra-engine>...</orchestra-engine>` block, so a new machine is one command away
from an agent that knows how to drive the engine.

Only the region between the two markers is ever rewritten. Everything else in
that file — other tagged sections, loose notes — survives byte-for-byte.
"""

from __future__ import annotations

import re
from pathlib import Path

TAG = "orchestra-engine"
# The tag from before the engine was renamed. A machine that installed under it
# has that block replaced, not a second copy appended beside it.
LEGACY_TAGS = ("loop-engine",)

# The whole block, markers included, so a re-install replaces it in place
# instead of stacking a second copy. Non-greedy: stops at the first close tag.
_BLOCK_RE = re.compile(
    rf"^<({'|'.join(map(re.escape, (TAG, *LEGACY_TAGS)))})>.*?^</\1>[ \t]*\n?",
    re.DOTALL | re.MULTILINE,
)

_GUIDANCE_FILE = Path(__file__).with_name("agent_guidance.md")
_ROOT_PLACEHOLDER = "{repo_root}"


def default_target() -> Path:
    """The global Claude Code memory file, loaded for every project."""
    return Path.home() / ".claude" / "CLAUDE.md"


def repo_root() -> Path | None:
    """The checkout this module was installed from, if it is one.

    bootstrap.py installs editable, so the package's parent is the repo. A
    plain wheel install has no checkout to point at — say so rather than
    stamping in a path that doesn't exist on this machine."""
    root = Path(__file__).resolve().parent.parent
    return root if (root / "pyproject.toml").exists() else None


def render_block() -> str:
    """The guidance wrapped in its markers, with this checkout's path stamped in."""
    body = _GUIDANCE_FILE.read_text(encoding="utf-8").strip()
    root = repo_root()
    body = body.replace(_ROOT_PLACEHOLDER, str(root) if root else "<path/to/orchestra-engine>")
    return f"<{TAG}>\n\n{body}\n\n</{TAG}>\n"


def install(target: Path, dry_run: bool = False) -> str:
    """Write the block into `target`, in place if it's already there.

    Returns what happened: created, updated, appended, or unchanged. Idempotent
    — installing twice from the same checkout leaves the file untouched the
    second time."""
    block = render_block()
    existing = target.read_text(encoding="utf-8") if target.exists() else None

    if existing is None:
        new, status = block, "created"
    elif _BLOCK_RE.search(existing):
        # A lambda replacement, so backslashes and \1-style groups in the
        # guidance text are literal rather than regex references.
        new = _BLOCK_RE.sub(lambda _: block, existing, count=1)
        status = "unchanged" if new == existing else "updated"
    else:
        separator = "" if existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
        new, status = existing + separator + block, "appended"

    if not dry_run and status != "unchanged":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new, encoding="utf-8")
    return status
