"""Typed contracts for the Agentic RAG workflow surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute

AgenticRagStageId = Literal[
    "query_cartographer",
    "source_warden",
    "candidate_scouts",
    "evidence_judge",
    "context_curator",
    "assistant_graph",
    "answer_composer",
]
AgenticRagStageStatus = Literal["completed", "skipped", "waiting", "failed"]
AgenticRagAgentRole = Literal["retrieval_agent", "assistant_agent"]

RETRIEVAL_AGENT_NAME = "ContextForge"
ASSISTANT_AGENT_NAME = "GeneralAssistantGraph"
EXPECTED_STAGE_ORDER: tuple[AgenticRagStageId, ...] = (
    "query_cartographer",
    "source_warden",
    "candidate_scouts",
    "evidence_judge",
    "context_curator",
    "assistant_graph",
    "answer_composer",
)


@dataclass(frozen=True)
class LocalizedAgenticRagText:
    """Bilingual frontend copy for compact trace rendering."""

    en: str
    ko: str


@dataclass(frozen=True)
class AgenticRagStage:
    """One redacted workflow stage exposed to API/SSE clients."""

    id: AgenticRagStageId
    role: AgenticRagAgentRole
    agent_name: str
    status: AgenticRagStageStatus
    title: LocalizedAgenticRagText
    description: LocalizedAgenticRagText
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgenticRagWorkflowPlan:
    """Deterministic plan for a conversation run's agentic RAG workflow."""

    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    stages: tuple[AgenticRagStage, ...]


@dataclass(frozen=True)
class AgenticRagVerification:
    """Result from deterministic workflow contract verification."""

    passed: bool
    errors: tuple[str, ...] = ()
