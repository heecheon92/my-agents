"""Deterministic verifier for Agentic RAG workflow trace contracts."""

from __future__ import annotations

from collections.abc import Mapping

from my_agents.agents.agentic_rag.contracts import (
    EXPECTED_STAGE_ORDER,
    RETRIEVAL_AGENT_NAME,
    AgenticRagStage,
    AgenticRagVerification,
    AgenticRagWorkflowPlan,
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
