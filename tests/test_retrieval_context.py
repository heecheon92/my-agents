"""Conversation retrieval-context packing tests."""

from __future__ import annotations

from my_agents.api.conversations.retrieval_context import retrieved_context_for_graph
from my_agents.knowledge.models import DocumentChunkModel, DocumentModel
from my_agents.knowledge.retrieval import RetrievedChunk


def test_retrieved_context_injects_broader_snippet_for_larger_chunks() -> None:
    content = "A" * 1300
    document = DocumentModel(
        id="doc-1",
        title="Long chunk doc",
        content=content,
        owner_user_id="user-1",
        knowledge_base_id="kb-1",
        source_filename="long.pdf",
    )
    chunk = DocumentChunkModel(
        id="chunk-1",
        document_id=document.id,
        extraction_run_id="run-1",
        ordinal=0,
        content=content,
        start_offset=0,
        end_offset=len(content),
        source_page=3,
        embedding_json="[]",
    )

    context = retrieved_context_for_graph(
        [RetrievedChunk(chunk=chunk, document=document, score=0.9, source="semantic_vector")]
    )

    assert len(context[0]["snippet"]) == 1200
    assert context[0]["source_page"] == 3
    assert context[0]["source_filename"] == "long.pdf"
