"""Grade an already-produced model.patch through Pier's normal verifier path.

Salvage tool, not part of any measured arm. The 2026-08-08 orchestra-engine trials
ran their pipelines to completion but lost the grade to a CRLF-mangled
tests/test.sh (the verifier container tried to exec "/bin/bash\r"). The patches
survived; only the grading did not.

Rather than hand-assemble the verifier container, this replays a saved patch as
if it were the agent's work: apply it in /app, commit it, and let Pier run its
own verifier and emit its own reward.json. The harness does the grading, so the
numbers come out of the same path a live trial's would.

Usage:
    pier run -p deep-swe/tasks/<task> \\
        --agent-import-path eval.feature.deepswe.replay_agent:PatchReplayAgent \\
        --ak patch_path=jobs/orchestra-engine/<run>/<trial>/artifacts/model.patch \\
        -k 1 -n 1 -o jobs/replay

This is a salvage and debugging tool. A number it produces is only as
trustworthy as the patch it replays, and it says nothing about the agent phase
that produced that patch — the trajectory and token metrics of the original
trial are gone either way.
"""

from __future__ import annotations

from pathlib import Path

from pier.agents.base import BaseAgent
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext


class PatchReplayAgent(BaseAgent):
    SUPPORTS_WINDOWS: bool = True

    #: Where DeepSWE checks the repo out.
    DEFAULT_REPO_DIR = "/app"

    @staticmethod
    def name() -> str:
        return "patch-replay"

    def __init__(
        self,
        logs_dir: Path,
        patch_path: str | None = None,
        repo_dir: str = DEFAULT_REPO_DIR,
        model_name: str | None = None,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        if not patch_path:
            raise ValueError("patch_path is required, e.g. --ak patch_path=…/model.patch")
        self._patch_path = Path(patch_path).resolve()
        if not self._patch_path.is_file():
            raise FileNotFoundError(f"No patch at {self._patch_path}")
        self._repo_dir = repo_dir

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        target = "/tmp/replay.patch"
        await environment.upload_file(source_path=self._patch_path, target_path=target)

        # --binary because the collect hook produced the patch with `git diff
        # --binary`; --whitespace=nowarn keeps a clean log. Committing is the
        # point: grading reads `git diff <base>..HEAD`, so an applied-but-
        # uncommitted patch scores zero exactly like the real runs would have.
        script = (
            f"set -euo pipefail; cd {self._repo_dir}; "
            "git config --global --add safe.directory /app; "
            'git config user.email replay@local; git config user.name replay; '
            f"git apply --binary --whitespace=nowarn {target}; "
            "git add -A; "
            'git commit -q -m "replayed patch"; '
            "git --no-pager diff --stat HEAD~1..HEAD | tail -3"
        )
        result = await environment.exec(command=f"bash -lc {script!r}", user="root")

        log = self.logs_dir / "replay.txt"
        log.write_text(
            f"exit={result.return_code}\n"
            f"--- stdout ---\n{result.stdout or ''}\n"
            f"--- stderr ---\n{result.stderr or ''}\n",
            encoding="utf-8",
        )
        if result.return_code != 0:
            raise RuntimeError(
                f"Replay failed (exit {result.return_code}): {result.stderr or result.stdout}"
            )
