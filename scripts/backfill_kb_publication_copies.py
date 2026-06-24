"""Backfill legacy personal-KB publication rows into group-owned KB copies."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict

from my_agents.knowledge.publication_copies import backfill_legacy_publication_copies
from my_agents.persistence.database import get_database_session


def run(*, dry_run: bool) -> dict[str, object]:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        summary = backfill_legacy_publication_copies(db, dry_run=dry_run)
        return asdict(summary)
    finally:
        session_generator.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy legacy personal-KB publications into group-owned KBs."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview actions without changes.")
    mode.add_argument("--apply", action="store_true", help="Apply the backfill.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dry_run = not args.apply
    print(json.dumps(run(dry_run=dry_run), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
