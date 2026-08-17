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
from my_agents.knowledge.retrieval import AuthorizedDocumentOption
from my_agents.knowledge.routing import RetrievalRoutingDecision
from my_agents.persistence.langgraph import open_langgraph_persistence
from my_agents.settings import Settings


def test_postgres_checkpoint_resumes_document_selection_after_resource_restart() -> None:
    database_url = os.getenv("MY_AGENTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("MY_AGENTS_TEST_DATABASE_URL is required for persistence integration smoke")
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_DATABASE_URL=database_url,
        MY_AGENTS_CHECKPOINTER_ENABLED=True,
        MY_AGENTS_MEMORY_STORE_ENABLED=False,
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
