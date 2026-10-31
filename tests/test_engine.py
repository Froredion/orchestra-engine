"""Execution layer: run_loop behavior with the SDK session runner stubbed out."""

import json
from pathlib import Path

import pytest

import orchestra_engine.engine.shapes as shapes
from orchestra_engine import handoff_tool
from orchestra_engine.engine import run_loop
from orchestra_engine.engine.runlog import format_duration
from orchestra_engine.model import StageResult


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0s"),
        (4, "4s"),
        (59.4, "59s"),
        (59.6, "1m 0s"),  # rounds up across the minute boundary
        (65, "1m 5s"),
        (3600, "1h 0m 0s"),  # once hours appear, minutes show even at zero
        (3725, "1h 2m 5s"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def make_fake_run_stage(calls, concurrency, fail_items=()):
    """A run_stage stand-in: records calls, tracks concurrency, writes leads.txt
    when playing the findings stage, and errors for items in fail_items."""

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, **kwargs):
        import asyncio

        concurrency["now"] += 1
        concurrency["peak"] = max(concurrency["peak"], concurrency["now"])
        await asyncio.sleep(0.01)
        calls.append({
            "stage": stage_name,
            "prompt": prompt,
            "handoff": kwargs.get("handoff_body") or "",
            "iteration": iteration,
            "item": item,
            "workspace": workspace,
            "artifacts": artifacts,
        })
        if stage_name == "findings":
            (artifacts / "leads.txt").write_text(
                "https://a.com\nhttps://b.com\n\n# comment\nhttps://c.com\n"
                "https://d.com\nhttps://e.com\nhttps://f.com\n"
            )
        concurrency["now"] -= 1
        failed = item in fail_items
        return StageResult(
            stage=stage_name,
            session_id=f"sess-{len(calls)}",
            summary="boom"
            if failed
            else "summary of " + stage_name
            + (f" pass {iteration}" if iteration else "")
            + (f" for {item}" if item else ""),
            is_error=failed,
            cost_usd=0.01,
            num_turns=1,
            duration_ms=5,
            iteration=iteration,
            item=item,
        )

    return fake


@pytest.fixture
def stub(monkeypatch):
    """Install the fake session runner; returns (calls, concurrency) recorders."""

    def _install(fail_items=()):
        calls, concurrency = [], {"now": 0, "peak": 0}
        monkeypatch.setattr(
            shapes, "run_stage", make_fake_run_stage(calls, concurrency, fail_items)
        )
        return calls, concurrency

    return _install


def run_research(run, research, tmp_path, runs_dir=None):
    return run(
        run_loop(
            task="research topic T",
            workspace=tmp_path / "ws",
            pipeline=research,
            runs_dir=runs_dir or tmp_path / "runs",
            log=lambda line: None,
        )
    )


def test_repeat_threads_handoff_between_passes(run, research, tmp_path, stub):
    calls, _ = stub()
    run_research(run, research, tmp_path)
    search = [c for c in calls if c["stage"] == "search"]
    assert len(search) == 3
    assert "HANDOFF" not in search[0]["prompt"]  # first session of the run
    assert search[0]["handoff"] == ""  # and nothing for the tool to serve
    # The reports reach a stage through read_handoff, not the prompt; the prompt
    # only names who reported.
    assert "summary of search pass 1" in search[1]["handoff"]
    assert "search (pass 1/3)" in search[1]["prompt"]
    assert "summary of search pass 1" not in search[1]["prompt"]
    assert "summary of search pass 2" in search[2]["handoff"]
    findings = [c for c in calls if c["stage"] == "findings"]
    assert len(findings) == 1 and "summary of search pass 3" in findings[0]["handoff"]


def test_cumulative_handoff_accumulates_every_prior_pass(run, research, tmp_path, stub):
    """search declares handoff: all — pass 3 sees passes 1 AND 2, with both
    session IDs, not just the newest one."""
    calls, _ = stub()
    run_research(run, research, tmp_path)
    search = [c for c in calls if c["stage"] == "search"]

    pass3 = search[2]["handoff"]
    assert "summary of search pass 1" in pass3
    assert "summary of search pass 2" in pass3
    assert "sess-1" in pass3 and "sess-2" in pass3  # both transcripts reachable
    assert pass3.count("--- END HANDOFF ---") == 2
    assert pass3.index("sess-1") < pass3.index("sess-2")  # oldest first
    # and the pointer names both, so the stage knows there are two to read
    assert search[2]["prompt"].count("search (pass") == 2

    assert search[1]["handoff"].count("--- END HANDOFF ---") == 1  # pass 2: only pass 1 yet


def test_last_handoff_scope_shows_only_the_previous_pass(run, research, tmp_path, stub):
    """deep-dive uses the default handoff: last — pass 2 sees pass 1 only, not
    the findings stage that preceded the chain."""
    calls, _ = stub()
    run_research(run, research, tmp_path)
    a = [c for c in calls if c["item"] == "https://a.com"]
    assert "summary of findings" in a[0]["handoff"]  # pass 1 gets the incoming stage
    assert "summary of findings" not in a[1]["handoff"]  # pass 2 does not
    assert a[1]["handoff"].count("--- END HANDOFF ---") == 1


def test_fan_out_caps_items_and_chains_passes(run, research, tmp_path, stub):
    calls, concurrency = stub()
    run_research(run, research, tmp_path)
    deep = [c for c in calls if c["stage"] == "deep-dive"]
    items = sorted({c["item"] for c in deep})
    # 6 leads written, comment/blank dropped, capped at max_items=5
    assert items == [f"https://{x}.com" for x in "abcde"]
    assert len(deep) == 10  # 5 items x 2 passes
    a = [c for c in deep if c["item"] == "https://a.com"]
    assert "summary of findings" in a[0]["handoff"]
    assert "summary of deep-dive pass 1 for https://a.com" in a[1]["handoff"]
    assert 2 <= concurrency["peak"] <= 5  # parallel, but bounded


def test_fan_out_merges_summaries_for_next_stage(run, research, tmp_path, stub):
    calls, _ = stub()
    run_research(run, research, tmp_path)
    (report,) = [c for c in calls if c["stage"] == "report"]
    assert "Parallel results for 5 items" in report["handoff"]
    assert "### https://a.com [ok]" in report["handoff"]
    assert "### https://e.com [ok]" in report["handoff"]


def test_every_stage_gets_prior_session_transcripts(run, research, tmp_path, monkeypatch):
    """Reaching prior sessions' transcripts is universal, not router-only: a
    repeat pass, a fan-out item, and the terminal stage all receive the index
    block *and* the read-access dir that makes its paths openable."""
    import orchestra_engine.engine.executor as executor
    from pathlib import Path

    # Stand in for on-disk transcripts so the block is always populated, without
    # touching the real ~/.claude/projects. The engine derives add_dirs from the
    # path's parent, so a fake path is enough to exercise the wiring.
    canned = [{"stage": "search", "session_id": "sess-x",
               "path": Path("/fake/projects/slug/sess-x.jsonl")}]
    monkeypatch.setattr(executor, "_resolve_transcripts", lambda results: canned)

    calls = []

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, extra_add_dirs=None, **kwargs):
        calls.append({"stage": stage_name, "prompt": prompt, "item": item,
                      "handoff": kwargs.get("handoff_body") or "",
                      "iteration": iteration, "extra_add_dirs": extra_add_dirs})
        if stage_name == "findings":
            (artifacts / "leads.txt").write_text("https://a.com\nhttps://b.com\n")
        return StageResult(stage=stage_name, session_id=f"sess-{len(calls)}",
                           summary=f"summary of {stage_name}", is_error=False)

    monkeypatch.setattr(shapes, "run_stage", fake)
    run_research(run, research, tmp_path)

    def assert_reachable(call):
        assert "--- PRIOR SESSION TRANSCRIPTS ---" in call["prompt"]
        assert "Extract the fact, not the file." in call["prompt"]
        assert str(Path("/fake/projects/slug/sess-x.jsonl")) in call["prompt"]
        assert call["extra_add_dirs"] == [str(Path("/fake/projects/slug"))]

    # search (repeat), deep-dive (fan-out), understand (terminal) are the three
    # non-router shapes that received nothing before this change.
    assert_reachable(next(c for c in calls if c["stage"] == "search" and c["iteration"] == 3))
    assert_reachable(next(c for c in calls if c["stage"] == "deep-dive"))
    assert_reachable(next(c for c in calls if c["stage"] == "understand"))


def test_handoff_none_withholds_reports_and_transcripts(run, tmp_path, monkeypatch):
    """A `handoff: none` stage starts from the request and the workspace: no
    predecessor summary through read_handoff, and no transcript index either.

    Withholding one and not the other would be a barrier in name only — the
    summaries are drawn from the transcripts, so a stage handed the paths can
    reconstruct exactly the reading it was meant to form independently.
    """
    import orchestra_engine.engine.executor as executor
    from orchestra_engine.model import Pipeline, Stage

    canned = [{"stage": "first", "session_id": "sess-x",
               "path": Path("/fake/projects/slug/sess-x.jsonl")}]
    monkeypatch.setattr(executor, "_resolve_transcripts", lambda results: canned)

    calls = []

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, extra_add_dirs=None, **kwargs):
        calls.append({"stage": stage_name, "prompt": prompt,
                      "handoff": kwargs.get("handoff_body") or "",
                      "extra_add_dirs": extra_add_dirs})
        return StageResult(stage=stage_name, session_id=f"sess-{len(calls)}",
                           summary=f"summary of {stage_name}", is_error=False)

    monkeypatch.setattr(shapes, "run_stage", fake)

    pipeline = Pipeline(
        name="t",
        stages=(
            Stage(name="first", prompt_template="do {task}\n{handoff}"),
            Stage(name="alone", prompt_template="do {task}\n{handoff}", handoff="none"),
            Stage(name="after", prompt_template="do {task}\n{handoff}"),
        ),
        handoff_template="{summary}",
    )
    run(run_loop("T", tmp_path, pipeline, tmp_path / "runs", log=lambda *a, **k: None))

    alone = next(c for c in calls if c["stage"] == "alone")
    assert alone["handoff"] == ""
    assert "summary of first" not in alone["prompt"]
    assert "--- PRIOR SESSION TRANSCRIPTS ---" not in alone["prompt"]
    assert alone["extra_add_dirs"] == []

    # The isolation is scoped to the declaring stage: the next one sees normally,
    # and what it sees is the isolated stage's own report.
    after = next(c for c in calls if c["stage"] == "after")
    assert "summary of alone" in after["handoff"]
    assert "--- PRIOR SESSION TRANSCRIPTS ---" in after["prompt"]


def test_handoff_none_rerun_cannot_see_its_own_earlier_document(run, tmp_path, monkeypatch):
    """A route-back re-runs a blind stage, and its own previous document is the
    one file that would defeat the barrier.

    Withholding the other stages' reports while leaving the stage's own prior
    reading in the directory it writes to makes the second pass an edit of the
    first. The document is kept — comparing the passes is the point of having
    two — but it leaves artifacts/, which is the only directory the stage is
    granted.
    """
    from orchestra_engine.model import Pipeline, Stage

    saw_on_entry = []

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, extra_add_dirs=None, **kwargs):
        reading = artifacts / ".reading.md"
        if stage_name == "alone":
            saw_on_entry.append(reading.read_text() if reading.exists() else None)
            reading.write_text(f"reading #{len(saw_on_entry)}")
        if stage_name == "router":
            # Route back once, then let the run advance to the terminal.
            nxt = "alone" if len(saw_on_entry) == 1 else "advance"
            (artifacts / "decision.json").write_text(json.dumps({"next": nxt}))
        return StageResult(stage=stage_name, session_id=None,
                           summary=f"summary of {stage_name}", is_error=False)

    monkeypatch.setattr(shapes, "run_stage", fake)

    pipeline = Pipeline(
        name="t",
        stages=(
            Stage(name="alone", prompt_template="do {task}\n{handoff}", handoff="none"),
            Stage(name="router", prompt_template="do {task}\n{handoff}",
                  decide_targets=("alone",)),
            Stage(name="last", prompt_template="do {task}\n{handoff}"),
        ),
        handoff_template="{summary}",
    )
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, pipeline, runs_dir, log=lambda *a, **k: None))

    # It ran twice, and started from nothing both times.
    assert saw_on_entry == [None, None]

    (run_dir,) = runs_dir.iterdir()
    assert (run_dir / "artifacts" / ".reading.md").read_text() == "reading #2"
    # Superseded, not destroyed — and outside the directory the stage can read.
    assert (run_dir / "superseded" / "alone-1" / ".reading.md").read_text() == "reading #1"


def test_handoff_none_cannot_see_other_stages_documents(run, tmp_path, monkeypatch):
    """A blind stage is handed a directory full of the reports it must not read.

    Withholding the summaries and the session pointers while leaving SCOPE.md
    and DESIGN.md in the one directory the stage writes to makes the barrier a
    convention rather than a barrier. The documents leave for the duration and
    come back after, because every later stage is meant to read them.
    """
    from orchestra_engine.model import Pipeline, Stage

    saw = []

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, extra_add_dirs=None, **kwargs):
        if stage_name == "plan":
            (artifacts / "PLAN.md").write_text("the plan")
        if stage_name == "alone":
            saw.append(sorted(p.name for p in artifacts.iterdir()))
            (artifacts / ".reading.md").write_text("read independently")
        return StageResult(stage=stage_name, session_id=None,
                           summary=f"summary of {stage_name}", is_error=False)

    monkeypatch.setattr(shapes, "run_stage", fake)

    pipeline = Pipeline(
        name="t",
        stages=(
            Stage(name="plan", prompt_template="do {task}\n{handoff}"),
            Stage(name="alone", prompt_template="do {task}\n{handoff}", handoff="none"),
            Stage(name="last", prompt_template="do {task}\n{handoff}"),
        ),
        handoff_template="{summary}",
    )
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, pipeline, runs_dir, log=lambda *a, **k: None))

    # The earlier stage's document was not in reach while the blind stage ran.
    assert saw == [[]]

    (run_dir,) = runs_dir.iterdir()
    artifacts = run_dir / "artifacts"
    # Withheld, not taken: it is back for every stage after the blind one.
    assert (artifacts / "PLAN.md").read_text() == "the plan"
    assert (artifacts / ".reading.md").read_text() == "read independently"


def test_rerun_stage_keeps_its_earlier_document_for_comparison(run, tmp_path, monkeypatch):
    """A stage sent back rewrites its document in place, erasing the version a
    later stage would need to see what the revision changed.

    The rewrite lands on the same filename, so without a snapshot there is no
    way to ask whether the second pass delivered less than the first — which is
    the cheapest way for a routed-back defect to disappear. Copied, not moved:
    the stage is meant to build on what it wrote, so the live document stays.
    """
    from orchestra_engine.model import Pipeline, Stage

    passes = []

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, extra_add_dirs=None, **kwargs):
        plan = artifacts / "PLAN.md"
        if stage_name == "plan":
            passes.append(plan.read_text() if plan.exists() else None)
            plan.write_text(f"plan #{len(passes)}")
        if stage_name == "router":
            nxt = "plan" if len(passes) == 1 else "advance"
            (artifacts / "decision.json").write_text(json.dumps({"next": nxt}))
        return StageResult(stage=stage_name, session_id=None,
                           summary=f"summary of {stage_name}", is_error=False)

    monkeypatch.setattr(shapes, "run_stage", fake)

    pipeline = Pipeline(
        name="t",
        stages=(
            Stage(name="plan", prompt_template="do {task}\n{handoff}"),
            Stage(name="router", prompt_template="do {task}\n{handoff}",
                  decide_targets=("plan",)),
            Stage(name="last", prompt_template="do {task}\n{handoff}"),
        ),
        handoff_template="{summary}",
    )
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, pipeline, runs_dir, log=lambda *a, **k: None))

    # Unlike a blind stage, this one sees what it wrote last time.
    assert passes == [None, "plan #1"]

    (run_dir,) = runs_dir.iterdir()
    artifacts = run_dir / "artifacts"
    assert (artifacts / "PLAN.md").read_text() == "plan #2"
    # The earlier pass is kept where a later stage can read and diff it.
    assert (artifacts / "prior" / "plan-1" / "PLAN.md").read_text() == "plan #1"


def test_a_later_stage_cannot_erase_an_earlier_escalation(run, tmp_path, monkeypatch):
    """A stage may append to the escalation record and may not rewrite it.

    Route-backs mean the stage that raised a concern is rarely the last one to
    write. Without this, a later pass settles a withdrawal by deleting the
    sentence recording it, and the run ends looking like nothing was given up —
    which is what happened on a benchmark task that shipped two points below its
    own first build, twice, with both alarms raised and then retracted.
    """
    from orchestra_engine.engine.runlog import ESCALATIONS_FILE, RESTORED_MARKER
    from orchestra_engine.model import Pipeline, Stage

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, extra_add_dirs=None, **kwargs):
        record = artifacts / ESCALATIONS_FILE
        if stage_name == "raiser":
            record.write_text("AC-11: array cases no longer asserted.\n")
        if stage_name == "retractor":
            # The move this guards: replace the record with a clean bill.
            record.write_text("Nothing outstanding.\n")
        return StageResult(stage=stage_name, session_id=None,
                           summary=f"summary of {stage_name}", is_error=False)

    monkeypatch.setattr(shapes, "run_stage", fake)

    pipeline = Pipeline(
        name="t",
        stages=(
            Stage(name="raiser", prompt_template="do {task}\n{handoff}"),
            Stage(name="retractor", prompt_template="do {task}\n{handoff}"),
            Stage(name="last", prompt_template="do {task}\n{handoff}"),
        ),
        handoff_template="{summary}",
    )
    runs_dir = tmp_path / "runs"
    run(run_loop("T", tmp_path, pipeline, runs_dir, log=lambda *a, **k: None))

    (run_dir,) = runs_dir.iterdir()
    text = (run_dir / "artifacts" / ESCALATIONS_FILE).read_text()
    assert "AC-11: array cases no longer asserted." in text  # survived the retraction
    assert "Nothing outstanding." in text  # and the disagreement is kept, below it
    assert text.index("AC-11") < text.index(RESTORED_MARKER) < text.index("Nothing outstanding.")


def _escalating_pipeline(monkeypatch, raised: str | None):
    """A two-stage pipeline whose second stage raises `raised`, or nothing."""
    from orchestra_engine.engine.runlog import ESCALATIONS_FILE
    from orchestra_engine.model import Pipeline, Stage

    async def fake(stage_name, prompt, workspace, artifacts, *, label=None,
                   iteration=None, item=None, log=print, extra_add_dirs=None, **kwargs):
        if stage_name == "raiser" and raised is not None:
            (artifacts / ESCALATIONS_FILE).write_text(raised)
        return StageResult(stage=stage_name, session_id=None,
                           summary=f"summary of {stage_name}", is_error=False)

    monkeypatch.setattr(shapes, "run_stage", fake)
    return Pipeline(
        name="t",
        stages=(
            Stage(name="quiet", prompt_template="do {task}\n{handoff}"),
            Stage(name="raiser", prompt_template="do {task}\n{handoff}"),
        ),
        handoff_template="{summary}",
    )


def test_an_escalation_is_announced_at_the_stage_that_raised_it(run, tmp_path, monkeypatch):
    """Growth in the escalation record is reported where it happened.

    A non-interactive run has no gates and nobody reading artifacts mid-flight,
    so a concern raised into a file is one nobody hears until the run ends —
    stages after the point where stopping was still cheap. Whoever is watching
    the log is told at the boundary instead.
    """
    from orchestra_engine.engine.runlog import ESCALATIONS_FILE

    pipeline = _escalating_pipeline(monkeypatch, "AC-4: empty input case dropped.\n")
    lines: list[str] = []
    run(run_loop("T", tmp_path, pipeline, tmp_path / "runs", log=lines.append))

    said = [ln for ln in lines if f"{ESCALATIONS_FILE} grew" in ln]
    assert len(said) == 1  # the raiser announces; the quiet stage before it does not
    # The text is agent-written and read by an agent deciding whether to let the
    # run continue, so it is quoted — no stage can forge a line of engine output.
    assert "  | AC-4: empty input case dropped." in said[0]


def test_the_run_ends_by_reporting_what_it_escalated(run, tmp_path, monkeypatch):
    """The record is repeated in full at the end, and only when there is one.

    The stage that raised it may have been several stages back, and nothing else
    in the closing summary would mention it — so a run that nobody watched still
    says what it gave up. A run that gave up nothing stays silent, or the report
    becomes noise and stops being read.
    """
    pipeline = _escalating_pipeline(monkeypatch, "AC-4: empty input case dropped.\n")
    lines: list[str] = []
    run(run_loop("T", tmp_path, pipeline, tmp_path / "runs", log=lines.append))
    closing = [ln for ln in lines if "raised during this run" in ln]
    assert len(closing) == 1
    assert "  | AC-4: empty input case dropped." in closing[0]

    quiet_pipeline = _escalating_pipeline(monkeypatch, None)
    quiet_lines: list[str] = []
    run(run_loop("T", tmp_path, quiet_pipeline, tmp_path / "runs2", log=quiet_lines.append))
    assert not [ln for ln in quiet_lines if "raised during this run" in ln]


def test_every_stage_is_told_how_to_escalate(tmp_path):
    """The instruction lives in the shared preamble, not in each pipeline's prompts.

    The engine protects and reports the escalation record whoever writes it, so
    leaving the instruction to prompt text meant only the pipeline that
    remembered to ask for it had the channel at all — a bugfix run that narrowed
    its reproduction, or a refactor that dropped a behaviour, had nowhere to say
    so while the machinery to record it sat unused.
    """
    from string import Formatter

    from orchestra_engine.engine.runlog import ESCALATIONS_FILE
    from orchestra_engine.engine.session import WORKSPACE_PREAMBLE

    # The call site in _run_session passes exactly these; a placeholder added to
    # the template without updating it would KeyError on every stage of every run.
    fields = {name for _, name, _, _ in Formatter().parse(WORKSPACE_PREAMBLE) if name}
    assert fields == {"workspace", "artifacts", "escalations"}

    text = WORKSPACE_PREAMBLE.format(
        workspace=tmp_path / "ws",
        artifacts=tmp_path / "artifacts",
        escalations=ESCALATIONS_FILE,
    )
    # Named from the constant the engine enforces rather than spelled out beside
    # it, so the file a stage is told to write is the file the engine protects.
    assert ESCALATIONS_FILE in text
    # Without the append-only rule in the instruction a stage rewrites the file,
    # the engine puts the earlier text back, and the stage's entry ends up below
    # the very content it meant to replace.
    assert "Append" in text and "never edit or replace" in text


def test_run_log_records_iterations_and_items(run, research, tmp_path, stub):
    stub()
    results = run_research(run, research, tmp_path, runs_dir=tmp_path / "runs")
    assert len(results) == 3 + 1 + 10 + 1 + 1  # search x3, findings, deep-dive 5x2, report, understand
    (log_file,) = (tmp_path / "runs").glob("*/run.json")
    payload = json.loads(log_file.read_text())
    assert any(s["item"] for s in payload["stages"])
    assert any(s["iteration"] == 2 for s in payload["stages"])


def test_run_log_records_total_duration(run, research, tmp_path, stub):
    """The whole-pipeline wall-clock time lands in run.json, both as a precise
    number and as a human h/m/s string."""
    stub()
    run_research(run, research, tmp_path, runs_dir=tmp_path / "runs")
    (log_file,) = (tmp_path / "runs").glob("*/run.json")
    payload = json.loads(log_file.read_text())
    assert isinstance(payload["duration_seconds"], (int, float))
    assert payload["duration_seconds"] >= 0
    assert payload["duration_hms"] == format_duration(payload["duration_seconds"])


def test_finished_run_is_marked_complete(run, research, tmp_path, stub):
    stub()
    run_research(run, research, tmp_path, runs_dir=tmp_path / "runs")
    (log_file,) = (tmp_path / "runs").glob("*/run.json")
    assert json.loads(log_file.read_text())["complete"] is True
    # the atomic write swaps its temp file into place rather than leaving it
    assert not list((tmp_path / "runs").glob("*/run.json.tmp"))


def test_run_log_survives_a_stage_raising(run, research, tmp_path, monkeypatch):
    """A stage that dies takes the run with it, but not the record of the stages
    that already finished: their summaries and session IDs are the handoff, and
    they live nowhere but run.json."""
    calls, concurrency = [], {"now": 0, "peak": 0}
    real = make_fake_run_stage(calls, concurrency)

    async def dies_on_findings(stage_name, *args, **kwargs):
        if stage_name == "findings":
            raise RuntimeError("connection closed mid-response")
        return await real(stage_name, *args, **kwargs)

    monkeypatch.setattr(shapes, "run_stage", dies_on_findings)
    with pytest.raises(RuntimeError):
        run_research(run, research, tmp_path, runs_dir=tmp_path / "runs")

    (log_file,) = (tmp_path / "runs").glob("*/run.json")
    payload = json.loads(log_file.read_text())
    assert payload["complete"] is False  # the engine never reached its own end
    assert [s["stage"] for s in payload["stages"]] == ["search"] * 3
    assert [s["session_id"] for s in payload["stages"]] == ["sess-1", "sess-2", "sess-3"]
    assert payload["total_cost_usd"] == pytest.approx(0.03)


def test_run_log_survives_a_lost_stage_gate(run, research, tmp_path, stub, monkeypatch):
    """The failure this durability exists for: driving -i over a pipe whose
    writer went away, so the gate between two stages raises EOFError."""
    stub()
    import orchestra_engine.engine.executor as executor

    async def gate(stage_name):
        if stage_name == "findings":
            raise EOFError("EOF when reading a line")
        return "run"

    monkeypatch.setattr(executor.interaction, "stage_gate", gate)
    with pytest.raises(EOFError):
        run(
            run_loop(
                task="research topic T",
                workspace=tmp_path / "ws",
                pipeline=research,
                runs_dir=tmp_path / "runs",
                interactive=True,
                log=lambda line: None,
            )
        )

    (log_file,) = (tmp_path / "runs").glob("*/run.json")
    payload = json.loads(log_file.read_text())
    assert payload["complete"] is False
    assert [s["stage"] for s in payload["stages"]] == ["search"] * 3


def test_artifacts_live_in_the_run_not_the_workspace(run, research, tmp_path, stub):
    """The whole point of the run directory: the stages hand each other files
    without ever writing them into the repo under test."""
    calls, _ = stub()
    run_research(run, research, tmp_path)

    workspace = tmp_path / "ws"
    artifacts = calls[0]["artifacts"]
    assert artifacts.is_dir()
    assert not artifacts.is_relative_to(workspace)
    assert artifacts.is_relative_to(tmp_path / "runs")
    # findings wrote leads.txt and deep-dive fanned out over it, so the bus
    # worked — and none of it landed in the workspace.
    assert (artifacts / "leads.txt").is_file()
    assert list(workspace.iterdir()) == []
    # every session got the same artifacts dir, and it's in their prompt
    assert {c["artifacts"] for c in calls} == {artifacts}
    assert str(artifacts) in calls[0]["prompt"]


def test_each_run_gets_its_own_directory(run, research, tmp_path, stub):
    """Two runs against one workspace must not overwrite each other's evidence."""
    calls, _ = stub()
    run_research(run, research, tmp_path)
    first = calls[0]["artifacts"]

    calls2, _ = stub()
    run_research(run, research, tmp_path)
    second = calls2[0]["artifacts"]

    assert first != second
    assert first.is_dir() and second.is_dir()
    assert len(list((tmp_path / "runs").glob("*/run.json"))) == 2


def test_fan_out_partial_failure_continues(run, research, tmp_path, stub):
    calls, _ = stub(fail_items=("https://b.com",))
    run_research(run, research, tmp_path)
    reports = [c for c in calls if c["stage"] == "report"]
    assert len(reports) == 1  # loop continued past the failed item
    assert "### https://b.com [ERROR]" in reports[0]["handoff"]
    assert "### https://a.com [ok]" in reports[0]["handoff"]
    # failed item's chain stopped after pass 1
    b = [c for c in calls if c["item"] == "https://b.com"]
    assert len(b) == 1


def test_fan_out_total_failure_stops_loop(run, research, tmp_path, stub):
    calls, _ = stub(fail_items=tuple(f"https://{x}.com" for x in "abcde"))
    run_research(run, research, tmp_path)
    assert not [c for c in calls if c["stage"] == "report"]


def test_missing_fan_out_file_stops_loop(run, research, tmp_path, stub):
    stub()
    import dataclasses

    deep_only = dataclasses.replace(research, stages=(research.stages[2],))
    results = run(
        run_loop(
            task="t",
            workspace=tmp_path / "ws",
            pipeline=deep_only,
            runs_dir=tmp_path / "runs",
            log=lambda line: None,
        )
    )
    assert len(results) == 1
    assert results[0].is_error and "not found" in results[0].summary


def test_sequential_stage_error_stops_loop(run, research, tmp_path, monkeypatch):
    async def failing(stage_name, prompt, workspace, artifacts, **kwargs):
        return StageResult(stage=stage_name, session_id="s", summary="boom", is_error=True)

    monkeypatch.setattr(shapes, "run_stage", failing)
    results = run_research(run, research, tmp_path)
    assert len(results) == 1  # search pass 1 failed; nothing else ran
    assert results[0].is_error


def test_transcripts_follow_claude_config_dir(tmp_path, monkeypatch):
    """A container run moves the CLI's config dir onto a mounted volume so the
    stage transcripts survive teardown. If the engine kept looking under $HOME,
    cross-stage transcript access would go dark there and say nothing about it."""
    import orchestra_engine.engine.executor as executor

    projects = tmp_path / "cfg" / "projects" / "-app"
    projects.mkdir(parents=True)
    sid = "11dceda4-7e66-430b-857a-3d4141cfdf2e"
    (projects / f"{sid}.jsonl").write_text("{}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))

    results = [StageResult(stage="explore", session_id=sid, summary="s", is_error=False)]
    found = executor._resolve_transcripts(results)
    assert [f["path"] for f in found] == [projects / f"{sid}.jsonl"]

    # ...and the dir handed to the next stage is the one holding that transcript.
    _, add_dirs = executor._prior_session_access(results)
    assert add_dirs == [str(projects)]


def test_transcripts_fall_back_to_home_when_unset(monkeypatch):
    import orchestra_engine.engine.executor as executor

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert executor._sessions_root() == Path.home() / ".claude" / "projects"


def test_only_this_runs_session_ids_reach_the_glob(tmp_path, monkeypatch):
    """add_dirs grants the next session read access, so the search must never be
    widenable by a value an agent chose. Fan-out merges put a sentence in the
    session_id slot; it must not be treated as an ID."""
    import orchestra_engine.engine.executor as executor

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "projects" / "slug").mkdir(parents=True)
    (tmp_path / "projects" / "slug" / "real.jsonl").write_text("{}")

    bogus = [
        StageResult(stage="fan", session_id="(multiple — 3 items)", summary="s", is_error=False)
    ]
    assert executor._resolve_transcripts(bogus) == []


# ---- handoff delivery: tool result, not prompt text ------------------------


def test_handoff_server_is_absent_for_the_first_stage():
    """Nothing to serve means no server and no tool — the opening stage of a
    run should not be offered a read_handoff that returns nothing useful."""
    from orchestra_engine.engine import session

    assert session._handoff_server(None) is None
    assert session._handoff_server("") is None


def test_handoff_server_serves_the_body_verbatim(run):
    """The tool returns exactly what the prompt used to carry, fences included:
    the text is a previous agent's account and must stay marked as one."""
    from orchestra_engine.engine import session

    body = (
        "--- HANDOFF: design ---\nSession ID: sess-1\n"
        "What it did:\nchose float64\n--- END HANDOFF ---\n"
    )
    server = session._handoff_server(body)
    assert server is not None

    from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

    inst = server["instance"]
    call_params = CallToolRequestParams(name=handoff_tool.TOOL_NAME, arguments={})

    # mcp 2.x keeps handlers by method name and returns bare results; 1.x
    # keys them by request type and wraps each result in a RootModel.
    if hasattr(inst, "get_request_handler"):
        listed = run(inst.get_request_handler("tools/list").handler(None, None))
        called = run(inst.get_request_handler("tools/call").handler(None, call_params))
    else:
        handlers = inst.request_handlers
        listed = run(handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))).root
        called = run(
            handlers[CallToolRequest](CallToolRequest(method="tools/call", params=call_params))
        ).root
    assert [t.name for t in listed.tools] == [handoff_tool.TOOL_NAME]
    assert called.content[0].text == body


def test_handoff_tool_is_auto_approved_and_widens_nothing(monkeypatch, tmp_path, run):
    """allowed_tools is the SDK's auto-approve list, not an exclusive allowlist.
    The engine names exactly one tool there — enough that an unattended run does
    not stall on a gate for its own data channel, and not enough to touch what
    else a stage may use."""
    from orchestra_engine.engine import session

    captured = {}

    def fake_query(prompt, options):
        captured["options"] = options

        async def empty():
            return
            yield

        return empty()

    monkeypatch.setattr(session, "query", fake_query)
    run(
        session._run_session(
            "design", "do it", tmp_path, tmp_path,
            permission_mode="auto", max_turns=None, model=None, interactive=False,
            log=lambda _: None, label="design", iteration=None, item=None,
            handoff_body="--- HANDOFF ---\nx\n--- END HANDOFF ---\n",
        )
    )
    opts = captured["options"]
    assert opts.allowed_tools == [handoff_tool.QUALIFIED_TOOL]
    assert list(opts.mcp_servers) == [handoff_tool.SERVER_NAME]
    # The report is not in the prompt the session was given.
    assert "--- HANDOFF ---" not in captured.get("prompt", "")

