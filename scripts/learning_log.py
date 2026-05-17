"""Create numbered personal learning notes under docs/learning.

This helper keeps the owner's personal learning-log convention repeatable:
front matter, reading-order filename, revision history, and index update.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

DEFAULT_DOCS_DIR = Path("docs/learning")
INDEX_FILENAME = "README.md"
WORKFLOW_MARKER = "\n## Learning-doc workflow"


def create_learning_log(
    *,
    title: str,
    body: str,
    topics: Sequence[str],
    related_code: Sequence[str],
    docs_dir: Path = DEFAULT_DOCS_DIR,
    today: str | None = None,
) -> Path:
    """Create a numbered personal learning note and add it to the learning index."""
    clean_title = _normalize_required_text(title, "title")
    clean_body = _normalize_required_text(body, "body")
    note_date = today or date.today().isoformat()
    _validate_iso_date(note_date)

    docs_dir.mkdir(parents=True, exist_ok=True)
    note_path = docs_dir / f"{_next_note_number(docs_dir):02d}-{_slugify(clean_title)}.md"
    if note_path.exists():
        raise FileExistsError(f"Learning note already exists: {note_path}")

    note_path.write_text(
        _render_note(
            title=clean_title,
            body=clean_body,
            topics=topics,
            related_code=related_code,
            today=note_date,
        ),
        encoding="utf-8",
    )
    _update_index(docs_dir / INDEX_FILENAME, note_path.name, clean_title)
    return note_path


def _render_note(
    *,
    title: str,
    body: str,
    topics: Sequence[str],
    related_code: Sequence[str],
    today: str,
) -> str:
    front_matter = [
        "---",
        f"created: {today}",
        f"updated: {today}",
        "status: active",
        "topics:",
        *_render_yaml_list(topics or ["learning-log"]),
        "related_code:",
        *_render_yaml_list(related_code),
        "---",
        "",
    ]
    heading = "" if body.lstrip().startswith("# ") else f"# {title}\n\n"
    revision = f"\n\n## Revision history\n\n- {today}: Created learning log for `{title}`.\n"
    return "\n".join(front_matter) + "\n" + heading + body.strip() + revision


def _render_yaml_list(items: Sequence[str]) -> list[str]:
    cleaned = [_normalize_list_item(item) for item in items if item.strip()]
    if not cleaned:
        return ["  []"]
    return [f"  - {item}" for item in cleaned]


def _normalize_list_item(item: str) -> str:
    return item.strip().replace("\n", " ")


def _next_note_number(docs_dir: Path) -> int:
    note_numbers = []
    for path in docs_dir.glob("[0-9][0-9]-*.md"):
        match = re.match(r"^(\d{2})-", path.name)
        if match:
            note_numbers.append(int(match.group(1)))
    return max(note_numbers, default=0) + 1


def _slugify(title: str) -> str:
    normalized = title.casefold().replace("**", " kwargs ").replace("*", " keyword-only ")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "learning-log"


def _normalize_required_text(value: str, field_name: str) -> str:
    clean_value = value.strip()
    if not clean_value:
        raise ValueError(f"{field_name} must not be blank")
    return clean_value


def _validate_iso_date(value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD format: {value!r}") from exc


def _update_index(index_path: Path, note_filename: str, title: str) -> None:
    link = f"./{note_filename}"
    if not index_path.exists():
        index_path.write_text(
            "# Learning notes\n\nStart here:\n\n"
            f"1. [{title}]({link})\n"
            "\n## Learning-doc workflow\n",
            encoding="utf-8",
        )
        return

    index_text = index_path.read_text(encoding="utf-8")
    if link in index_text:
        return

    if WORKFLOW_MARKER not in index_text:
        index_path.write_text(
            index_text.rstrip() + f"\n1. [{title}]({link})\n",
            encoding="utf-8",
        )
        return

    before, after = index_text.split(WORKFLOW_MARKER, maxsplit=1)
    next_number = len(re.findall(r"^\d+\. \[", before, flags=re.MULTILINE)) + 1
    updated = f"{before.rstrip()}\n{next_number}. [{title}]({link})\n{WORKFLOW_MARKER}{after}"
    index_path.write_text(updated, encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a personal docs/learning note and update the index."
    )
    parser.add_argument("--title", required=True, help="Human-readable learning note title.")
    body_group = parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Markdown body content for the note.")
    body_group.add_argument("--body-file", type=Path, help="Path to a Markdown body file.")
    parser.add_argument("--topic", action="append", default=[], help="Topic keyword; repeatable.")
    parser.add_argument(
        "--related-code",
        action="append",
        default=[],
        help="Repo path related to the note; repeatable.",
    )
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--date", help="Override creation/update date in YYYY-MM-DD format.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    body = args.body_file.read_text(encoding="utf-8") if args.body_file else args.body
    note_path = create_learning_log(
        title=args.title,
        body=body,
        topics=args.topic,
        related_code=args.related_code,
        docs_dir=args.docs_dir,
        today=args.date,
    )
    print(note_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
