"""Run the Orchestra Engine as a Pier agent, so DeepSWE grades it like any other.

Pier (github.com/datacurve-ai/pier) runs a coding agent inside a task container,
then grades in a *separate* container from ``git diff <base_commit>..HEAD``.
This adapter registers the staged Orchestra Engine as one of the agents Pier can
drive, which is the thing that makes "staged pipeline vs one long session" an
apples-to-apples comparison: same sandbox, same verifier, same task text. Any
hand-rolled alternative reintroduces the confounds DeepSWE's isolated verifier
exists to remove.

No fork of Pier is needed — it resolves agents by import path::

    pier run -p deep-swe/tasks/prometheus-transactional-reload-status \\
        --agent-import-path eval.feature.deepswe.pier_agent:OrchestraEngineAgent

This subclasses Pier's own ``ClaudeCode`` agent rather than ``BaseInstalledAgent``,
inheriting its Node + Claude Code CLI install, its egress allowlist (task
containers run ``no-network`` *plus* the agent's allowlist, so dropping that
inheritance silently kills every model call), and its auth/env resolution. What
it replaces is the invocation — one staged ``orchestra`` run instead of one
``claude -p`` session — and the trajectory collection, which the parent gets
wrong for us for a specific reason documented on ``populate_context_post_run``.
"""

from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from pier.agents.installed.base import with_prompt_template
from pier.agents.installed.claude_code import ClaudeCode
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext
from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.trial.paths import EnvironmentPaths
from pier.models.trajectories import FinalMetrics, Trajectory
from pier.utils.trajectory_metrics import populate_context_from_final_metrics

def _repo_root() -> Path:
    """The engine checkout this file lives in, found by walking up.

    Counting ``.parent`` hops would be silently wrong the next time this
    adapter moves — and it has already moved once, from ``eval/`` down to
    ``eval/feature/deepswe/``. A wrong root does not fail loudly: it becomes
    the pip target uploaded into the container, so look for the two things
    ``_stage_package`` actually needs and anchor on those.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "orchestra_engine"
        ).is_dir():
            return candidate
    raise RuntimeError(f"No Orchestra Engine checkout above {here}")


#: Where pip finds the engine. Defaults to *this checkout* — uploaded into the
#: container at setup — rather than a git URL, for two reasons. The repo is
#: private, so a container has no way to clone it; and benchmarking the working
#: tree is what you actually want, since a git spec silently measures whatever
#: was last pushed instead of the code in front of you. Any pip-installable
#: string still works: pass ``engine_spec=git+https://…@branch`` for a public
#: fork.
DEFAULT_ENGINE_SPEC = str(_repo_root())

#: Where the uploaded source lands in the container.
LOCAL_SRC_TARGET = "/tmp/orchestra-engine-src"

#: The engine's dependencies, downloaded as wheels at image *build* time. The
#: agent container's egress allows only the model API, so a runtime
#: ``pip install`` cannot reach PyPI (``403 Forbidden`` from the proxy). The
#: build has network; the runtime install then runs ``--no-index`` against
#: this directory, and the agent never gains network the baseline lacks.
WHEELHOUSE = "/opt/orchestra-engine-wheels"


def _engine_requirements() -> list[str]:
    """The checkout's runtime dependencies plus its build backend, from pyproject."""
    import tomllib

    pyproject = tomllib.loads((_repo_root() / "pyproject.toml").read_text())
    return [
        *pyproject["build-system"]["requires"],
        *pyproject["project"]["dependencies"],
        "wheel",
    ]

#: The engine gets its own virtualenv, deliberately. DeepSWE spans five
#: languages and many of its tasks are Python; installing ``claude-agent-sdk``
#: and friends into the container's system Python could perturb the *task's*
#: dependency resolution and cause failures that look like the agent's fault.
#: An isolated venv keeps the harness off the graded surface.
VENV = "$HOME/.loop-venv"

#: Belt-and-braces. ``--runs-dir`` already keeps stage documents out of the
#: workspace ("code goes in the repo, reasoning goes in the run"), so these
#: should never appear under /app. If a prompt ever regresses and writes one
#: there anyway, this stops it being committed into the graded diff. Lives in
#: ``.git/info/exclude``, which is not tracked and so never shows up in the diff
#: itself — unlike editing ``.gitignore``.
ENGINE_ARTIFACTS = (
    "SCOPE.md",
    "BASELINE.md",
    "DESIGN.md",
    "FEATURE.md",
    "TESTREPORT.md",
    "VERIFY.md",
    "verdict.txt",
    "decision.json",
    # approach's documents, for the chained ``approach+feature`` arm
    "SPEC.md",
    "DECISION.md",
    "REUSE.md",
)

#: Separates the pipelines of a chain: ``--ak pipeline=approach+feature`` runs
#: approach, then hands its SPEC.md to feature in the same container.
CHAIN_SEP = "+"

#: The share of the agent budget the first pipeline of a chain may spend. The
#: last pipeline is the one that writes the graded diff, so an upstream stage
#: that runs long must not starve it.
CHAIN_FIRST_SHARE = 1 / 3

#: Where a chain's published documents land. Under ``engine_runs_dir`` on
#: purpose, so ``_collect_stage_artifacts`` brings the SPEC home with the rest.
CHAIN_DOCS_SUBDIR = "docs"


def _with_spec(instruction: str, spec_path: str | None) -> str:
    """The downstream task text: the original ticket, plus the spec to build.

    The ticket stays first and stays the authority, because the verifier grades
    against it, not against the spec. Nobody rules on approach's escalations in
    an unattended trial, so the text says which side wins when they disagree.
    """
    if not spec_path:
        return instruction
    return (
        f"{instruction}\n\n---\n\n"
        f"An earlier `approach` run already decided how to build this. Its spec "
        f"is at {spec_path} — read it first and implement it. No human has ruled "
        f"on the escalations it raises: where the spec and the task text above "
        f"disagree, the task text wins."
    )

def _as_bool(value: Any) -> bool:
    """Coerce a Pier ``--agent-kwarg`` value to a bool.

    CLI kwargs arrive as strings, and a bare ``bool("false")`` is ``True`` —
    so ``--ak dangerously_skip_permissions=false`` would silently turn on
    auto-approval of every tool call. Parse the word, don't test truthiness.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


_PYTHON_INSTALL = (
    "if command -v apk > /dev/null 2>&1; then"
    "  apk add --no-cache python3 py3-pip git;"
    " elif command -v apt-get > /dev/null 2>&1; then"
    "  apt-get update && apt-get install -y python3 python3-pip python3-venv git;"
    " elif command -v yum > /dev/null 2>&1; then"
    "  yum install -y python3 python3-pip git;"
    " else"
    '  echo "Warning: no known package manager; assuming python3 is present" >&2;'
    " fi"
)


class OrchestraEngineAgent(ClaudeCode):
    """The staged Orchestra Engine, as a Pier-benchmarkable agent."""

    SUPPORTS_ATIF: bool = True

    @staticmethod
    def name() -> str:
        return "orchestra-engine"

    def __init__(
        self,
        logs_dir: Path,
        pipeline: str = "feature",
        repo_dir: str = "/app",
        engine_runs_dir: str = "/logs/loop-run",
        engine_spec: str = DEFAULT_ENGINE_SPEC,
        permission_mode: str = "auto",
        dangerously_skip_permissions: bool = False,
        max_turns: int | None = None,
        max_hops: int | None = None,
        autocommit: bool = True,
        agent_timeout_sec: int | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        # ``bypassPermissions`` makes the engine stop and ask for confirmation
        # before it starts (main.py: confirm_bypass). There is no one to answer
        # in a benchmark container, so the run would block until the trial
        # timeout and score zero having done nothing. Refuse the combination up
        # front instead of discovering it 90 minutes later.
        #
        # The escape hatch is deliberately explicit rather than defaulted:
        # auto-approving every tool call is the operator's call to make, even
        # though a disposable single-task container is exactly the scratch
        # workspace where it is defensible.
        dangerously_skip_permissions = _as_bool(dangerously_skip_permissions)
        if permission_mode == "bypassPermissions" and not dangerously_skip_permissions:
            raise ValueError(
                "permission_mode='bypassPermissions' needs "
                "dangerously_skip_permissions=True in an unattended run: the "
                "engine asks for confirmation before starting, and nothing in a "
                "benchmark container can answer it. Set it knowingly, or use "
                "permission_mode='auto'."
            )

        self.pipeline = pipeline
        self.repo_dir = repo_dir
        self.engine_runs_dir = engine_runs_dir
        self.engine_spec = engine_spec
        self.permission_mode = permission_mode
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.engine_max_turns = max_turns
        self.engine_max_hops = max_hops
        self.autocommit = _as_bool(autocommit)
        self.agent_timeout_sec = (
            int(agent_timeout_sec) if agent_timeout_sec is not None else None
        )
        self._stdout: str | None = None

        super().__init__(logs_dir, *args, **kwargs)

    # ---- install -------------------------------------------------------

    def _venv_python(self) -> str:
        return f'"{VENV}/bin/python"'

    def install_spec(self) -> AgentInstallSpec:
        """ClaudeCode's Node + CLI install, plus Python and the engine itself.

        The engine drives the Claude Agent SDK, which needs the Claude Code CLI
        underneath it — so the parent's steps are a prerequisite here, not an
        alternative.
        """
        base = super().install_spec()
        steps = list(base.steps)

        steps.append(
            InstallStep(
                user="root",
                env={"DEBIAN_FRONTEND": "noninteractive"},
                run=_PYTHON_INSTALL,
            )
        )
        if self._is_local_source():
            wheels = shlex.quote(WHEELHOUSE)
            requirements = " ".join(shlex.quote(r) for r in _engine_requirements())
            steps.append(
                InstallStep(
                    user="root",
                    run=(
                        f"python3 -m pip download -q -d {wheels} {requirements} "
                        f"&& chmod -R a+rX {wheels}"
                    ),
                )
            )
        # The engine is deliberately NOT installed here. Pier inlines these
        # steps into the image at *build* time, and the engine installs from
        # source uploaded at *runtime* — which does not exist during the build.
        # That install lives in setup(); see _install_engine.
        return AgentInstallSpec(
            agent_name=self.name(),
            version=base.version,
            steps=steps,
            verification_command="python3 --version",
        )

    def get_version_command(self) -> str | None:
        return (
            f"{self._venv_python()} -c "
            "'import importlib.metadata as m; "
            "print(m.version(\"orchestra-engine\"))'"
        )

    def parse_version(self, stdout: str) -> str:
        return stdout.strip() or "unknown"

    @staticmethod
    def _sessions_dir() -> str:
        """The container path Claude Code writes session transcripts to.

        ``EnvironmentPaths.agent_dir`` (``/logs/agent``) is mounted from the
        trial directory, so anything written here is already on the operator's
        disk when the container dies — no download step, which is why the
        baseline's transcripts survive. Same location the parent uses, so a
        loop trial and a baseline trial keep the same layout on disk.
        """
        return (EnvironmentPaths.agent_dir / "sessions").as_posix()

    def _is_local_source(self) -> bool:
        """True when ``engine_spec`` points at a checkout rather than a pip spec."""
        try:
            return Path(self.engine_spec).expanduser().is_dir()
        except OSError:
            return False

    async def setup(self, environment: BaseEnvironment) -> None:
        """Upload the engine source and install it, then delegate up.

        Ordering here is forced by how Pier builds images. ``install_spec()``
        steps are inlined into the Dockerfile and run at *build* time, so they
        cannot see anything we upload — an earlier version installed the engine
        there and failed with "engine source not found in container". Everything
        that depends on uploaded source has to happen at runtime, which is here.

        We install *before* ``super().setup()`` because the base class probes
        the agent version after installing, and ``get_version_command`` asks the
        engine for its own version — which only answers once it exists.
        """
        # install_spec() put python3 in the image, so it is normally already
        # here. Guard anyway: if the environment was not prebuilt from the spec,
        # the base class installs it later and we would be too early.
        probe = await environment.exec(command="command -v python3", user="root")
        if getattr(probe, "return_code", 1) != 0:
            self.logger.debug("python3 absent; installing before the engine")
            await self.exec_as_root(environment, _PYTHON_INSTALL)

        if self._is_local_source():
            source = Path(self.engine_spec).expanduser().resolve()
            with tempfile.TemporaryDirectory() as staging:
                self._stage_package(source, Path(staging))
                self.logger.debug(
                    f"Uploading engine source {source} -> {LOCAL_SRC_TARGET}"
                )
                await environment.upload_dir(staging, LOCAL_SRC_TARGET)

        await self._install_engine(environment)
        await super().setup(environment)

    async def _install_engine(self, environment: BaseEnvironment) -> None:
        """Create the venv and install the engine into it, at runtime."""
        if self._is_local_source():
            # upload_dir's contents-vs-directory semantics are unspecified by
            # the base class, so locate pyproject.toml instead of assuming.
            target = shlex.quote(LOCAL_SRC_TARGET)
            resolve_src = (
                f"SRC={target}; "
                f'if [ ! -f "$SRC/pyproject.toml" ]; then '
                f"  SRC=$(dirname \"$(find {target} -maxdepth 3 -name pyproject.toml "
                f'| head -1)"); '
                "fi; "
                'test -f "$SRC/pyproject.toml" || '
                '{ echo "engine source not found in container" >&2; exit 1; }; '
            )
            pip_target = '"$SRC"'
            # Offline, from the build-time wheelhouse: see WHEELHOUSE.
            pip_install = (
                f'"{VENV}/bin/pip" install -q --no-index '
                f"--find-links {shlex.quote(WHEELHOUSE)} {pip_target}"
            )
        else:
            resolve_src = ""
            pip_target = shlex.quote(self.engine_spec)
            pip_install = (
                f'"{VENV}/bin/pip" install --upgrade pip -q && '
                f'"{VENV}/bin/pip" install -q {pip_target}'
            )

        await self.exec_as_agent(
            environment,
            (
                "set -euo pipefail; "
                f"{resolve_src}"
                f"python3 -m venv {VENV} && "
                f"{pip_install} && "
                f"{self._venv_python()} -m orchestra_engine --list > /dev/null && "
                'echo "Orchestra Engine installed"'
            ),
        )

    @staticmethod
    def _stage_package(source: Path, staging: Path) -> None:
        """Copy just what pip needs to build the engine.

        Uploading the checkout wholesale would drag in ``.venv``, ``.git``,
        ``runs/`` and the scratch workspaces — hundreds of megabytes of things
        the container has no use for. ``pyproject.toml`` declares the package as
        ``orchestra_engine*`` plus its markdown package-data, so that is all that
        needs to travel.
        """
        missing = [
            name
            for name in ("pyproject.toml", "orchestra_engine")
            if not (source / name).exists()
        ]
        if missing:
            raise RuntimeError(
                f"{source} does not look like the Orchestra Engine checkout "
                f"(missing: {', '.join(missing)})"
            )
        shutil.copytree(
            source / "orchestra_engine",
            staging / "orchestra_engine",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copy2(source / "pyproject.toml", staging / "pyproject.toml")
        for optional in ("README.md", "requirements.txt"):
            if (source / optional).exists():
                shutil.copy2(source / optional, staging / optional)

    # ---- run -----------------------------------------------------------

    @property
    def pipelines(self) -> list[str]:
        return [p.strip() for p in self.pipeline.split(CHAIN_SEP) if p.strip()]

    @property
    def chain_docs_dir(self) -> str:
        return f"{self.engine_runs_dir}/{CHAIN_DOCS_SUBDIR}"

    def _build_command(
        self,
        instruction: str,
        pipeline: str | None = None,
        timeout_sec: int | None = None,
    ) -> str:
        """The engine invocation. ``instruction`` is untrusted task text — quote it.

        ``pipeline`` and ``timeout_sec`` default to the adapter's own settings;
        a chain passes one link's pipeline and its share of the budget.
        """
        pipeline = pipeline or self.pipeline
        if timeout_sec is None:
            timeout_sec = self.agent_timeout_sec
        parts = [
            self._venv_python(),
            "-m",
            "orchestra_engine",
            shlex.quote(instruction),
            "-y",  # skip the setup wizard; flags and defaults as-is
            "--pipeline",
            shlex.quote(pipeline),
            "--workspace",
            shlex.quote(self.repo_dir),
            # Stage documents and the run log land here, NOT in the workspace,
            # so they never reach the graded diff.
            "--runs-dir",
            shlex.quote(self.engine_runs_dir),
            "--permission-mode",
            shlex.quote(self.permission_mode),
        ]
        if len(self.pipelines) > 1:
            parts += ["--docs-dir", shlex.quote(self.chain_docs_dir)]
        if self.dangerously_skip_permissions:
            parts.append("--dangerously-skip-permissions")
        if self.model_name:
            parts += ["--model", shlex.quote(self.model_name.split("/")[-1])]
        if self.engine_max_turns is not None:
            parts += ["--max-turns", str(int(self.engine_max_turns))]
        if self.engine_max_hops is not None:
            parts += ["--max-hops", str(int(self.engine_max_hops))]
        command = " ".join(parts)

        # Stop the engine *inside* the container, a little before Pier's own
        # agent timeout fires. This matters more than it looks.
        #
        # Pier enforces its timeout with ``asyncio.wait_for(agent.run(...))``,
        # which *cancels* this coroutine — and a cancelled coroutine cannot
        # reliably ``await`` anything more, including the autocommit in run()'s
        # ``finally``. Losing that means losing every committed-but-for-the-
        # commit change in a 90-minute trial, scored as a flat zero.
        #
        # A container-side ``timeout`` turns that cancellation into an ordinary
        # non-zero exit (124). The command returns, ``finally`` runs normally,
        # the work gets committed, and Pier still collects and grades it —
        # it treats NonZeroAgentExitCodeError as a gradeable outcome.
        if timeout_sec is not None:
            command = f"timeout -k 30 {timeout_sec} {command}"
        return command

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        env = self._engine_process_env()
        await self._guard_workspace(environment)
        await self._prepare_sessions_dir(environment)

        try:
            if len(self.pipelines) > 1:
                await self._run_chain(instruction, environment, env)
            else:
                result = await self.exec_as_agent(
                    environment,
                    self._build_command(instruction),
                    env=env,
                    cwd=self.repo_dir,
                )
                self._stdout = getattr(result, "stdout", None)
        except Exception as exc:
            # A failed or timed-out run is the case where the engine's stage
            # output matters most, and exec_as_agent raises rather than
            # returning — so pull what it captured off the exception instead of
            # losing it. Pier truncates stdout/stderr into the message. A chain
            # has already logged every link, the failure included.
            if len(self.pipelines) == 1:
                self._stdout = f"[run failed: {type(exc).__name__}]\n{exc}"
            raise
        finally:
            # Grading reads ``git diff <base>..HEAD``, so uncommitted work is
            # worth exactly zero no matter how good it is. Run this even when
            # the pipeline raised or timed out: partial committed work is a
            # real (and interesting) datapoint, silence is not.
            self._write_stdout_log()
            if self.autocommit:
                await self._ensure_committed(environment)
            # After the commit, so a slow download can never delay the thing
            # that makes partial work gradeable.
            await self._collect_stage_artifacts(environment)

    async def _run_chain(
        self, instruction: str, environment: BaseEnvironment, env: dict[str, str]
    ) -> None:
        """Run each pipeline of the chain in turn, handing the SPEC downstream.

        Only the last link writes the graded diff, so an upstream link that
        fails or times out is logged and skipped past rather than raised: the
        last link then runs on the bare ticket, which is still a gradeable trial.
        """
        deadline = (
            time.monotonic() + self.agent_timeout_sec
            if self.agent_timeout_sec is not None
            else None
        )
        logs: list[str] = []
        task = instruction
        *upstream, last = self.pipelines
        for pipeline in upstream:
            budget = (
                int((deadline - time.monotonic()) * CHAIN_FIRST_SHARE)
                if deadline is not None
                else None
            )
            logs.append(f"===== {pipeline} =====")
            try:
                result = await self.exec_as_agent(
                    environment,
                    self._build_command(task, pipeline, budget),
                    env=env,
                    cwd=self.repo_dir,
                )
                logs.append(getattr(result, "stdout", None) or "")
            except Exception as exc:
                logs.append(f"[{pipeline} failed: {type(exc).__name__}]\n{exc}")
            task = _with_spec(instruction, await self._find_spec(environment))
        self._stdout = "\n".join(logs)

        budget = (
            max(int(deadline - time.monotonic()), 60) if deadline is not None else None
        )
        logs.append(f"===== {last} =====")
        try:
            result = await self.exec_as_agent(
                environment,
                self._build_command(task, last, budget),
                env=env,
                cwd=self.repo_dir,
            )
            logs.append(getattr(result, "stdout", None) or "")
        except Exception as exc:
            logs.append(f"[run failed: {type(exc).__name__}]\n{exc}")
            raise
        finally:
            self._stdout = "\n".join(logs)

    async def _find_spec(self, environment: BaseEnvironment) -> str | None:
        """The newest SPEC*.md an upstream link published, or None if it declined."""
        docs = shlex.quote(self.chain_docs_dir)
        try:
            result = await self.exec_as_agent(
                environment,
                f"ls -t {docs}/*/SPEC*.md 2>/dev/null | head -1",
            )
        except Exception as exc:
            self.logger.debug(f"Could not look for a SPEC: {exc}")
            return None
        path = (getattr(result, "stdout", None) or "").strip()
        return path or None

    async def _prepare_sessions_dir(self, environment: BaseEnvironment) -> None:
        """Create the transcript directory before the engine opens a session.

        The parent does this in its own ``run()``; this class overrides that, so
        the directory has to be made here or the CLI meets a missing path on a
        mounted volume. Never fatal — a run without transcripts is worse
        instrumented, not wrong, and failing the trial over it would throw away
        the graded work too.
        """
        sessions = shlex.quote(self._sessions_dir())
        try:
            await self.exec_as_agent(
                environment,
                f"mkdir -p {sessions}/projects {sessions}/todos "
                f"{sessions}/shell-snapshots {sessions}/statsig {sessions}/debug",
            )
        except Exception as exc:
            self.logger.debug(f"Could not create {self._sessions_dir()}: {exc}")

    def _engine_process_env(self) -> dict[str, str]:
        """Auth and model env for the engine's own SDK sessions.

        Deliberately narrow: only what the SDK needs to reach the API. Keys are
        never echoed into the command line or the stdout log.
        """
        env = {
            "ANTHROPIC_API_KEY": self._get_env("ANTHROPIC_API_KEY") or "",
            "ANTHROPIC_AUTH_TOKEN": self._get_env("ANTHROPIC_AUTH_TOKEN") or "",
            "ANTHROPIC_BASE_URL": self._get_env("ANTHROPIC_BASE_URL") or "",
            "CLAUDE_CODE_OAUTH_TOKEN": self._get_env("CLAUDE_CODE_OAUTH_TOKEN") or "",
            # The engine shells out to the Claude Code CLI via the SDK; it is
            # installed under ~/.local/bin by the parent's install step.
            "PATH": "$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin",
            # Put the stage transcripts on the mounted volume. Without this the
            # CLI writes them under the container's own $HOME, where they are
            # destroyed on teardown — and _get_session_dirs, which exists to
            # merge exactly these, finds nothing. The parent sets the same
            # variable, but in its run(), which this class overrides.
            #
            # This is not a nicety: token counts, step counts and cost come off
            # the transcripts, and losing them costs the *staged* arm the
            # evaluation's primary measures while the single-session baseline
            # keeps them — an asymmetry sitting on the variable under test.
            "CLAUDE_CONFIG_DIR": self._sessions_dir(),
        }
        if self.model_name:
            env["ANTHROPIC_MODEL"] = self.model_name.split("/")[-1]
        if self.dangerously_skip_permissions:
            # DeepSWE task containers run the agent as root, and Claude Code
            # refuses bypass mode under root: "--dangerously-skip-permissions
            # cannot be used with root/sudo privileges for security reasons".
            # Without this the pipeline dies in stage 1 with an empty patch.
            #
            # IS_SANDBOX is the escape hatch for precisely this case. The guard
            # exists to stop someone auto-approving every tool call as root on
            # their own machine; a disposable, single-task, network-restricted
            # container is the situation it is meant to permit. Scoped to the
            # explicit opt-in so it can never widen anything on its own.
            env["IS_SANDBOX"] = "1"
        env = self.build_process_env(env, include_resolved_env=False)
        return {k: v for k, v in env.items() if v}

    async def _guard_workspace(self, environment: BaseEnvironment) -> None:
        """Keep stray engine artifacts out of the graded diff (see ENGINE_ARTIFACTS)."""
        patterns = "\\n".join(ENGINE_ARTIFACTS)
        command = (
            f"cd {shlex.quote(self.repo_dir)} && "
            "mkdir -p .git/info && "
            f'printf "%b\\n" {shlex.quote(patterns)} >> .git/info/exclude'
        )
        try:
            await self.exec_as_agent(environment, command)
        except Exception as exc:  # never fail a run over a hygiene guard
            self.logger.debug(f"Could not write .git/info/exclude: {exc}")

    async def _ensure_committed(self, environment: BaseEnvironment) -> None:
        """Commit residual work, so a pipeline that forgets does not score zero."""
        repo = shlex.quote(self.repo_dir)
        command = (
            f"cd {repo} && "
            f"git config --global --add safe.directory {repo} && "
            'git config user.email "orchestra-engine@invalid" && '
            'git config user.name "orchestra-engine" && '
            'if [ -n "$(git status --porcelain)" ]; then '
            '  echo "--- autocommitting ---"; git status --porcelain; '
            '  git add -A && git commit -q -m "orchestra-engine: residual changes"; '
            'else echo "worktree clean"; fi'
        )
        try:
            result = await self.exec_as_agent(environment, command)
            self.logger.debug(f"autocommit: {getattr(result, 'stdout', '')}")
        except Exception as exc:
            self.logger.warning(f"Autocommit failed; graded diff may be empty: {exc}")

    async def _collect_stage_artifacts(self, environment: BaseEnvironment) -> None:
        """Pull the pipeline's stage documents out before the container dies.

        SCOPE.md, DESIGN.md, TESTREPORT.md, VERIFY.md and decision.json are the
        record of what the run actually decided, and they live in ``--runs-dir``
        inside the container — which nothing mounts and the task's collect hook
        does not touch. Without this they are destroyed on teardown, and a run
        can only be diagnosed from its diff, which does not say *why*.

        This matters most on the path where the run timed out or raised: a
        truncated pipeline is exactly the one you cannot reconstruct from the
        patch alone. Never fatal — losing the logs is bad, failing the run over
        losing them is worse.
        """
        target = self.logs_dir / "loop-run"
        try:
            await environment.download_dir(self.engine_runs_dir, target)
        except Exception as exc:
            self.logger.debug(f"Could not collect stage artifacts: {exc}")

    def _write_stdout_log(self) -> None:
        if not self._stdout:
            return
        try:
            (self.logs_dir / "orchestra-engine-stdout.log").write_text(
                self._stdout, encoding="utf-8"
            )
        except OSError as exc:
            self.logger.debug(f"Could not write stdout log: {exc}")

    # ---- trajectory ----------------------------------------------------

    def _get_session_dirs(self) -> list[Path]:
        """Every Claude session directory, not just the unambiguous one.

        This is the reason this method exists. The parent's ``_get_session_dir``
        returns ``None`` the moment it finds more than one session directory —
        reasonable for a single ``claude -p`` invocation, wrong for us: the loop
        engine runs *each stage as its own session*, so a six-stage pipeline
        always produces at least six. Inheriting the parent's behaviour would
        silently yield no trajectory and no token/cost metrics at all, which are
        the evaluation's primary measures.
        """
        sessions_root = self.logs_dir / "sessions" / "projects"
        if not sessions_root.is_dir():
            return []

        session_dirs: set[Path] = set()
        for project_dir in sessions_root.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl in project_dir.rglob("*.jsonl"):
                if "subagents" in jsonl.parent.parts:
                    continue
                session_dirs.add(jsonl.parent)
        return sorted(session_dirs)

    @staticmethod
    def _merge_final_metrics(metrics: list[FinalMetrics]) -> FinalMetrics | None:
        """Sum per-stage metrics into one run-level total."""
        if not metrics:
            return None
        totals: dict[str, float] = {}
        for m in metrics:
            for field, value in m.model_dump().items():
                if isinstance(value, (int, float)) and value is not None:
                    totals[field] = totals.get(field, 0) + value
        # Token and step counts are integers; cost is not.
        coerced: dict[str, Any] = {
            k: (v if k.endswith("_usd") else int(v)) for k, v in totals.items()
        }
        return FinalMetrics(**coerced)

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Merge the per-stage session trajectories into one run trajectory."""
        session_dirs = self._get_session_dirs()
        if not session_dirs:
            self.logger.debug("No Claude Code session directories found")
            return

        steps: list[Any] = []
        metrics: list[FinalMetrics] = []
        merged: Trajectory | None = None

        for session_dir in session_dirs:
            try:
                trajectory = self._convert_events_to_trajectory(session_dir)
            except Exception as exc:
                # One unreadable stage must not cost us the other five.
                self.logger.debug(f"Skipping session {session_dir}: {exc}")
                continue
            if not trajectory:
                continue
            merged = merged or trajectory
            steps.extend(trajectory.steps or [])
            if trajectory.final_metrics:
                metrics.append(trajectory.final_metrics)

        if merged is None:
            self.logger.debug("No session converted to a trajectory")
            return

        totals = self._merge_final_metrics(metrics)
        if totals is not None:
            # The field contract says total_steps must match the trajectory's
            # own step count or be explained in notes; after merging, it does.
            totals = totals.model_copy(update={"total_steps": len(steps)})

        # Rebuild rather than mutate, for two reasons. ``Trajectory`` validates
        # that step_ids are sequential from 1, and each stage session numbers
        # its own steps from 1 — so concatenating six stages yields 1,2,3,1,2,3
        # and has to be renumbered. And that validator only runs on
        # construction, so assigning to ``merged.steps`` would skip it and emit
        # a silently invalid trajectory instead of failing here.
        data = merged.model_dump()
        data["steps"] = [
            {**step.model_dump(), "step_id": index}
            for index, step in enumerate(steps, start=1)
        ]
        data["final_metrics"] = totals.model_dump() if totals else None
        merged_trajectory = Trajectory(**data)

        trajectory_path = self.logs_dir / "trajectory.json"
        try:
            with open(trajectory_path, "w", encoding="utf-8") as handle:
                json.dump(
                    merged_trajectory.to_json_dict(),
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )
            self.logger.debug(
                f"Wrote merged trajectory ({len(session_dirs)} stage sessions, "
                f"{len(steps)} steps) to {trajectory_path}"
            )
        except OSError as exc:
            self.logger.debug(f"Failed to write {trajectory_path}: {exc}")

        if merged_trajectory.final_metrics:
            populate_context_from_final_metrics(
                context, merged_trajectory.final_metrics
            )
