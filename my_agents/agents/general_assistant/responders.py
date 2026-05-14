"""Response composition providers for the assistant graph."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from my_agents.schemas import RouteDecision
from my_agents.settings import Settings, get_settings

_SYSTEM_PROMPT = (
    "You are the reply-generation component of a backend-only FastAPI + LangGraph "
    "assistant backend. Be concise, practical, and helpful. Preserve the provided route "
    "label as metadata; do not claim that a separate specialized agent ran."
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
    ) -> str:
        _ = messages
        return (
            f"Classified as route label `{route.label}`. {guidance} "
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
    ) -> str:
        response = self._chat_model.invoke(
            _build_input_messages(
                messages=messages,
                route=route,
                guidance=guidance,
            )
        )
        return _extract_message_content(response)


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
                f"Local guidance: {guidance}\n\n"
                f"User message: {latest_user_message}\n\n"
                "Write one concise, actionable reply. Do not invent completed actions, "
                "persistent memory, hidden tools, or a frontend."
            )
        )
    )
    return provider_messages


def _build_chat_model_args(settings: Settings) -> dict[str, Any]:
    """Map project env settings to `langchain-openai` `ChatOpenAI` init args."""
    args: dict[str, Any] = {
        "model": settings.openai_model,
        "api_key": settings.openai_api_key_value(),
        "timeout": settings.openai_timeout_seconds,
        "max_completion_tokens": settings.openai_max_output_tokens,
        "use_responses_api": True,
    }
    if settings.openai_reasoning_effort is not None:
        args["reasoning_effort"] = settings.openai_reasoning_effort
    if settings.openai_verbosity is not None:
        args["verbosity"] = settings.openai_verbosity
    return args


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


def _extract_message_content(response: Any) -> str:
    """Extract text from LangChain AI messages and lightweight test doubles."""
    content = getattr(response, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        text_parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        text = "\n".join(part.strip() for part in text_parts if part.strip())
        if text:
            return text

    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    raise ResponseProviderError("LangChain OpenAI response did not include text content.")
