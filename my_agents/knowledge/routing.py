"""Deterministic retrieval-routing policy for product conversation runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage

RetrievalRoute = Literal[
    "no_retrieval",
    "retrieval_required",
    "retrieval_optional",
    "clarification_required",
]
DocumentScope = Literal["current_conversation", "user_documents", "group_documents", "unknown"]
AnswerMode = Literal["general_knowledge", "document_grounded", "mixed"]


@dataclass(frozen=True)
class RetrievalRoutingDecision:
    """Decision describing whether a conversation run should use document retrieval."""

    route: RetrievalRoute
    reason: str
    rewritten_query: str
    document_scope: DocumentScope


_REQUIRED_DOCUMENT_HINTS = (
    "uploaded document",
    "uploaded file",
    "my document",
    "my documents",
    "my resume",
    "my cv",
    "about me",
    "resume",
    "cv",
    "contract",
    "agreement",
    "clause",
    "based on the document",
    "from the document",
    "in the document",
    "according to the document",
    "문서",
    "자료",
    "파일",
    "업로드",
    "이력서",
    "계약서",
    "조항",
    "기준으로",
    "근거로",
    "요약",
)
_AMBIGUOUS_DOCUMENT_REFERENCES = (
    "this document",
    "that document",
    "the document",
    "this file",
    "that file",
    "the file",
    "이 문서",
    "그 문서",
    "해당 문서",
    "이 자료",
    "그 자료",
    "해당 자료",
)
_OPTIONAL_RETRIEVAL_HINTS = (
    "our service",
    "our app",
    "our backend",
    "our api",
    "auth logic",
    "authentication logic",
    "system design",
    "architecture",
    "implementation",
    "codebase",
    "project",
    "서비스",
    "인증 로직",
    "백엔드",
    "아키텍처",
    "구현",
    "프로젝트",
)
_GENERAL_KNOWLEDGE_HINTS = (
    "what is rag",
    "what is r.a.g",
    "explain rag",
    "rag가 뭐야",
    "rag란",
    "rag 뭐야",
    "개념",
    "정의",
)
_GROUP_SCOPE_HINTS = ("group", "team", "shared", "우리 팀", "그룹", "공유")
_CURRENT_SCOPE_HINTS = ("current conversation", "this chat", "이 대화", "현재 대화")


def route_retrieval(
    *,
    message: str,
    history: list[BaseMessage] | None = None,
    authorized_document_count: int | None = None,
) -> RetrievalRoutingDecision:
    """Classify a request into a deterministic retrieval route.

    The policy asks whether searching already-authorized uploaded documents would improve
    answer reliability. It does not query storage; callers provide storage-derived counts
    only when ambiguity needs to be resolved safely.
    """
    query = _clean_query(message)
    normalized = _normalize(query)
    document_scope = _document_scope(normalized)

    if _has_any(normalized, _AMBIGUOUS_DOCUMENT_REFERENCES) and (
        authorized_document_count is not None and authorized_document_count > 1
    ):
        return RetrievalRoutingDecision(
            route="clarification_required",
            reason="ambiguous document reference with multiple authorized documents",
            rewritten_query=query,
            document_scope="unknown",
        )

    if _has_any(normalized, _REQUIRED_DOCUMENT_HINTS):
        return RetrievalRoutingDecision(
            route="retrieval_required",
            reason="request explicitly references uploaded or document-backed material",
            rewritten_query=query,
            document_scope=document_scope,
        )

    if _has_any(normalized, _GENERAL_KNOWLEDGE_HINTS):
        return RetrievalRoutingDecision(
            route="no_retrieval",
            reason="general conceptual request without document grounding hints",
            rewritten_query=query,
            document_scope="unknown",
        )

    if _has_any(normalized, _OPTIONAL_RETRIEVAL_HINTS) or _recent_history_mentions_documents(
        history or []
    ):
        return RetrievalRoutingDecision(
            route="retrieval_optional",
            reason="stored project or service context may improve answer reliability",
            rewritten_query=query,
            document_scope=document_scope,
        )

    if authorized_document_count is not None and authorized_document_count > 0:
        return RetrievalRoutingDecision(
            route="retrieval_optional",
            reason="authorized documents are available and may contain project-specific context",
            rewritten_query=query,
            document_scope=document_scope,
        )

    return RetrievalRoutingDecision(
        route="no_retrieval",
        reason="no document-retrieval hint detected",
        rewritten_query=query,
        document_scope="unknown",
    )


def answer_mode_for_route(
    *,
    decision: RetrievalRoutingDecision,
    relevant_context_found: bool,
) -> AnswerMode:
    """Map a retrieval decision plus relevance to answer-grounding mode."""
    if decision.route == "retrieval_required" and relevant_context_found:
        return "document_grounded"
    if decision.route == "retrieval_optional" and relevant_context_found:
        return "mixed"
    return "general_knowledge"


def is_relevant_retrieval_result(*, route: RetrievalRoute, source: str, score: float) -> bool:
    """Return whether one retrieved chunk should affect answer grounding."""
    if score <= 0:
        return False
    if source == "document_fallback" and route != "retrieval_required":
        return False
    return True


def _clean_query(message: str) -> str:
    return " ".join(message.strip().split())


def _normalize(value: str) -> str:
    return value.casefold()


def _has_any(value: str, hints: tuple[str, ...]) -> bool:
    return any(hint in value for hint in hints)


def _document_scope(normalized: str) -> DocumentScope:
    if _has_any(normalized, _CURRENT_SCOPE_HINTS):
        return "current_conversation"
    if _has_any(normalized, _GROUP_SCOPE_HINTS):
        return "group_documents"
    if any(hint in normalized for hint in ("my ", "내 ", "제가 ", "나의", "내가")):
        return "user_documents"
    return "unknown"


def _recent_history_mentions_documents(history: list[BaseMessage]) -> bool:
    for message in history[-4:]:
        if not isinstance(message, HumanMessage):
            continue
        content = message.content
        text = content if isinstance(content, str) else str(content)
        if _has_any(_normalize(text), _REQUIRED_DOCUMENT_HINTS):
            return True
    return False
