"""Tests for the Pier agent adapter (eval/feature/deepswe/pier_agent.py).

Pier is not a dependency of this engine — it is only needed to *benchmark* the
engine — so the whole module skips when it is absent. Install it with
``uv tool install git+https://github.com/datacurve-ai/pier`` (or
``pip install <path-to-pier-checkout>``) to run these.
"""

import json
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("pier", reason="Pier is only needed for benchmark runs")

from pier.models.trajectories import (  # noqa: E402
    Agent,
    FinalMetrics,
    Step,
    Trajectory,
)

from eval.feature.deepswe.pier_agent import (
    LOCAL_SRC_TARGET,
    OrchestraEngineAgent,
)

LOGS = Path("/tmp/orchestra-engine-pier-tests")


def test_bypass_without_explicit_skip_is_refused():
    """An unattended run would block on the confirm prompt until it times out."""
    with pytest.raises(ValueError, match="dangerously_skip_permissions"):
        OrchestraEngineAgent(LOGS, permission_mode="bypassPermissions")


def test_bypass_allowed_when_asked_for_explicitly():
    agent = OrchestraEngineAgent(
        LOGS, permission_mode="bypassPermissions", dangerously_skip_permissions=True
    )
    assert "--dangerously-skip-permissions" in agent._build_command("t")


@pytest.mark.parametrize("falsey", ["false", "False", "0", "no", "off", ""])
def test_string_false_from_cli_does_not_enable_bypass(falsey):
    """``--ak dangerously_skip_permissions=false`` must not be truthy.

    Pier passes agent kwargs through as strings, and ``bool("false")`` is True —
    which would silently auto-approve every tool call.
    """
    with pytest.raises(ValueError, match="dangerously_skip_permissions"):
        OrchestraEngineAgent(
            LOGS,
            permission_mode="bypassPermissions",
            dangerously_skip_permissions=falsey,
        )


@pytest.mark.parametrize("truthy", ["true", "True", "1", "yes", "on"])
def test_string_true_from_cli_enables_bypass(truthy):
    agent = OrchestraEngineAgent(
        LOGS,
        permission_mode="bypassPermissions",
        dangerously_skip_permissions=truthy,
    )
    assert "--dangerously-skip-permissions" in agent._build_command("t")


def test_command_targets_the_repo_and_keeps_artifacts_out_of_it():
    agent = OrchestraEngineAgent(LOGS)
    cmd = agent._build_command("do the thing")
    assert "--pipeline feature" in cmd
    assert "--workspace /app" in cmd
    # Stage documents must not land in the graded tree.
    assert "--runs-dir /logs/loop-run" in cmd


def test_command_is_non_interactive():
    """No wizard and no stage gate: nothing can answer a prompt in a container."""
    cmd = OrchestraEngineAgent(LOGS)._build_command("t")
    assert " -y " in cmd
    assert " -i " not in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_instruction_is_shell_quoted():
    """Task text is untrusted input interpolated into a shell command."""
    hostile = "Add a thing; rm -rf / $(whoami) `id`"
    cmd = OrchestraEngineAgent(LOGS)._build_command(hostile)
    assert f"'{hostile}'" in cmd


def test_model_name_loses_its_provider_prefix():
    agent = OrchestraEngineAgent(LOGS, model_name="anthropic/claude-opus-5")
    assert "--model claude-opus-5" in agent._build_command("t")


def test_sandbox_flag_set_only_when_bypassing_permissions():
    """Claude Code refuses bypass mode as root, and task containers run as root.

    Verified in-container: without IS_SANDBOX the CLI exits with
    "--dangerously-skip-permissions cannot be used with root/sudo privileges"
    and the pipeline dies in stage 1 with an empty patch.
    """
    bypassing = OrchestraEngineAgent(
        LOGS, permission_mode="bypassPermissions", dangerously_skip_permissions=True
    )
    assert bypassing._engine_process_env().get("IS_SANDBOX") == "1"

    # Never widen the sandbox on its own — only alongside the explicit opt-in.
    assert "IS_SANDBOX" not in OrchestraEngineAgent(LOGS)._engine_process_env()


def test_no_container_timeout_wrapper_by_default():
    assert not OrchestraEngineAgent(LOGS)._build_command("t").startswith("timeout")


def test_container_timeout_wraps_the_command():
    """Turns Pier's cancelling timeout into an ordinary non-zero exit.

    Pier times agents out with ``asyncio.wait_for``, which cancels the coroutine
    — and a cancelled coroutine cannot reliably await the autocommit in run()'s
    ``finally``, so a 90-minute trial's work would be lost. A container-side
    timeout lets the command return normally instead.
    """
    cmd = OrchestraEngineAgent(LOGS, agent_timeout_sec=5200)._build_command("t")
    assert cmd.startswith("timeout -k 30 5200 ")
    assert "-m orchestra_engine" in cmd


def test_container_timeout_accepts_a_string_from_the_cli():
    cmd = OrchestraEngineAgent(LOGS, agent_timeout_sec="5200")._build_command("t")
    assert cmd.startswith("timeout -k 30 5200 ")


def test_defaults_to_installing_this_checkout():
    """A git spec would measure whatever was last pushed, not the working tree."""
    agent = OrchestraEngineAgent(LOGS)
    assert agent._is_local_source()
    assert (Path(agent.engine_spec) / "orchestra_engine").is_dir()


def test_a_pip_spec_is_not_treated_as_a_local_checkout():
    agent = OrchestraEngineAgent(LOGS, engine_spec="git+https://example.invalid/x.git")
    assert not agent._is_local_source()


def test_build_time_install_never_references_uploaded_source():
    """Regression: Pier inlines install_spec() into the Dockerfile.

    Those steps run at image *build* time, before anything is uploaded, so an
    engine install placed there fails with "engine source not found in
    container". The engine install belongs in setup(), at runtime.
    """
    for agent in (
        OrchestraEngineAgent(LOGS),
        OrchestraEngineAgent(LOGS, engine_spec="git+https://example.invalid/x.git"),
    ):
        build_steps = " ".join(s.run for s in agent.install_spec().steps)
        assert LOCAL_SRC_TARGET not in build_steps
        assert "orchestra_engine" not in build_steps
        assert "loop-venv" not in build_steps


def test_staging_carries_the_package_and_nothing_else():
    """Uploading the checkout wholesale would drag in .venv, .git and runs/."""
    source = Path(OrchestraEngineAgent(LOGS).engine_spec)
    with tempfile.TemporaryDirectory() as staging:
        OrchestraEngineAgent._stage_package(source, Path(staging))
        staged = Path(staging)
        names = {p.name for p in staged.iterdir()}
        assert "pyproject.toml" in names
        assert "orchestra_engine" in names
        assert not names & {".venv", ".git", "runs", "workspace", "orchestra-docs"}
        # The pipelines are package-data; an engine without them cannot run.
        assert list((staged / "orchestra_engine" / "pipelines").rglob("*.md"))
        assert not list(staged.rglob("__pycache__"))


def test_staging_rejects_a_directory_that_is_not_the_engine():
    with tempfile.TemporaryDirectory() as notengine:
        with tempfile.TemporaryDirectory() as staging:
            with pytest.raises(RuntimeError, match="does not look like"):
                OrchestraEngineAgent._stage_package(Path(notengine), Path(staging))


def test_metrics_merge_sums_across_stages():
    agent = OrchestraEngineAgent(LOGS)
    merged = agent._merge_final_metrics(
        [
            FinalMetrics(total_prompt_tokens=100, total_cost_usd=0.5, total_steps=3),
            FinalMetrics(total_prompt_tokens=250, total_cost_usd=1.25, total_steps=4),
        ]
    )
    assert merged.total_prompt_tokens == 350
    assert isinstance(merged.total_prompt_tokens, int)
    assert merged.total_cost_usd == pytest.approx(1.75)
    assert agent._merge_final_metrics([]) is None


def _stage(n_steps, cost):
    return Trajectory(
        agent=Agent(name="orchestra-engine", version="0.2.0"),
        steps=[
            Step(step_id=i + 1, source="agent", message=f"step {i + 1}")
            for i in range(n_steps)
        ],
        final_metrics=FinalMetrics(
            total_steps=n_steps, total_cost_usd=cost, total_prompt_tokens=10 * n_steps
        ),
    )


def test_multi_stage_sessions_merge_into_one_valid_trajectory():
    """The reason this adapter overrides the parent at all.

    Each stage is its own Claude session, so a run yields several session
    directories each numbering its steps from 1. Pier's ``Trajectory`` requires
    step_ids sequential from 1 across the whole document, so the merge has to
    renumber — and the parent agent would have produced no trajectory here at
    all, since it bails whenever it finds more than one session directory.
    """
    stages = [_stage(3, 1.0), _stage(2, 2.0), _stage(4, 0.5)]

    class FakeSessions(OrchestraEngineAgent):
        def _get_session_dirs(self):
            return [Path(f"/fake/{i}") for i in range(len(stages))]

        def _convert_events_to_trajectory(self, session_dir):
            return stages[int(session_dir.name)]

    with tempfile.TemporaryDirectory() as td:
        agent = FakeSessions(Path(td))
        agent.populate_context_post_run(type("Ctx", (), {})())

        written = Path(td) / "trajectory.json"
        assert written.exists()
        data = json.loads(written.read_text())

        assert [s["step_id"] for s in data["steps"]] == list(range(1, 10))
        assert data["final_metrics"]["total_cost_usd"] == pytest.approx(3.5)
        assert data["final_metrics"]["total_steps"] == 9
        # Must be a legal ATIF document, not merely written.
        Trajectory(**data)


def test_one_unreadable_stage_does_not_lose_the_others():
    stages = [_stage(2, 1.0), None, _stage(3, 1.0)]

    class PartlyBroken(OrchestraEngineAgent):
        def _get_session_dirs(self):
            return [Path(f"/fake/{i}") for i in range(len(stages))]

        def _convert_events_to_trajectory(self, session_dir):
            index = int(session_dir.name)
            if stages[index] is None:
                raise RuntimeError("corrupt session log")
            return stages[index]

    with tempfile.TemporaryDirectory() as td:
        agent = PartlyBroken(Path(td))
        agent.populate_context_post_run(type("Ctx", (), {})())
        data = json.loads((Path(td) / "trajectory.json").read_text())
        assert [s["step_id"] for s in data["steps"]] == [1, 2, 3, 4, 5]


class _ChainRecorder:
    """Stands in for ``exec_as_agent``: records commands, answers the SPEC lookup."""

    def __init__(self, spec: str = "", fail: str | None = None):
        self.commands: list[str] = []
        self.spec = spec
        self.fail = fail

    async def __call__(self, environment, command, env=None, cwd=None):
        self.commands.append(command)
        if self.fail and f"--pipeline {self.fail} " in command:
            raise RuntimeError(f"{self.fail} exited 124")
        stdout = self.spec if command.startswith("ls -t") else "ok"
        return type("Result", (), {"stdout": stdout})()


def _run_chain(agent, recorder):
    import asyncio

    agent.exec_as_agent = recorder
    asyncio.run(agent._run_chain("Fix the thing", None, {}))
    return [c for c in recorder.commands if "-m orchestra_engine" in c]


def test_chain_hands_the_spec_downstream():
    spec = "/logs/loop-run/docs/fix-the-thing/SPEC.md"
    agent = OrchestraEngineAgent(LOGS, pipeline="approach+feature")
    approach, feature = _run_chain(agent, _ChainRecorder(spec=spec + "\n"))
    assert "--pipeline approach " in approach
    assert "--pipeline feature " in feature
    assert spec not in approach
    assert spec in feature and "Fix the thing" in feature
    assert "--docs-dir /logs/loop-run/docs" in approach


def test_chain_without_a_spec_runs_downstream_on_the_bare_ticket():
    agent = OrchestraEngineAgent(LOGS, pipeline="approach+feature")
    _, feature = _run_chain(agent, _ChainRecorder(spec=""))
    assert "Fix the thing" in feature
    assert "earlier `approach` run" not in feature


def test_chain_gives_the_first_link_a_third_of_the_budget():
    agent = OrchestraEngineAgent(
        LOGS, pipeline="approach+feature", agent_timeout_sec=21000
    )
    approach, feature = _run_chain(agent, _ChainRecorder())
    first = int(approach.split()[3])
    assert 6900 <= first <= 7000
    assert int(feature.split()[3]) > first


def test_chain_survives_an_upstream_failure():
    agent = OrchestraEngineAgent(LOGS, pipeline="approach+feature")
    commands = _run_chain(agent, _ChainRecorder(fail="approach"))
    assert "--pipeline feature " in commands[-1]
    assert "[approach failed" in agent._stdout


def test_single_pipeline_gets_no_docs_dir():
    assert "--docs-dir" not in OrchestraEngineAgent(LOGS)._build_command("t")


def test_local_engine_installs_offline_from_a_build_time_wheelhouse():
    """The agent container reaches only the model API, so PyPI is a 403 there.

    Dependencies are downloaded while the image builds, which has network, and
    the runtime install must not touch an index at all.
    """
    from eval.feature.deepswe.pier_agent import WHEELHOUSE

    build_steps = " ".join(s.run for s in OrchestraEngineAgent(LOGS).install_spec().steps)
    assert f"pip download -q -d {WHEELHOUSE}" in build_steps
    assert "claude-agent-sdk" in build_steps and "setuptools" in build_steps
