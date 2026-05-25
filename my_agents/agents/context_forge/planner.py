"""Query Cartographer role for ContextForge."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import BaseMessage

from my_agents.agents.context_forge.contracts import CandidateLimits, RetrievalPlan
from my_agents.knowledge.routing import RetrievalRoutingDecision, route_retrieval

_ENUMERATION_HINTS = (
    "list",
    "show",
    "extract",
    "enumerate",
    "what are the",
    "목록",
    "나열",
    "보여줘",
    "추출",
)
_ENTITY_TYPE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("api_endpoint", ("endpoint", "endpoints", "route", "routes", "api", "get /", "post /")),
    ("config_key", ("env", "environment variable", "config", "setting", "환경변수", "설정")),
    ("command", ("command", "commands", "cli", "shell", "명령어")),
    ("error_code", ("error code", "status code", "http code", "에러", "오류", "상태 코드")),
    ("database_table", ("table", "tables", "database", "schema", "db", "테이블")),
)
_OVERVIEW_HINTS = ("summarize", "summary", "overview", "요약")
_COMPARISON_HINTS = ("compare", "difference", "vs", "versus", "비교", "차이")
_SOURCE_LOOKUP_HINTS = ("where", "source", "citation", "which document", "근거", "출처")


class QueryCartographer:
    """Deterministic retrieval planner with an OpenAI seam reserved for later."""

    def plan(
        self,
        *,
        message: str,
        history: Sequence[BaseMessage],
        authorized_document_count: int | None,
    ) -> RetrievalPlan:
        decision = route_retrieval(
            message=message,
            history=list(history),
            authorized_document_count=authorized_document_count,
        )
        normalized = message.casefold()
        structured_entity_types = _structured_entity_types(normalized)
        if _should_require_structured_retrieval(
            normalized=normalized,
            structured_entity_types=structured_entity_types,
            authorized_document_count=authorized_document_count,
            route=decision.route,
        ):
            decision = RetrievalRoutingDecision(
                route="retrieval_required",
                reason="structured enumeration request references document-backed material",
                rewritten_query=decision.rewritten_query,
                document_scope=decision.document_scope,
            )
        intent = _intent(normalized, structured_entity_types=structured_entity_types)
        return RetrievalPlan(
            intent=intent,
            original_query=message,
            rewritten_query=decision.rewritten_query,
            route_decision=decision,
            expansion_terms=(),
            structured_entity_types=structured_entity_types,
            use_hyde=False,
            limits=CandidateLimits(),
        )


def _intent(normalized: str, *, structured_entity_types: tuple[str, ...]) -> str:
    if structured_entity_types and _has_any(normalized, _ENUMERATION_HINTS):
        return "enumeration"
    if _has_any(normalized, _OVERVIEW_HINTS):
        return "overview"
    if _has_any(normalized, _COMPARISON_HINTS):
        return "comparison"
    if _has_any(normalized, _SOURCE_LOOKUP_HINTS):
        return "source_lookup"
    return "semantic_qa"


def _structured_entity_types(normalized: str) -> tuple[str, ...]:
    if not _has_any(normalized, _ENUMERATION_HINTS):
        return ()
    matches = [
        entity_type for entity_type, hints in _ENTITY_TYPE_HINTS if _has_any(normalized, hints)
    ]
    return tuple(dict.fromkeys(matches))


def _should_require_structured_retrieval(
    *,
    normalized: str,
    structured_entity_types: tuple[str, ...],
    authorized_document_count: int | None,
    route: str,
) -> bool:
    if not structured_entity_types or route not in {"no_retrieval", "retrieval_optional"}:
        return False
    if authorized_document_count is not None and authorized_document_count > 0:
        return True
    return _has_any(normalized, ("document", "file", "문서", "자료", "파일"))


def _has_any(value: str, hints: tuple[str, ...]) -> bool:
    return any(hint in value for hint in hints)
