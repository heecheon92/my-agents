"""Deterministic verifier for RAG Agent workflow trace contracts."""

from __future__ import annotations

from collections.abc import Mapping

from my_agents.agents.rag_agent.contracts import (
    EXPECTED_STAGE_ORDER,
    RETRIEVAL_AGENT_NAME,
    RagAgentGroundingVerification,
    RagAgentStage,
    RagAgentVerification,
    RagAgentWorkflowPlan,
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


class DeterministicRagAgentVerifier:
    """Verify compact trace invariants before exposing them to clients."""

    def verify(self, plan: RagAgentWorkflowPlan) -> RagAgentVerification:
        errors: list[str] = []
        stage_ids = tuple(stage.id for stage in plan.stages)
        if stage_ids != EXPECTED_STAGE_ORDER:
            errors.append(f"unexpected stage order: {stage_ids!r}")
        for stage in plan.stages:
            errors.extend(_stage_errors(stage))
        return RagAgentVerification(passed=not errors, errors=tuple(errors))


class DeterministicRagAgentGroundingVerifier:
    """Verify deterministic citation/evidence invariants before completion.

    This is not a semantic model-output judge. It enforces the v1 invariants the
    service can prove without provider calls: required-RAG completions must carry
    relevant authorized chunks into answer composition, safe fallback paths must not
    consult document evidence, and general-knowledge completions must not pretend to be grounded.
    """

    def verify(
        self,
        *,
        retrieval_decision: RetrievalRoutingDecision,
        answer_mode: AnswerMode,
        consulted_chunks: list[RetrievedChunk],
        consulted_count: int,
        insufficient_evidence: bool = False,
        clarification_required: bool = False,
        retrieval_attempt_count: int = 1,
    ) -> RagAgentGroundingVerification:
        errors: list[str] = []
        route = retrieval_decision.route
        relevant_chunk_count = sum(
            1
            for item in consulted_chunks
            if is_relevant_retrieval_result(
                route=route,
                source=item.source,
                score=item.score,
            )
        )

        if consulted_count != len(consulted_chunks):
            errors.append("consulted count must match consulted chunk count")
        if relevant_chunk_count != len(consulted_chunks):
            errors.append("all consulted chunks must be relevant for the retrieval route")

        if clarification_required:
            if consulted_chunks or consulted_count:
                errors.append("clarification runs must not consult document evidence")
            return RagAgentGroundingVerification(passed=not errors, errors=tuple(errors))

        if insufficient_evidence:
            if route != "retrieval_required":
                errors.append("insufficient evidence fallback is only valid for required retrieval")
            if consulted_chunks or consulted_count:
                errors.append("insufficient evidence fallback must not persist consulted evidence")
            if retrieval_attempt_count < 2:
                errors.append("required retrieval fallback must follow the bounded retry")
            return RagAgentGroundingVerification(passed=not errors, errors=tuple(errors))

        if answer_mode == "general_knowledge":
            if consulted_chunks or consulted_count:
                errors.append("general knowledge answers must not persist consulted evidence")
        elif not consulted_chunks:
            errors.append("document-grounded answers must consult at least one source")

        if route == "retrieval_required":
            if answer_mode != "document_grounded":
                errors.append("required retrieval completions must be document grounded")
            if not consulted_chunks or consulted_count < 1:
                errors.append("required retrieval completions must consult source evidence")

        return RagAgentGroundingVerification(passed=not errors, errors=tuple(errors))


def _stage_errors(stage: RagAgentStage) -> list[str]:
    errors: list[str] = []
    if stage.role == "retrieval_agent" and stage.agent_name != RETRIEVAL_AGENT_NAME:
        errors.append(f"{stage.id}: retrieval role must use {RETRIEVAL_AGENT_NAME}")
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
