"""Typed contracts for the ContextForge retrieval-agent suite."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import BaseMessage

from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision

RetrievalIntent = Literal[
    "semantic_qa",
    "overview",
    "comprehensive_document",
    "enumeration",
    "comparison",
    "source_lookup",
]
QualityMode = Literal["balanced", "high_recall"]


@dataclass(frozen=True)
class CandidateLimits:
    """Bound high-recall retrieval so quality is explicit rather than unmetered."""

    vector_limit: int = 20
    lexical_limit: int = 20
    structured_limit: int = 80
    rerank_limit: int = 40
    injected_limit: int = 12
    char_budget: int = 24_000


@dataclass(frozen=True)
class ContextForgeRequest:
    """Input from a conversation run into ContextForge."""

    user_id: str
    conversation_id: str
    query: str
    messages: Sequence[BaseMessage]
    selection_context: KnowledgeBaseSelectionContext
    selected_document_id: str | None = None
    quality_mode: QualityMode = "high_recall"


@dataclass(frozen=True)
class RetrievalPlan:
    """Query Cartographer output used by downstream retrieval roles."""

    intent: RetrievalIntent
    original_query: str
    rewritten_query: str
    route_decision: RetrievalRoutingDecision
    expansion_terms: tuple[str, ...] = ()
    structured_entity_types: tuple[str, ...] = ()
    use_hyde: bool = False
    limits: CandidateLimits = field(default_factory=CandidateLimits)


@dataclass(frozen=True)
class RetrievalCandidate:
    """Transparent candidate wrapper used by fusion, reranking, and observability."""

    chunk: RetrievedChunk
    sources: tuple[str, ...]
    score: float
    rerank_score: float | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RejectedCandidateSummary:
    """Redacted reason why a candidate was not injected into answer context."""

    chunk_id: str
    document_id: str
    reason: str


@dataclass(frozen=True)
class RetrievalEvidence:
    """Redacted retrieval facts safe for events, evals, and debugging."""

    intent: RetrievalIntent
    candidate_count: int
    injected_count: int
    rejected_count: int
    source_counts: dict[str, int]
    structured_entity_types: tuple[str, ...]
    reranker: str = "deterministic"
    budget_truncated: bool = False


@dataclass(frozen=True)
class ContextForgeResult:
    """Complete retrieval-layer output for conversation-run orchestration."""

    plan: RetrievalPlan
    decision: RetrievalRoutingDecision
    answer_mode: AnswerMode
    retrieved_chunks: list[RetrievedChunk]
    retrieval_latency_ms: float
    evidence: RetrievalEvidence
    rejected_candidates: tuple[RejectedCandidateSummary, ...] = ()
