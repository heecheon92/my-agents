"""Deterministic verifier for Agentic RAG workflow trace contracts."""

from __future__ import annotations

from collections.abc import Mapping

from my_agents.agents.agentic_rag.contracts import (
    EXPECTED_STAGE_ORDER,
    RETRIEVAL_AGENT_NAME,
    AgenticRagGroundingVerification,
    AgenticRagStage,
    AgenticRagVerification,
    AgenticRagWorkflowPlan,
)
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import (
    AnswerMode,
    RetrievalRoutingDecision,
    is_relevant_retrieval_result,
)

_UNSAFE_EVIDENCE_KEYS = {
    "content",
    "full_text",
    "message",
    "prompt",
    "provider_error",
    "query",
    "raw_text",
    "snippet",
}
_MAX_STRING_EVIDENCE_CHARS = 160


class DeterministicAgenticRagVerifier:
    """Verify compact trace invariants before exposing them to clients."""

    def verify(self, plan: AgenticRagWorkflowPlan) -> AgenticRagVerification:
        errors: list[str] = []
        stage_ids = tuple(stage.id for stage in plan.stages)
        if stage_ids != EXPECTED_STAGE_ORDER:
            errors.append(f"unexpected stage order: {stage_ids!r}")
        for stage in plan.stages:
            errors.extend(_stage_errors(stage))
        return AgenticRagVerification(passed=not errors, errors=tuple(errors))


class DeterministicAgenticRagGroundingVerifier:
    """Verify deterministic citation/evidence invariants before completion.

    This is not a semantic model-output judge. It enforces the v1 invariants the
    service can prove without provider calls: required-RAG completions must carry
    relevant authorized chunks into citations, safe fallback paths must not cite
    anything, and general-knowledge completions must not pretend to be grounded.
    """

    def verify(
        self,
        *,
        retrieval_decision: RetrievalRoutingDecision,
        answer_mode: AnswerMode,
        cited_chunks: list[RetrievedChunk],
        citation_count: int,
        insufficient_evidence: bool = False,
        clarification_required: bool = False,
        retrieval_attempt_count: int = 1,
    ) -> AgenticRagGroundingVerification:
        errors: list[str] = []
        route = retrieval_decision.route
        relevant_chunk_count = sum(
            1
            for item in cited_chunks
            if is_relevant_retrieval_result(
                route=route,
                source=item.source,
                score=item.score,
            )
        )

        if citation_count != len(cited_chunks):
            errors.append("citation count must match cited chunk count")
        if relevant_chunk_count != len(cited_chunks):
            errors.append("all cited chunks must be relevant for the retrieval route")

        if clarification_required:
            if cited_chunks or citation_count:
                errors.append("clarification runs must not cite document evidence")
            return AgenticRagGroundingVerification(passed=not errors, errors=tuple(errors))

        if insufficient_evidence:
            if route != "retrieval_required":
                errors.append("insufficient evidence fallback is only valid for required retrieval")
            if cited_chunks or citation_count:
                errors.append("insufficient evidence fallback must not persist citations")
            if retrieval_attempt_count < 2:
                errors.append("required retrieval fallback must follow the bounded retry")
            return AgenticRagGroundingVerification(passed=not errors, errors=tuple(errors))

        if answer_mode == "general_knowledge":
            if cited_chunks or citation_count:
                errors.append("general knowledge answers must not persist document citations")
        elif not cited_chunks:
            errors.append("document-grounded answers must include at least one citation")

        if route == "retrieval_required":
            if answer_mode != "document_grounded":
                errors.append("required retrieval completions must be document grounded")
            if not cited_chunks or citation_count < 1:
                errors.append("required retrieval completions must include citations")

        return AgenticRagGroundingVerification(passed=not errors, errors=tuple(errors))


def _stage_errors(stage: AgenticRagStage) -> list[str]:
    errors: list[str] = []
    if stage.role == "retrieval_agent" and stage.agent_name != RETRIEVAL_AGENT_NAME:
        errors.append(f"{stage.id}: retrieval role must use ContextForge")
    if not stage.title.en or not stage.title.ko:
        errors.append(f"{stage.id}: missing localized title")
    if not stage.description.en or not stage.description.ko:
        errors.append(f"{stage.id}: missing localized description")
    errors.extend(_evidence_errors(stage.id, stage.evidence))
    return errors


def _evidence_errors(stage_id: str, evidence: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    for key, value in evidence.items():
        normalized_key = key.casefold()
        if normalized_key in _UNSAFE_EVIDENCE_KEYS:
            errors.append(f"{stage_id}: unsafe evidence key {key!r}")
        if isinstance(value, str) and len(value) > _MAX_STRING_EVIDENCE_CHARS:
            errors.append(f"{stage_id}: evidence value for {key!r} is too long")
    return errors
