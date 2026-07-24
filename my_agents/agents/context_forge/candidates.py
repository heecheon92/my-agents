"""Candidate Scout roles for ContextForge."""

from __future__ import annotations

from my_agents.agents.context_forge.contracts import RetrievalPlan
from my_agents.knowledge.retrieval import RetrievalService, RetrievedChunk


class CandidateScouts:
    """Gather authorized vector/lexical/entity and structured candidates."""

    def __init__(self, retrieval_service: RetrievalService) -> None:
        self._retrieval_service = retrieval_service

    def gather(
        self,
        *,
        user_id: str,
        plan: RetrievalPlan,
        knowledge_base_ids: tuple[str, ...] | None,
    ) -> list[RetrievedChunk]:
        chunks = self._retrieval_service.retrieve_scoped(
            user_id=user_id,
            query=plan.rewritten_query,
            limit=plan.limits.vector_limit,
            knowledge_base_ids=knowledge_base_ids,
            hybrid_search=True,
        )
        lexical_chunks = self._retrieval_service.retrieve_lexical_scoped(
            user_id=user_id,
            query=plan.rewritten_query,
            limit=plan.limits.lexical_limit,
            knowledge_base_ids=knowledge_base_ids,
        )
        if not plan.structured_entity_types:
            return [*chunks, *lexical_chunks]
        structured = self._retrieval_service.retrieve_structured_entities(
            user_id=user_id,
            query=plan.rewritten_query,
            entity_types=plan.structured_entity_types,
            limit=plan.limits.structured_limit,
            knowledge_base_ids=knowledge_base_ids,
        )
        structured_chunks = [
            RetrievedChunk(
                chunk=item.chunk,
                document=item.document,
                score=item.score,
                source=f"structured_entity:{item.entity.entity_type}",
            )
            for item in structured
        ]
        return [*structured_chunks, *chunks, *lexical_chunks]
