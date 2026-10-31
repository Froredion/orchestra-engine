"""The guidance installer: idempotent, and it never eats the rest of the file."""

from orchestra_engine import guidance


def test_creates_the_file_when_there_is_none(tmp_path):
    target = tmp_path / "nested" / "CLAUDE.md"

    assert guidance.install(target) == "created"

    text = target.read_text()
    assert text.startswith("<orchestra-engine>")
    assert text.rstrip().endswith("</orchestra-engine>")


def test_second_install_changes_nothing(tmp_path):
    target = tmp_path / "CLAUDE.md"
    guidance.install(target)
    before = target.read_text()

    assert guidance.install(target) == "unchanged"
    assert target.read_text() == before


def test_appends_without_disturbing_existing_notes(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("# My rules\n\nNever push to main.\n")

    assert guidance.install(target) == "appended"

    text = target.read_text()
    assert text.startswith("# My rules\n\nNever push to main.\n")
    assert "<orchestra-engine>" in text


def test_replaces_in_place_between_the_markers(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(
        "# Top matter\n\n"
        "<orchestra-engine>\n\nstale guidance from an older checkout\n\n</orchestra-engine>\n\n"
        "# Bottom matter\n"
    )

    assert guidance.install(target) == "updated"

    text = target.read_text()
    assert "stale guidance" not in text
    assert text.startswith("# Top matter\n")
    assert text.endswith("# Bottom matter\n")
    # One block, not two stacked copies.
    assert text.count("<orchestra-engine>") == 1


def test_replaces_a_block_installed_under_the_old_name(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(
        "# Top matter\n\n"
        "<loop-engine>\n\nguidance from before the rename\n\n</loop-engine>\n\n"
        "# Bottom matter\n"
    )

    assert guidance.install(target) == "updated"

    text = target.read_text()
    assert "<loop-engine>" not in text and "before the rename" not in text
    assert text.count("<orchestra-engine>") == 1
    assert text.startswith("# Top matter\n")
    assert text.endswith("# Bottom matter\n")


def test_leaves_other_tagged_sections_alone(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(
        "<corridor>\n\nAlways analyze the plan.\n\n</corridor>\n"
        "<orchestra-engine>\n\nSTALE-GUIDANCE-SENTINEL\n\n</orchestra-engine>\n"
    )

    guidance.install(target)

    text = target.read_text()
    assert "<corridor>\n\nAlways analyze the plan.\n\n</corridor>\n" in text
    assert "STALE-GUIDANCE-SENTINEL" not in text


def test_dry_run_reports_without_writing(tmp_path):
    target = tmp_path / "CLAUDE.md"

    assert guidance.install(target, dry_run=True) == "created"
    assert not target.exists()


def test_block_names_this_checkout(tmp_path):
    block = guidance.render_block()

    assert "{repo_root}" not in block
    assert str(guidance.repo_root()) in block
