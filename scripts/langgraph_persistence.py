"""Set up, inspect, and reconcile self-hosted LangGraph persistence."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import inspect, select

from my_agents.conversations.models import AgentRunModel, RunStatus
from my_agents.memory.store_projection import reconcile_memory_store
from my_agents.persistence.database import _sessionmaker_for_url
from my_agents.persistence.langgraph import open_langgraph_persistence, setup_langgraph_persistence
from my_agents.settings import get_settings

EXPECTED_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "store_migrations",
    "store",
    "store_vectors",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", help="run framework-owned Postgres migrations")
    subparsers.add_parser("status", help="verify required persistence tables")
    subparsers.add_parser("prune-checkpoints", help="delete terminal, expired, or orphan threads")
    reconcile = subparsers.add_parser("reconcile-memory", help="report or repair Store drift")
    reconcile.add_argument("--apply", action="store_true", help="repair drift after reporting it")
    args = parser.parse_args(argv)

    base_settings = get_settings()
    settings = base_settings.model_copy(
        update={
            "checkpointer_enabled": True,
            "memory_store_enabled": True,
            "memory_store_embedding_dimensions": (
                32
                if base_settings.embedding_mode == "deterministic"
                else base_settings.memory_store_embedding_dimensions
            ),
        }
    )
    if args.command == "setup":
        setup_langgraph_persistence(settings)
        return _print_status(settings)
    if args.command == "status":
        return _print_status(settings)
    if args.command == "prune-checkpoints":
        return _prune_checkpoints(settings)
    return _reconcile(settings, apply=bool(args.apply))


def _print_status(settings) -> int:  # noqa: ANN001
    engine = _sessionmaker_for_url(settings.database_url).kw["bind"]
    tables = set(inspect(engine).get_table_names())
    missing = sorted(EXPECTED_TABLES - tables)
    print(f"langgraph_persistence_tables={len(EXPECTED_TABLES - set(missing))}")
    print(f"langgraph_persistence_missing={','.join(missing) if missing else 'none'}")
    return 1 if missing else 0


def _reconcile(settings, *, apply: bool) -> int:  # noqa: ANN001
    resources = open_langgraph_persistence(settings)
    if resources.store is None:
        raise RuntimeError("LangGraph Store is unavailable")
    session = _sessionmaker_for_url(settings.database_url)()
    try:
        report = reconcile_memory_store(db=session, store=resources.store, apply=apply)
    finally:
        session.close()
        resources.close()
    print(f"expected={report.expected}")
    print(f"actual={report.actual}")
    print(f"missing={report.missing}")
    print(f"stale={report.stale}")
    print(f"orphaned={report.orphaned}")
    print(f"applied_upserts={report.applied_upserts}")
    print(f"applied_deletes={report.applied_deletes}")
    return 1 if report.drift and not apply else 0


def _prune_checkpoints(settings) -> int:  # noqa: ANN001
    resources = open_langgraph_persistence(settings)
    checkpointer = resources.checkpointer
    if checkpointer is None:
        raise RuntimeError("LangGraph checkpointer is unavailable")
    session = _sessionmaker_for_url(settings.database_url)()
    try:
        valid_waiting = set(
            session.scalars(
                select(AgentRunModel.id).where(
                    AgentRunModel.status == RunStatus.WAITING_FOR_INPUT.value,
                    AgentRunModel.interaction_expires_at > datetime.now(UTC),
                )
            ).all()
        )
        checkpoint_threads = {
            str(item.config.get("configurable", {}).get("thread_id"))
            for item in checkpointer.list(None)
            if item.config.get("configurable", {}).get("thread_id")
        }
        stale_threads = checkpoint_threads - valid_waiting
        for thread_id in sorted(stale_threads):
            checkpointer.delete_thread(thread_id)
    finally:
        session.close()
        resources.close()
    print(f"checkpoint_threads={len(checkpoint_threads)}")
    print(f"checkpoint_threads_deleted={len(stale_threads)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
