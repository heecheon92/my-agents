"""Typed contracts for the RAG Agent workflow surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute

RagAgentStageId = Literal[
    "query_cartographer",
    "source_warden",
    "candidate_scouts",
    "evidence_judge",
    "context_curator",
    "assistant_graph",
    "answer_composer",
]
RagAgentStageStatus = Literal["completed", "skipped", "waiting", "failed"]
RagAgentAgentRole = Literal["retrieval_agent", "assistant_agent"]

RETRIEVAL_AGENT_NAME = "ContextForge"
ASSISTANT_AGENT_NAME = "GeneralAssistantGraph"
EXPECTED_STAGE_ORDER: tuple[RagAgentStageId, ...] = (
    "query_cartographer",
    "source_warden",
    "candidate_scouts",
    "evidence_judge",
    "context_curator",
    "assistant_graph",
    "answer_composer",
)


@dataclass(frozen=True)
class LocalizedRagAgentText:
    """Bilingual frontend copy for compact trace rendering."""

    en: str
    ko: str


@dataclass(frozen=True)
class RagAgentStage:
    """One redacted workflow stage exposed to API/SSE clients."""

    id: RagAgentStageId
    role: RagAgentAgentRole
    agent_name: str
    status: RagAgentStageStatus
    title: LocalizedRagAgentText
    description: LocalizedRagAgentText
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RagAgentWorkflowPlan:
    """Deterministic plan for a conversation run's RAG Agent workflow."""

    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    stages: tuple[RagAgentStage, ...]


@dataclass(frozen=True)
class RagAgentVerification:
    """Result from deterministic workflow contract verification."""

    passed: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RagAgentGroundingVerification:
    """Result from deterministic answer-grounding boundary verification."""

    passed: bool
    errors: tuple[str, ...] = ()
