"""Runtime boundary for graph-owned long-term memory recall."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langgraph.store.base import BaseStore
from sqlalchemy.orm import Session

from my_agents.memory.models import MemoryCategory, UserMemoryModel
from my_agents.memory.service import UserMemoryService


@dataclass(frozen=True)
class MemoryRuntimeItem:
    """Compact memory item shape allowed to enter graph/provider context."""

    id: str
    key: str
    category: str
    content: str
    provenance_type: str
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    source_run_id: str | None = None
    source_document_id: str | None = None


class MemoryRuntime(Protocol):
    """LangGraph-facing memory runtime abstraction.

    The current adapter is Product DB-backed, but graph code depends on this boundary so
    active memory search can later move to LangGraph Store without changing responder code.
    """

    def search(
        self,
        *,
        user_id: str,
        query: str,
        categories: list[MemoryCategory | str] | None = None,
        limit: int = 8,
        store: BaseStore | None = None,
    ) -> list[MemoryRuntimeItem]:
        """Return provider-eligible active memories for one user/query."""
        ...


class SqlAlchemyMemoryRuntime:
    """Product DB-backed V1 adapter behind the LangGraph memory runtime boundary."""

    def __init__(self, db: Session) -> None:
        self._service = UserMemoryService(db)

    def search(
        self,
        *,
        user_id: str,
        query: str,
        categories: list[MemoryCategory | str] | None = None,
        limit: int = 8,
        store: BaseStore | None = None,
    ) -> list[MemoryRuntimeItem]:
        if store is not None:
            candidates = store.search(
                (user_id, "memories"),
                query=query or None,
                limit=max(limit * 4, limit),
            )
            memory_ids = [
                str(item.value["memory_id"])
                for item in candidates
                if isinstance(item.value.get("memory_id"), str)
                and (
                    not categories
                    or item.value.get("category")
                    in {
                        category.value if isinstance(category, MemoryCategory) else str(category)
                        for category in categories
                    }
                )
            ]
            memories = self._service.active_memories_by_ids_for_context(
                user_id=user_id,
                memory_ids=memory_ids,
            )[:limit]
            return [memory_runtime_item_from_model(memory) for memory in memories]
        memories = self._service.active_memories_for_context(
            user_id=user_id,
            categories=categories,
            query=query,
            limit=limit,
        )
        return [memory_runtime_item_from_model(memory) for memory in memories]


def memory_runtime_item_from_model(memory: UserMemoryModel) -> MemoryRuntimeItem:
    return MemoryRuntimeItem(
        id=memory.id,
        key=memory.key,
        category=memory.category,
        content=memory.content,
        provenance_type=memory.provenance_type,
        source_conversation_id=memory.source_conversation_id,
        source_message_id=memory.source_message_id,
        source_run_id=memory.source_run_id,
        source_document_id=memory.source_document_id,
    )


def memory_item_context(memory: MemoryRuntimeItem) -> dict[str, object]:
    """Serialize a runtime memory item into graph/provider context."""
    return {
        "id": memory.id,
        "key": memory.key,
        "category": memory.category,
        "content": memory.content,
        "provenance_type": memory.provenance_type,
        "source_conversation_id": memory.source_conversation_id,
        "source_message_id": memory.source_message_id,
        "source_run_id": memory.source_run_id,
        "source_document_id": memory.source_document_id,
    }
