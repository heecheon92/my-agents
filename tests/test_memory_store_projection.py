"""Governed LangGraph Store projection tests."""

from langgraph.store.memory import InMemoryStore

from my_agents.knowledge.embeddings import DeterministicEmbeddingProvider
from my_agents.memory.runtime import SqlAlchemyMemoryRuntime
from my_agents.memory.service import UserMemoryService
from my_agents.memory.store_projection import reconcile_memory_store
from my_agents.persistence.database import get_database_session


def test_memory_store_reconciliation_and_governed_recall(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    provider = DeterministicEmbeddingProvider()
    store = InMemoryStore(
        index={
            "dims": provider.dimensions,
            "embed": provider.embed_documents,
            "fields": ["content"],
        }
    )
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = UserMemoryService(db)
        service.set_enabled("store-user", True)
        memory = service.store_explicit_memory(
            user_id="store-user",
            content="User prefers Korean technical explanations",
            category="stable_preference",
        )

        dry_run = reconcile_memory_store(db=db, store=store)
        assert dry_run.missing == 1
        applied = reconcile_memory_store(db=db, store=store, apply=True)
        assert applied.applied_upserts == 1
        assert reconcile_memory_store(db=db, store=store).drift == 0

        recalled = SqlAlchemyMemoryRuntime(db).search(
            user_id="store-user",
            query="Which language should explanations use?",
            store=store,
        )
        assert [item.id for item in recalled] == [memory.id]

        service.deactivate_memory(user_id="store-user", memory_id=memory.id)
        drift = reconcile_memory_store(db=db, store=store)
        assert drift.orphaned == 1
        reconcile_memory_store(db=db, store=store, apply=True)
        assert reconcile_memory_store(db=db, store=store).drift == 0
    finally:
        session_generator.close()
