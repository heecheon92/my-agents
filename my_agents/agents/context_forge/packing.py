"""Context Curator role for ContextForge."""

from __future__ import annotations

from collections.abc import Sequence

from my_agents.agents.context_forge.contracts import (
    RejectedCandidateSummary,
    RetrievalCandidate,
    RetrievalPlan,
)
from my_agents.knowledge.retrieval import RetrievedChunk


class ContextCurator:
    """Pack high-recall candidates under explicit context budgets."""

    def pack(
        self,
        *,
        plan: RetrievalPlan,
        candidates: Sequence[RetrievalCandidate],
    ) -> tuple[list[RetrievedChunk], tuple[RejectedCandidateSummary, ...], bool]:
        injected: list[RetrievedChunk] = []
        rejected: list[RejectedCandidateSummary] = []
        used_chars = 0
        for candidate in candidates:
            next_chars = len(candidate.chunk.chunk.content)
            if len(injected) >= plan.limits.injected_limit:
                rejected.append(_rejected(candidate, "injected_limit"))
                continue
            if injected and used_chars + next_chars > plan.limits.char_budget:
                rejected.append(_rejected(candidate, "char_budget"))
                continue
            injected.append(candidate.chunk)
            used_chars += next_chars
        return injected, tuple(rejected), bool(rejected)


def _rejected(candidate: RetrievalCandidate, reason: str) -> RejectedCandidateSummary:
    return RejectedCandidateSummary(
        chunk_id=candidate.chunk.chunk.id,
        document_id=candidate.chunk.document.id,
        reason=reason,
    )
