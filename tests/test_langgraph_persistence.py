"""Gated PostgreSQL restart smoke for run-scoped LangGraph checkpoints."""

from __future__ import annotations

import os
import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from my_agents.agents.general_assistant.graph import build_graph
from my_agents.agents.general_assistant.retrieval_gate import RetrievalSourceDecision
from my_agents.agents.rag_agent import RagAgentRetrievalResult
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import (
    AuthorizedDocumentOption,
    FullDocumentTargetResolution,
    RankedAuthorizedDocumentOption,
)
from my_agents.knowledge.routing import RetrievalRoutingDecision
from my_agents.persistence.langgraph import open_langgraph_persistence
from my_agents.settings import Settings


def test_postgres_persistence_is_automatic_and_uses_embedding_provider_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakePool:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            captured["pool_kwargs"] = kwargs

        def open(self, *, wait: bool) -> None:
            captured["pool_open_wait"] = wait

        def close(self) -> None:
            captured["pool_closed"] = True

    class FakeSaver:
        def __init__(self, pool, *, serde) -> None:  # noqa: ANN001
            captured["saver_pool"] = pool
            captured["saver_serde"] = serde

    class FakeStore:
        def __init__(self, pool, *, index) -> None:  # noqa: ANN001
            captured["store_pool"] = pool
            captured["store_index"] = index

    monkeypatch.setattr("my_agents.persistence.langgraph.ConnectionPool", FakePool)
    monkeypatch.setattr("my_agents.persistence.langgraph.PostgresSaver", FakeSaver)
    monkeypatch.setattr("my_agents.persistence.langgraph.PostgresStore", FakeStore)
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_DATABASE_URL="postgresql+psycopg://app:pw@localhost/app",
        MY_AGENTS_MEMORY_STORE_EMBEDDING_DIMENSIONS=1536,
    )

    resources = open_langgraph_persistence(settings)

    assert resources.checkpointer is not None
    assert resources.store is not None
    assert captured["store_index"]["dims"] == 32  # type: ignore[index]
    resources.close()
    assert captured["pool_closed"] is True


def test_postgres_checkpoint_resumes_document_selection_after_resource_restart() -> None:
    database_url = os.getenv("MY_AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("MY_AGENTS_TEST_DATABASE_URL is required for persistence integration smoke")
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_DATABASE_URL=database_url,
    )
    run_id = f"test-{uuid.uuid4()}"
    context = {
        "user_id": "persistence-user",
        "rag_runtime": _ClarifyingRuntime(),
        "retrieval_source_decider": _KnowledgeBaseDecider(),
        "knowledge_base_selection": KnowledgeBaseSelectionContext(
            mode="all", knowledge_base_ids=(), resolved_count=0
        ),
    }
    config = {"configurable": {"thread_id": run_id}}

    first_resources = open_langgraph_persistence(settings)
    assert first_resources.checkpointer is not None
    assert first_resources.store is not None
    first_resources.checkpointer.setup()
    first_graph = build_graph(
        checkpointer=first_resources.checkpointer,
        document_selection_hitl_enabled=True,
    )
    interrupted = first_graph.invoke(
        {
            "messages": [HumanMessage(content="Summarize this document")],
            "principal_id": "persistence-user",
            "conversation_id": "persistence-conversation",
            "run_id": run_id,
        },
        config=config,
        context=context,
    )
    assert interrupted["__interrupt__"]
    first_resources.close()

    second_resources = open_langgraph_persistence(settings)
    assert second_resources.checkpointer is not None
    second_graph = build_graph(
        checkpointer=second_resources.checkpointer,
        document_selection_hitl_enabled=True,
    )
    resumed = second_graph.invoke(
        Command(resume={"document_id": "doc-restart"}),
        config=config,
        context=context,
    )
    assert resumed["selected_document_id"] == "doc-restart"
    second_resources.checkpointer.delete_thread(run_id)
    second_resources.close()


class _KnowledgeBaseDecider:
    def decide(self, **kwargs):  # noqa: ANN003, ANN201
        return RetrievalSourceDecision(source="knowledge_base", reason="integration test")


class _ClarifyingRuntime:
    def resolve_full_document_target(self, **kwargs):  # noqa: ANN003, ANN201
        """Model an unresolved V2 shortlist without database or provider dependencies."""
        return FullDocumentTargetResolution(
            target=None,
            option_count=2,
            library_count=2,
            candidates=tuple(
                RankedAuthorizedDocumentOption(
                    document_id=document_id,
                    title=title,
                    source_filename=f"{document_id}.txt",
                    knowledge_base_id="kb-restart",
                    knowledge_base_name="Restart KB",
                    score=0.5,
                    matched_tokens=1,
                    match_confidence="low",
                    match_reason_code="metadata_overlap",
                )
                for document_id, title in (
                    ("doc-restart", "Restart source"),
                    ("doc-alternative", "Alternative source"),
                )
            ),
        )

    def retrieve_context(self, **kwargs):  # noqa: ANN003, ANN201
        selected = kwargs.get("selected_document_id")
        return RagAgentRetrievalResult(
            decision=RetrievalRoutingDecision(
                route="retrieval_required" if selected else "clarification_required",
                reason="integration test",
                rewritten_query=kwargs["message"],
                document_scope="user_documents" if selected else "unknown",
            ),
            answer_mode="general_knowledge",
            retrieved_chunks=[],
            retrieval_latency_ms=0.0,
            knowledge_base_selection=kwargs["selection_context"],
            insufficient_evidence=bool(selected),
        )

    def document_options(self, **kwargs):  # noqa: ANN003, ANN201
        return (
            [
                AuthorizedDocumentOption(
                    document_id="doc-restart",
                    title="Restart source",
                    source_filename="restart.txt",
                    knowledge_base_id="kb-restart",
                    knowledge_base_name="Restart KB",
                )
            ],
            1,
        )
