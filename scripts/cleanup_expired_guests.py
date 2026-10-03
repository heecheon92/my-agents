"""Preview expired guest erasure; --apply requires configured opt-in and audit HMAC key."""

from __future__ import annotations

import argparse
import json
from collections import Counter

from my_agents.auth.guest_cleanup import cleanup_batch
from my_agents.persistence.database import _sessionmaker_for_url, initialize_database
from my_agents.persistence.langgraph import (
    LangGraphPersistenceResources,
    open_langgraph_persistence,
)
from my_agents.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Permanently erase eligible expired guests"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.apply and not settings.guest_cleanup_enabled:
        parser.error("--apply requires MY_AGENTS_GUEST_CLEANUP_ENABLED=true")
    initialize_database(settings)
    resources = (
        open_langgraph_persistence(settings) if args.apply else LangGraphPersistenceResources()
    )
    totals: Counter[str] = Counter()
    cursor = ""
    try:
        while True:
            with _sessionmaker_for_url(settings.database_url)() as db:
                counts, cursor = cleanup_batch(
                    db, settings, resources, after_id=cursor, dry_run=not args.apply
                )
            totals.update(counts)
            if not cursor:
                break
    finally:
        resources.close()
    print(json.dumps({"dry_run": not args.apply, "counts": dict(totals)}, sort_keys=True))
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
