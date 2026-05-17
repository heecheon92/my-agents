"""Learning-log automation tests."""

from __future__ import annotations

from scripts.learning_log import create_learning_log


def test_create_learning_log_writes_front_matter_revision_and_index(tmp_path) -> None:
    docs_dir = tmp_path / "docs" / "learning"
    docs_dir.mkdir(parents=True)
    (docs_dir / "README.md").write_text(
        "# Learning notes\n\nStart here:\n\n## Learning-doc workflow\n",
        encoding="utf-8",
    )

    note_path = create_learning_log(
        title="Python keyword-only arguments",
        body="Use `*` before optional flags.",
        topics=["python", "function-signatures"],
        related_code=["my_agents/example.py"],
        docs_dir=docs_dir,
        today="2026-05-15",
    )

    assert note_path.name == "01-python-keyword-only-arguments.md"
    note = note_path.read_text(encoding="utf-8")
    assert note.startswith("---\ncreated: 2026-05-15\nupdated: 2026-05-15")
    assert "  - python" in note
    assert "  - my_agents/example.py" in note
    assert "# Python keyword-only arguments" in note
    assert "## Revision history" in note

    index = (docs_dir / "README.md").read_text(encoding="utf-8")
    assert "1. [Python keyword-only arguments](./01-python-keyword-only-arguments.md)" in index
    assert "## Learning-doc workflow" in index


def test_create_learning_log_numbers_after_existing_notes(tmp_path) -> None:
    docs_dir = tmp_path / "docs" / "learning"
    docs_dir.mkdir(parents=True)
    (docs_dir / "README.md").write_text(
        "# Learning notes\n\nStart here:\n\n"
        "1. [Existing](./01-existing.md)\n"
        "\n## Learning-doc workflow\n",
        encoding="utf-8",
    )
    (docs_dir / "01-existing.md").write_text("existing", encoding="utf-8")

    note_path = create_learning_log(
        title="Dict kwargs expansion",
        body="# Custom heading\n\nBody stays as provided.",
        topics=[],
        related_code=[],
        docs_dir=docs_dir,
        today="2026-05-15",
    )

    assert note_path.name == "02-dict-kwargs-expansion.md"
    note = note_path.read_text(encoding="utf-8")
    assert "topics:\n  - learning-log" in note
    assert "related_code:\n  []" in note
    assert note.count("# Custom heading") == 1

    index = (docs_dir / "README.md").read_text(encoding="utf-8")
    assert "2. [Dict kwargs expansion](./02-dict-kwargs-expansion.md)" in index
