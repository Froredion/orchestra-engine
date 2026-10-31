"""Pins: stated criteria made executable before anything adjudicates them.

Every stage can measure what a decision costs — run a differential and the
programs that break are countable. Nothing measures a criterion quietly narrowed
to a convenient subset of the inputs it named. These cover the engine half: a
pin that stops promising what it promised is announced and recorded, whether it
was deleted or rewritten.
"""

from orchestra_engine.engine.runlog import ESCALATIONS_FILE, PINS_FILE
from orchestra_engine.model import Pipeline, Stage


def _pipeline(*names):
    return Pipeline(
        name="p",
        stages=tuple(Stage(name=n, prompt_template=n) for n in names),
        handoff_template="{summary}",
    )


def _run(stub, run, tmp_path, writes, stages=("pin", "design", "handback")):
    lines: list[str] = []
    stub(_pipeline(*stages), tmp_path / "ws", run, on_stage=writes, log=lines.append)
    escalations = next((tmp_path / "runs").glob(f"*/artifacts/{ESCALATIONS_FILE}"), None)
    return lines, (escalations.read_text(encoding="utf-8") if escalations else "")


def test_a_deleted_pin_is_announced_and_recorded(stub, run, tmp_path):
    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text(
                "PIN-1: a errors\nPIN-2: b errors\n", encoding="utf-8"
            )
        if stage == "design":
            (artifacts / PINS_FILE).write_text("PIN-1: a errors\n", encoding="utf-8")

    lines, escalations = _run(stub, run, tmp_path, writes)
    assert any("PINS CHANGED" in ln and "dropped: PIN-2" in ln for ln in lines)
    assert "PIN-2" in escalations and "[design]" in escalations


def test_an_inverted_pin_is_caught_even_though_the_id_survives(stub, run, tmp_path):
    """The hole this closes: keep every id, rewrite what two of them assert, and
    report "41 declared, 41 present, 41 pass" over a withdrawn requirement."""

    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text(
                "PIN-1: errors, message contains 'cannot have destructuring'\n", encoding="utf-8"
            )
        if stage == "design":
            (artifacts / PINS_FILE).write_text(
                "PIN-1: parses as a member-expression assignment\n", encoding="utf-8"
            )

    lines, escalations = _run(stub, run, tmp_path, writes)
    assert any("PINS CHANGED" in ln and "now assert something else: PIN-1" in ln for ln in lines)
    # Both wordings land on the record, so the withdrawal is legible without a diff.
    assert "cannot have destructuring" in escalations
    assert "member-expression assignment" in escalations


def test_reflowing_the_document_is_not_a_change(stub, run, tmp_path):
    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text("PIN-1: a  errors   loudly\n", encoding="utf-8")
        if stage == "design":
            (artifacts / PINS_FILE).write_text("  PIN-1:  a errors loudly\n", encoding="utf-8")

    lines, _ = _run(stub, run, tmp_path, writes)
    assert not any("PINS CHANGED" in ln for ln in lines)


def test_a_status_column_update_is_not_a_change(stub, run, tmp_path):
    """A pin going from failing to passing is the run working, not a withdrawal."""

    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text(
                "PIN-1: a errors\n\n| PIN-1 | AC-1 | a | errors | fails |\n", encoding="utf-8"
            )
        if stage == "design":
            (artifacts / PINS_FILE).write_text(
                "PIN-1: a errors\n\n| PIN-1 | AC-1 | a | errors | passes |\n", encoding="utf-8"
            )

    lines, _ = _run(stub, run, tmp_path, writes)
    assert not any("PINS CHANGED" in ln for ln in lines)


def _ws_test(tmp_path, body):
    """Write the pin's test into the workspace, where the promise is really kept."""
    tests = tmp_path / "ws" / "test"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "pins.spec.ts").write_text(body, encoding="utf-8")


def test_an_inverted_pin_test_is_caught_though_the_document_never_moves(stub, run, tmp_path):
    """The hole a claim watch cannot see, and the one a real run went through.

    Design overruled three pins by rewriting their *tests* and left PINS.md
    alone, so every id was present, every claim intact, and the shipped parser
    accepted the input all three said must be rejected.
    """

    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text(
                "PIN-1: `using [a] = b` is rejected, message contains "
                "'cannot have destructuring'\n",
                encoding="utf-8",
            )
            _ws_test(tmp_path, "// PIN-1\nit('rejects', () => expect(f).toThrow());\n")
        if stage == "design":
            # PINS.md deliberately untouched.
            _ws_test(tmp_path, "// PIN-1\nit('parses', () => expect(f).not.toThrow());\n")

    lines, escalations = _run(stub, run, tmp_path, writes)
    assert any("PIN TESTS CHANGED" in ln and "assert differently: PIN-1" in ln for ln in lines)
    # The claim is quoted from the document that did not change, because that is
    # what a reader needs to judge whether the new assertion still keeps it.
    assert "cannot have destructuring" in escalations
    assert "not.toThrow" in escalations


def test_a_pin_test_deleted_outright_is_caught(stub, run, tmp_path):
    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text("PIN-1: a errors\n", encoding="utf-8")
            _ws_test(tmp_path, "// PIN-1\nit('errors', () => expect(f).toThrow());\n")
        if stage == "design":
            _ws_test(tmp_path, "// nothing here any more\n")

    lines, escalations = _run(stub, run, tmp_path, writes)
    assert any("no longer asserted anywhere: PIN-1" in ln for ln in lines)
    assert "PIN-1" in escalations


def test_editing_elsewhere_in_the_test_file_is_not_a_pin_change(stub, run, tmp_path):
    """Adding tests is what a run does all day; only the pin's own block counts."""

    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text("PIN-1: a errors\n", encoding="utf-8")
            _ws_test(tmp_path, "// PIN-1\nit('errors', () => expect(f).toThrow());\n")
        if stage == "design":
            _ws_test(
                tmp_path,
                "// PIN-1\nit('errors', () => expect(f).toThrow());\n"
                + "\n" * 20
                + "it('something else entirely', () => expect(g).toBe(1));\n",
            )

    lines, _ = _run(stub, run, tmp_path, writes)
    assert not any("PIN TESTS CHANGED" in ln for ln in lines)


def test_a_run_with_no_pins_never_walks_the_workspace(stub, run, tmp_path, monkeypatch):
    """The scan is the one expensive thing here; a pipeline without a pin stage
    must not pay for it on every boundary."""
    from orchestra_engine.engine import executor

    calls = []
    real = executor._pin_assertion_files
    monkeypatch.setattr(
        executor, "_pin_assertion_files", lambda ws: calls.append(ws) or real(ws)
    )

    _run(stub, run, tmp_path, lambda stage, artifacts: None)
    assert calls == []


def test_a_change_is_reported_once_not_by_every_later_stage(stub, run, tmp_path):
    def writes(stage, artifacts):
        if stage == "pin":
            (artifacts / PINS_FILE).write_text("PIN-1: x\nPIN-2: y\n", encoding="utf-8")
        if stage == "design":
            (artifacts / PINS_FILE).write_text("PIN-1: x\n", encoding="utf-8")

    lines, _ = _run(stub, run, tmp_path, writes, stages=("pin", "design", "build", "handback"))
    assert len([ln for ln in lines if "PINS CHANGED" in ln]) == 1
