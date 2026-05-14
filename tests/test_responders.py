"""Response provider tests that do not call external services."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from my_agents.responders import (
    DeterministicResponseProvider,
    OpenAIResponseProvider,
    _build_chat_model_args,
)
from my_agents.schemas import RouteDecision
from my_agents.settings import Settings


class FakeChatModel:
    def __init__(self) -> None:
        self.calls: list[list[BaseMessage]] = []

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content="LangChain OpenAI drafted reply.")


def test_deterministic_provider_is_credential_free() -> None:
    provider = DeterministicResponseProvider()
    route = RouteDecision(label="project_planner", explanation="planning request")

    reply = provider.compose_reply(
        message="Plan the next milestone",
        history=[],
        route=route,
        guidance="Break the work into one verifiable milestone.",
    )

    assert "project_planner" in reply
    assert "deterministic response mode" in reply


def test_openai_provider_passes_gpt_variant_and_optional_tuning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MY_AGENTS_OPENAI_MODEL", "gpt-5.5")
    monkeypatch.setenv("MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS", "123")
    monkeypatch.setenv("MY_AGENTS_OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("MY_AGENTS_OPENAI_VERBOSITY", "low")
    settings = Settings(_env_file=None)
    chat_model = FakeChatModel()
    provider = OpenAIResponseProvider(settings=settings, chat_model=chat_model)
    route = RouteDecision(label="learning_coach", explanation="study request")

    reply = provider.compose_reply(
        message="Help me learn LangGraph",
        history=[],
        route=route,
        guidance="Define, build, and test one tiny example.",
    )

    assert reply == "LangChain OpenAI drafted reply."
    assert len(chat_model.calls) == 1
    messages = chat_model.calls[0]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[-1], HumanMessage)
    assert "Route label: learning_coach" in str(messages[-1].content)

    model_args = _build_chat_model_args(settings)
    assert model_args["model"] == "gpt-5.5"
    assert model_args["max_completion_tokens"] == 123
    assert model_args["use_responses_api"] is True
    assert model_args["reasoning_effort"] == "low"
    assert model_args["verbosity"] == "low"
