import asyncio

import pytest

from orchestra_engine.pipeline import load_pipeline


@pytest.fixture(autouse=True)
def no_app_window(monkeypatch):
    """No test opens a real window on the desktop; the page goes to the
    (patched or harmless) browser path instead. A test about the window
    patches open_window back in."""
    from orchestra_engine import window

    monkeypatch.setattr(window, "open_window", lambda run_id=None: False)


@pytest.fixture
def research():
    return load_pipeline("research")


@pytest.fixture
def run():
    """Run a coroutine to completion on a fresh event loop."""

    def _run(coro):
        return asyncio.run(coro)

    return _run


@pytest.fixture
def logs():
    return []


@pytest.fixture
def stub(monkeypatch):
    """Run a pipeline with the agent session stubbed out.

    Returns (calls, results): what each stage was asked to do, and what
    run_loop produced. No API calls.
    """
    import orchestra_engine.engine.executor as executor
    from orchestra_engine.engine import run_loop
    from orchestra_engine.model import StageResult

    def _stub(pipeline, workspace, run, log=None, task="T", runs_dir=None, on_stage=None,
              max_hops=None):
        """on_stage(stage_name, artifacts) runs as each stage does, so a test can
        plant the files a real agent would leave behind for a later stage."""
        calls = []

        async def fake_run_stage(stage_name, prompt, ws, artifacts, *, label=None,
                                 iteration=None, item=None, log=print, **kwargs):
            # handoff: what the read_handoff tool would serve this stage. It is
            # no longer in the prompt, so a test asserting on what a stage can
            # see from its predecessors asserts on this.
            calls.append({
                "stage": stage_name,
                "prompt": prompt,
                "item": item,
                "handoff": kwargs.get("handoff_body") or "",
                # What the engine adds on top of the stage's own prompt —
                # transcript access, and whether routing is still open.
                "extra_context": kwargs.get("extra_context") or "",
            })
            if on_stage:
                on_stage(stage_name, artifacts)
            return StageResult(
                stage=stage_name,
                session_id=f"sess-{len(calls)}",
                summary=f"summary of {stage_name}",
                is_error=False,
            )

        monkeypatch.setattr(executor, "run_chain", _chain_via(fake_run_stage))
        results = run(
            run_loop(
                task=task,
                workspace=workspace,
                pipeline=pipeline,
                runs_dir=runs_dir or workspace.parent / "runs",
                max_hops=max_hops,
                log=log or (lambda _: None),
            )
        )
        return calls, results

    def _chain_via(fake):
        async def run_chain(stage, pipeline, task, workspace, artifacts, prev, **kwargs):
            from orchestra_engine import handoff_tool
            from orchestra_engine.model import Handoff

            handoffs = [Handoff.from_result(prev)] if prev else []
            prompt = pipeline.build_prompt(
                stage, task, handoffs=handoffs, artifacts=str(artifacts)
            )
            # Same split as the real chain: pointer in the prompt, reports to
            # the tool. A double that skipped this would make every stage look
            # like it had been handed nothing.
            body = (
                handoff_tool.render(handoffs, pipeline.handoff_template)
                if handoffs
                else None
            )
            return [
                await fake(
                    stage.name,
                    prompt,
                    workspace,
                    artifacts,
                    handoff_body=body,
                    extra_context=kwargs.get("extra_context"),
                )
            ]

        return run_chain

    return _stub
