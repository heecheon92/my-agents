"""Response composition providers for the assistant graph."""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from my_agents.agents.capabilities import AgentCapability
from my_agents.knowledge.routing import AnswerMode
from my_agents.schemas import RouteDecision
from my_agents.settings import Settings, get_settings

_SYSTEM_PROMPT = (
    "You are the reply-generation component of a backend-only FastAPI + LangGraph "
    "assistant backend. Be concise, practical, and helpful. Preserve the provided route "
    "label as metadata; do not claim that a separate specialized agent ran."
)
_WEB_SEARCH_TOOL = {"type": "web_search"}
_GENERAL_ASSISTANT_WEB_SEARCH_HINTS = (
    "current",
    "currently",
    "latest",
    "recent",
    "recently",
    "today",
    "this week",
    "this month",
    "this year",
    "news",
    "web",
    "internet",
    "online",
    "search",
    "browse",
    "look up",
    "find source",
    "find sources",
    "source",
    "sources",
    "citation",
    "citations",
    "docs",
    "documentation",
    "2025",
    "2026",
)


class ResponseProviderError(RuntimeError):
    """Base error for response-provider failures."""


class ResponseProviderConfigurationError(ResponseProviderError):
    """Raised when runtime settings are insufficient for the selected provider."""


class ResponseProvider(Protocol):
    """Minimal interface used by LangGraph response nodes."""

    def compose_reply(
        self,
        *,
        messages: Sequence[BaseMessage],
        route: RouteDecision,
        guidance: str,
        capability: AgentCapability | None = None,
        retrieved_context: Sequence[dict[str, Any]] = (),
        answer_mode: AnswerMode = "general_knowledge",
        debug_empty_response: bool = False,
    ) -> str:
        """Return a user-facing reply for the classified request."""
        ...


class DeterministicResponseProvider:
    """Credential-free response composer used by default and in tests."""

    def compose_reply(
        self,
        *,
        messages: Sequence[BaseMessage],
        route: RouteDecision,
        guidance: str,
        capability: AgentCapability | None = None,
        retrieved_context: Sequence[dict[str, Any]] = (),
        answer_mode: AnswerMode = "general_knowledge",
        debug_empty_response: bool = False,
    ) -> str:
        _ = debug_empty_response
        _ = messages
        context_sentence = _deterministic_context_sentence(retrieved_context, answer_mode)
        capability_sentence = _deterministic_capability_sentence(capability)
        return (
            f"Classified as route label `{route.label}`. {capability_sentence}"
            f"{context_sentence}{guidance} "
            "This backend is running in deterministic response mode."
        )


class OpenAIResponseProvider:
    """OpenAI GPT response composer backed by LangChain's `langchain-openai` package."""

    def __init__(self, settings: Settings, chat_model: Any | None = None) -> None:
        self._settings = settings
        api_key = settings.openai_api_key_value()
        if chat_model is None and not api_key:
            raise ResponseProviderConfigurationError(
                "OPENAI_API_KEY is required when MY_AGENTS_RESPONSE_MODE=openai"
            )
        self._chat_model = chat_model or ChatOpenAI(**_build_chat_model_args(settings))

    def compose_reply(
        self,
        *,
        messages: Sequence[BaseMessage],
        route: RouteDecision,
        guidance: str,
        capability: AgentCapability | None = None,
        retrieved_context: Sequence[dict[str, Any]] = (),
        answer_mode: AnswerMode = "general_knowledge",
        debug_empty_response: bool = False,
    ) -> str:
        model = self._chat_model
        tools = _tools_for_route(route, messages)

        if tools:
            model = model.bind_tools(tools)

        response = model.invoke(
            _build_input_messages(
                messages=messages,
                route=route,
                guidance=guidance,
                capability=capability,
                retrieved_context=retrieved_context,
                answer_mode=answer_mode,
            )
        )
        return _extract_message_content(
            response,
            debug_empty_response=debug_empty_response,
        )


@lru_cache
def get_response_provider() -> ResponseProvider:
    """Build the provider selected by environment settings."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        raise ResponseProviderConfigurationError(str(exc)) from exc

    if settings.response_mode == "deterministic":
        return DeterministicResponseProvider()
    return OpenAIResponseProvider(settings)


def reset_response_provider_cache() -> None:
    """Clear provider cache after tests or local env changes."""
    get_response_provider.cache_clear()


def _build_input_messages(
    *,
    messages: Sequence[BaseMessage],
    route: RouteDecision,
    guidance: str,
    capability: AgentCapability | None = None,
    retrieved_context: Sequence[dict[str, Any]] = (),
    answer_mode: AnswerMode = "general_knowledge",
) -> list[BaseMessage]:
    recent_context = list(messages[-6:])
    latest_user_message = _latest_human_text(recent_context)
    provider_messages: list[BaseMessage] = [SystemMessage(content=_SYSTEM_PROMPT)]
    provider_messages.extend(recent_context[:-1])
    provider_messages.append(
        HumanMessage(
            content=(
                "Use the route metadata and guidance to answer the user.\n\n"
                f"Route label: {route.label}\n"
                f"Route explanation: {route.explanation}\n"
                f"{_capability_guidance(capability)}\n"
                f"Local guidance: {guidance}\n\n"
                f"Answer mode: {answer_mode}\n"
                f"Authorized document context: {_format_retrieved_context(retrieved_context)}\n\n"
                f"User message: {latest_user_message}\n\n"
                "Write one concise, actionable reply. In document_grounded mode, use "
                "authorized document context as the primary source. In mixed mode, use "
                "document context where relevant and supplement with general guidance. In "
                "general_knowledge mode, answer generally without claiming document grounding. "
                "When authorized document context is present and relevant, use it instead "
                "of saying you cannot access uploaded documents. "
                "If authorized context is insufficient for a document-grounded request, say "
                "what is missing. Use capability metadata to stay honest about available "
                "tools, data sources, and side effects. Do not invent completed actions, "
                "persistent memory, "
                "hidden tools, real-world side effects, or a frontend."
            )
        )
    )
    return provider_messages


def _format_retrieved_context(retrieved_context: Sequence[dict[str, Any]]) -> str:
    if not retrieved_context:
        return "none"
    lines = []
    for index, item in enumerate(retrieved_context, start=1):
        title = str(item.get("title") or "Untitled document")
        snippet = str(item.get("snippet") or "").strip()
        page = item.get("source_page")
        filename = item.get("source_filename")
        source_parts = [f"title={title!r}"]
        if filename:
            source_parts.append(f"file={filename!r}")
        if page is not None:
            source_parts.append(f"page={page}")
        lines.append(f"[{index}] " + ", ".join(source_parts) + f": {snippet}")
    return "\n".join(lines)


def _deterministic_context_sentence(
    retrieved_context: Sequence[dict[str, Any]], answer_mode: AnswerMode
) -> str:
    if not retrieved_context:
        return f"Answer mode `{answer_mode}`. "
    count = len(retrieved_context)
    return f"Answer mode `{answer_mode}` with {count} authorized document chunk(s). "


def _capability_guidance(capability: AgentCapability | None) -> str:
    if capability is None:
        return "Capability metadata: unavailable"
    return capability.guidance_text()


def _deterministic_capability_sentence(capability: AgentCapability | None) -> str:
    if capability is None:
        return ""
    side_effects = ", ".join(capability.side_effects) if capability.side_effects else "none"
    return f"Capability `{capability.name}` has side effects: {side_effects}. "


def _build_chat_model_args(settings: Settings) -> dict[str, Any]:
    """Map project env settings to `langchain-openai` `ChatOpenAI` init args."""
    args: dict[str, Any] = {
        "model": settings.openai_model,
        "api_key": settings.openai_api_key_value(),
        "timeout": settings.openai_timeout_seconds,
        "max_completion_tokens": settings.openai_max_output_tokens,
        "use_responses_api": True,
        "output_version": "responses/v1",
    }
    if settings.openai_reasoning_effort is not None:
        args["reasoning_effort"] = settings.openai_reasoning_effort
    if settings.openai_verbosity is not None:
        args["verbosity"] = settings.openai_verbosity
    return args


def _tools_for_route(
    route: RouteDecision,
    messages: Sequence[BaseMessage],
) -> list[dict[str, str]]:
    """Choose OpenAI hosted tools for a route without changing graph flow."""
    if route.label == "research_helper":
        return [_WEB_SEARCH_TOOL]
    if route.label == "general_assistant" and _latest_human_message_needs_web_search(messages):
        return [_WEB_SEARCH_TOOL]
    return []


def _latest_human_message_needs_web_search(messages: Sequence[BaseMessage]) -> bool:
    latest_user_message = _latest_human_text(messages).casefold()
    return any(hint in latest_user_message for hint in _GENERAL_ASSISTANT_WEB_SEARCH_HINTS)


def _latest_human_text(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return " ".join(parts)
    return str(content)


def _extract_message_content(
    response: Any,
    *,
    debug_empty_response: bool = False,
) -> str:
    """Extract text from LangChain AI messages and lightweight test doubles."""
    text = getattr(response, "text", "")
    if isinstance(text, str) and text.strip():
        return text.strip()

    text = _collect_text(response)
    if text:
        return text

    return _fallback_message_for_empty_openai_response(
        response,
        debug_empty_response=debug_empty_response,
    )


def _collect_text(value: Any) -> str:
    """Collect text from common LangChain/OpenAI response block shapes."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_collect_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        direct_text = value.get("text") or value.get("output_text")
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()

        nested_parts = []
        for key in ("content", "output", "message", "value"):
            if key in value:
                nested_text = _collect_text(value[key])
                if nested_text:
                    nested_parts.append(nested_text)
        return "\n".join(nested_parts)

    content = getattr(value, "content", None)
    additional_kwargs = getattr(value, "additional_kwargs", None)
    response_metadata = getattr(value, "response_metadata", None)

    parts = []
    for item in (content, additional_kwargs, response_metadata):
        nested_text = _collect_text(item)
        if nested_text:
            parts.append(nested_text)
    return "\n".join(parts)


def _fallback_message_for_empty_openai_response(
    response: Any,
    *,
    debug_empty_response: bool,
) -> str:
    """Return a fallback instead of crashing when OpenAI emits no text."""
    metadata = getattr(response, "response_metadata", {}) or {}
    status = metadata.get("status")
    incomplete_details = metadata.get("incomplete_details")

    details = []
    if status:
        details.append(f"status={status}")
    if incomplete_details:
        details.append(f"incomplete_details={incomplete_details}")

    suffix = f" ({'; '.join(details)})" if details else ""
    reason = _empty_response_failure_reason(status, incomplete_details)
    message = f"I could not extract a final text answer from the OpenAI response{suffix}. {reason}"
    if not debug_empty_response:
        return message

    return f"{message}\n\nRaw response object:\n```text\n{_debug_dump_response(response)}\n```"


def _empty_response_failure_reason(status: Any, incomplete_details: Any) -> str:
    """Explain why OpenAI returned no final text in user-facing language."""
    reason = None
    if isinstance(incomplete_details, dict):
        reason = incomplete_details.get("reason")

    if status == "incomplete" and reason == "max_output_tokens":
        return (
            "The model used the full output token budget before it produced a final answer. "
            "This can happen with web search because the model spends output tokens on "
            "reasoning and search steps first. Try increasing "
            "MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS, for example to 1200 or 2000, "
            "or ask a narrower question."
        )
    if status == "incomplete" and reason:
        return (
            f"The OpenAI response ended early because `{reason}`. "
            "Try again with a narrower question or adjust the relevant OpenAI setting."
        )
    if status == "incomplete":
        return "The OpenAI response ended early before producing final text."
    return "Please try again with a narrower question."


def _debug_dump_response(response: Any) -> str:
    """Serialize the full response object for local CLI debugging."""
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            return json.dumps(model_dump(mode="json"), indent=2, ensure_ascii=False)
        except TypeError:
            return json.dumps(model_dump(), indent=2, default=str, ensure_ascii=False)
    return repr(response)
