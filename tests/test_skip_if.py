"""skip_if: conditional stages driven by an artifact an earlier stage wrote."""

import pytest

from orchestra_engine.model import Pipeline, SkipIf, Stage
from orchestra_engine.pipeline import _parse_stage, stage_signature


def make_pipeline(*stages):
    return Pipeline(name="t", stages=tuple(stages), handoff_template="{summary}")


def stage(name, **kwargs):
    return Stage(name=name, prompt_template="do {task}\n{handoff}", **kwargs)


def writes(stage_name, files):
    """An on_stage hook: when `stage_name` runs, it leaves `files` in artifacts —
    which is how the gate file gets there for real."""

    def hook(name, artifacts):
        if name == stage_name:
            for filename, content in files.items():
                (artifacts / filename).write_text(content)

    return hook


# --- parsing -------------------------------------------------------------


def test_parses_file_and_needle(tmp_path):
    s = _parse_stage("fix", "---\nskip_if: verdict.txt contains NOT REPRODUCED\n---\nbody", tmp_path)
    assert s.skip_if == SkipIf(file="verdict.txt", needle="NOT REPRODUCED")


def test_needle_keeps_internal_spacing_and_case(tmp_path):
    s = _parse_stage("fix", "---\nskip_if: v.txt contains Verdict: NOT REPRODUCED\n---\nb", tmp_path)
    assert s.skip_if.needle == "Verdict: NOT REPRODUCED"


@pytest.mark.parametrize(
    "value",
    [
        "verdict.txt",  # no separator
        "verdict.txt contains ",  # empty needle
        " contains NOT REPRODUCED",  # empty file
    ],
)
def test_rejects_malformed_skip_if(value, tmp_path):
    with pytest.raises(ValueError, match="skip_if must read"):
        _parse_stage("fix", f"---\nskip_if: {value}\n---\nbody", tmp_path)


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.txt", "a/../../b.txt"])
def test_rejects_paths_escaping_the_artifacts_dir(path, tmp_path):
    with pytest.raises(ValueError, match="relative path inside the run's"):
        _parse_stage("fix", f"---\nskip_if: {path} contains x\n---\nbody", tmp_path)


def test_signature_shows_the_gate():
    s = stage("fix", skip_if=SkipIf(file="verdict.txt", needle="NOT REPRODUCED"))
    assert "skipped if verdict.txt contains 'NOT REPRODUCED'" in stage_signature(s)


# --- evaluation ----------------------------------------------------------


def test_skips_when_needle_present(run, tmp_path, stub):
    calls, results = stub(
        make_pipeline(
            stage("reproduce"),
            stage("fix", skip_if=SkipIf("verdict.txt", "NOT REPRODUCED")),
        ),
        tmp_path,
        run,
        on_stage=writes("reproduce", {"verdict.txt": "Verdict: NOT REPRODUCED\n"}),
    )
    assert [c["stage"] for c in calls] == ["reproduce"], "fix must not run"
    assert [r.stage for r in results] == ["reproduce", "fix"]
    skipped = results[-1]
    assert skipped.session_id is None
    assert "skipped" in skipped.summary, "the skip must survive into the run log"
    assert not skipped.is_error


def test_runs_when_needle_absent(run, tmp_path, stub):
    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("fix", skip_if=SkipIf("verdict.txt", "NOT REPRODUCED")),
        ),
        tmp_path,
        run,
        on_stage=writes("reproduce", {"verdict.txt": "Verdict: REPRODUCED\n"}),
    )
    assert [c["stage"] for c in calls] == ["reproduce", "fix"]


def test_missing_gate_file_fails_open_and_says_so(run, tmp_path, stub, logs):
    """An unevaluable gate must not silently skip — nor silently pass."""
    calls, _ = stub(
        make_pipeline(stage("fix", skip_if=SkipIf("verdict.txt", "NOT REPRODUCED"))),
        tmp_path / "ws",
        run,
        log=logs.append,
    )
    assert [c["stage"] for c in calls] == ["fix"], "fail open: the stage still runs"
    assert any("GATE NOT EVALUATED" in line and "not found" in line for line in logs)


def test_symlink_out_of_artifacts_is_refused(run, tmp_path, stub, logs):
    """resolve() follows symlinks, so a link an agent plants in the artifacts
    dir can't smuggle a verdict in from outside it."""
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("NOT REPRODUCED")

    def plant_symlink(name, artifacts):
        if name == "reproduce":
            (artifacts / "verdict.txt").symlink_to(outside)

    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("fix", skip_if=SkipIf("verdict.txt", "NOT REPRODUCED")),
        ),
        tmp_path / "ws",
        run,
        log=logs.append,
        on_stage=plant_symlink,
    )
    assert [c["stage"] for c in calls] == ["reproduce", "fix"], "must not honor an outside gate"
    assert any("GATE NOT EVALUATED" in line and "outside" in line for line in logs)


def test_gate_does_not_disturb_the_handoff_chain(run, tmp_path, stub):
    """A skipped stage is transparent: the next stage sees the last one that ran."""
    calls, _ = stub(
        make_pipeline(
            stage("reproduce"),
            stage("fix", skip_if=SkipIf("verdict.txt", "NOT REPRODUCED")),
            stage("prevent"),
        ),
        tmp_path / "ws",
        run,
        on_stage=writes("reproduce", {"verdict.txt": "NOT REPRODUCED"}),
    )
    assert [c["stage"] for c in calls] == ["reproduce", "prevent"]
    assert "summary of reproduce" in calls[-1]["handoff"], "prevent should see reproduce"
