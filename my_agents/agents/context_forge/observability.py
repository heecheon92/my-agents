"""Citation Auditor observability helpers for ContextForge."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from my_agents.agents.context_forge.contracts import (
    RetrievalCandidate,
    RetrievalEvidence,
    RetrievalPlan,
)
from my_agents.knowledge.retrieval import RetrievedChunk


def build_retrieval_evidence(
    *,
    plan: RetrievalPlan,
    candidates: Sequence[RetrievalCandidate],
    injected_chunks: Sequence[RetrievedChunk],
    rejected_count: int,
    budget_truncated: bool,
    reranker_name: str,
) -> RetrievalEvidence:
    source_counts: Counter[str] = Counter()
    for candidate in candidates:
        source_counts.update(candidate.sources)
    return RetrievalEvidence(
        intent=plan.intent,
        candidate_count=len(candidates),
        injected_count=len(injected_chunks),
        rejected_count=rejected_count,
        source_counts=dict(sorted(source_counts.items())),
        structured_entity_types=plan.structured_entity_types,
        reranker=reranker_name,
        budget_truncated=budget_truncated,
    )
