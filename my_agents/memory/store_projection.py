"""Governed Product DB to LangGraph Store memory projection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from langgraph.store.base import BaseStore, Item
from sqlalchemy.orm import Session

from my_agents.memory.models import UserMemoryModel
from my_agents.memory.service import UserMemoryService, user_memory_namespace

MEMORY_STORE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MemoryProjectionReport:
    expected: int
    actual: int
    missing: int
    stale: int
    orphaned: int
    applied_upserts: int = 0
    applied_deletes: int = 0

    @property
    def drift(self) -> int:
        return self.missing + self.stale + self.orphaned


def project_memory(store: BaseStore, memory: UserMemoryModel) -> None:
    """Idempotently write one canonical eligible memory to Store."""
    store.put(
        user_memory_namespace(memory.user_id, memory.category),
        memory.key,
        memory_store_value(memory),
        index=["content"],
    )


def delete_projected_memory(store: BaseStore, memory: UserMemoryModel) -> None:
    """Idempotently remove one memory projection."""
    store.delete(user_memory_namespace(memory.user_id, memory.category), memory.key)


def reconcile_memory_store(
    *,
    db: Session,
    store: BaseStore,
    apply: bool = False,
    user_id: str | None = None,
) -> MemoryProjectionReport:
    """Compare the governed ledger with Store and optionally repair all drift."""
    memories = UserMemoryService(db).eligible_memories_for_store(user_id=user_id)
    expected = {
        (user_memory_namespace(memory.user_id, memory.category), memory.key): memory
        for memory in memories
    }
    actual = _all_projected_items(store, user_id=user_id)
    missing_keys = expected.keys() - actual.keys()
    orphaned_keys = actual.keys() - expected.keys()
    stale_keys = {
        key
        for key in expected.keys() & actual.keys()
        if actual[key].value.get("content_sha256") != _content_sha256(expected[key].content)
        or actual[key].value.get("schema_version") != MEMORY_STORE_SCHEMA_VERSION
    }
    applied_upserts = 0
    applied_deletes = 0
    if apply:
        for key in sorted(missing_keys | stale_keys):
            project_memory(store, expected[key])
            applied_upserts += 1
        for namespace, key in sorted(orphaned_keys):
            store.delete(namespace, key)
            applied_deletes += 1
    return MemoryProjectionReport(
        expected=len(expected),
        actual=len(actual),
        missing=len(missing_keys),
        stale=len(stale_keys),
        orphaned=len(orphaned_keys),
        applied_upserts=applied_upserts,
        applied_deletes=applied_deletes,
    )


def memory_store_value(memory: UserMemoryModel) -> dict[str, object]:
    return {
        "schema_version": MEMORY_STORE_SCHEMA_VERSION,
        "memory_id": memory.id,
        "content": memory.content,
        "content_sha256": _content_sha256(memory.content),
        "category": memory.category,
        "provenance_type": memory.provenance_type,
        "source_conversation_id": memory.source_conversation_id,
        "source_message_id": memory.source_message_id,
        "source_run_id": memory.source_run_id,
        "source_document_id": memory.source_document_id,
        "updated_at": memory.updated_at.isoformat(),
    }


def _all_projected_items(
    store: BaseStore,
    *,
    user_id: str | None,
) -> dict[tuple[tuple[str, ...], str], Item]:
    namespaces: list[tuple[str, ...]] = []
    offset = 0
    while True:
        page = store.list_namespaces(
            prefix=(user_id, "memories") if user_id else None,
            limit=100,
            offset=offset,
        )
        namespaces.extend(page)
        if len(page) < 100:
            break
        offset += len(page)
    actual: dict[tuple[tuple[str, ...], str], Item] = {}
    for namespace in namespaces:
        if len(namespace) != 3 or namespace[1] != "memories":
            continue
        item_offset = 0
        while True:
            page = store.search(namespace, limit=100, offset=item_offset)
            for item in page:
                actual[(tuple(item.namespace), item.key)] = item
            if len(page) < 100:
                break
            item_offset += len(page)
    return actual


def _content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
