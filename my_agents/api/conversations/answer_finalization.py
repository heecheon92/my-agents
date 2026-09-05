"""Request-local answer preparation, independent of transport and persistence."""

from dataclasses import dataclass, field

from my_agents.agents.rag_agent import DeterministicRagAgentGroundingVerifier
from my_agents.api.conversations.retrieval_context import (
    ConversationRetrievalContext,
    chunks_consulted_for_answer,
    compose_rag_reply,
    document_coverage_from_graph_state,
    insufficient_evidence_reply,
)
from my_agents.conversations.schemas import DocumentCoverageResponse, ReasoningSummaryItem
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision
from my_agents.reasoning_summaries import summaries_from_graph_state

_GROUNDING_VERIFIER = DeterministicRagAgentGroundingVerifier()


@dataclass(frozen=True)
class PreparedAnswer:
    reply: str
    consulted_chunks: list[RetrievedChunk]
    document_coverage: DocumentCoverageResponse | None
    insufficient_evidence: bool
    memory_source_snapshot: str | None
    # Validation stays at the persistence call site through the property below.
    _graph_state: dict[str, object] = field(repr=False)

    @property
    def reasoning_summaries(self) -> list[ReasoningSummaryItem]:
        return reasoning_summary_items_from_graph_state(self._graph_state)


def prepare_answer(
    *,
    base_reply: str,
    retrieval_context: ConversationRetrievalContext,
    graph_state: dict[str, object],
    memory_source_snapshot: str | None,
) -> PreparedAnswer:
    """Apply existing composition and grounding once; never invoke providers or commit."""
    consulted_chunks = chunks_consulted_for_answer(retrieval_context)
    coverage = document_coverage_from_graph_state(graph_state)
    reply = compose_rag_reply(base_reply, consulted_chunks, retrieval_context.answer_mode)
    reply, consulted_chunks, insufficient = _verified_grounding_or_fallback(
        reply=reply,
        consulted_chunks=consulted_chunks,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
    )
    return PreparedAnswer(
        reply, consulted_chunks, coverage, insufficient, memory_source_snapshot, graph_state
    )


def _verified_grounding_or_fallback(
    *,
    reply: str,
    consulted_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    retrieval_attempt_count: int,
) -> tuple[str, list[RetrievedChunk], bool]:
    verification = _GROUNDING_VERIFIER.verify(
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        consulted_chunks=consulted_chunks,
        consulted_count=len(consulted_chunks),
        retrieval_attempt_count=retrieval_attempt_count,
    )
    if verification.passed:
        return reply, consulted_chunks, False
    if retrieval_decision.route == "retrieval_required" and retrieval_attempt_count >= 2:
        fallback_verification = _GROUNDING_VERIFIER.verify(
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            consulted_chunks=[],
            consulted_count=0,
            insufficient_evidence=True,
            retrieval_attempt_count=retrieval_attempt_count,
        )
        if fallback_verification.passed:
            return insufficient_evidence_reply(), [], True
    errors = "; ".join(verification.errors)
    raise RuntimeError(f"RAG Agent grounding verification failed: {errors}")


def reasoning_summary_items_from_graph_state(
    state: dict[str, object] | None,
) -> list[ReasoningSummaryItem]:
    """Validate the compact graph fields at the public persistence boundary."""
    return [
        ReasoningSummaryItem.model_validate(item)
        for item in summaries_from_graph_state(state)  # type: ignore[arg-type]
    ]
