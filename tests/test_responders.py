"""Response provider tests that do not call external services."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from my_agents.agents.general_assistant.responders import (
    DeterministicResponseProvider,
    OpenAIResponseProvider,
    _build_chat_model_args,
)
from my_agents.schemas import RouteDecision
from my_agents.settings import Settings


class FakeChatModel:
    def __init__(self) -> None:
        self.calls: list[list[BaseMessage]] = []
        self.bound_tools: list[list[dict[str, str]]] = []

    def bind_tools(self, tools: list[dict[str, str]]) -> FakeChatModel:
        self.bound_tools.append(tools)
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content="LangChain OpenAI drafted reply.")


def test_deterministic_provider_is_credential_free() -> None:
    provider = DeterministicResponseProvider()
    route = RouteDecision(label="project_planner", explanation="planning request")

    reply = provider.compose_reply(
        messages=[HumanMessage(content="Plan the next milestone")],
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
        messages=[HumanMessage(content="Help me learn LangGraph")],
        route=route,
        guidance="Define, build, and test one tiny example.",
    )

    assert reply == "LangChain OpenAI drafted reply."
    assert chat_model.bound_tools == []
    assert len(chat_model.calls) == 1
    messages = chat_model.calls[0]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[-1], HumanMessage)
    assert "Route label: learning_coach" in str(messages[-1].content)

    model_args = _build_chat_model_args(settings)
    assert model_args["model"] == "gpt-5.5"
    assert model_args["max_completion_tokens"] == 123
    assert model_args["use_responses_api"] is True
    assert model_args["output_version"] == "responses/v1"
    assert model_args["reasoning_effort"] == "low"
    assert model_args["verbosity"] == "low"


def test_deterministic_provider_discloses_simulated_capability() -> None:
    from my_agents.agents.capabilities import get_capability_for_route

    provider = DeterministicResponseProvider()
    route = RouteDecision(label="project_planner", explanation="planning request")

    reply = provider.compose_reply(
        messages=[HumanMessage(content="Plan the next milestone")],
        route=route,
        capability=get_capability_for_route("project_planner"),
        guidance="Break the work into one verifiable milestone.",
    )

    assert "Capability mode `simulation`" in reply
    assert "not a real-world integration" in reply


def test_openai_provider_includes_capability_metadata_in_prompt() -> None:
    from my_agents.agents.capabilities import get_capability_for_route

    settings = Settings(_env_file=None, OPENAI_API_KEY="test-key")
    chat_model = FakeChatModel()
    provider = OpenAIResponseProvider(settings=settings, chat_model=chat_model)

    provider.compose_reply(
        messages=[HumanMessage(content="Help me study LangGraph")],
        route=RouteDecision(label="learning_coach", explanation="study request"),
        capability=get_capability_for_route("learning_coach"),
        guidance="Define, build, and test one tiny example.",
    )

    final_prompt = str(chat_model.calls[0][-1].content)
    assert "Capability mode: simulation" in final_prompt
    assert "simulated_learning_coach" in final_prompt
    assert "Do not invent" in final_prompt


def test_openai_provider_includes_authorized_document_context_in_prompt() -> None:
    settings = Settings(_env_file=None, OPENAI_API_KEY="test-key")
    chat_model = FakeChatModel()
    provider = OpenAIResponseProvider(settings=settings, chat_model=chat_model)

    provider.compose_reply(
        messages=[HumanMessage(content="Tell me about me from my uploaded resume")],
        route=RouteDecision(label="general_assistant", explanation="general request"),
        guidance="Answer from available context.",
        retrieved_context=[
            {
                "title": "Resume 2026",
                "snippet": "Heecheon Park builds FastAPI LangGraph portfolio systems.",
                "source_page": 1,
                "source_filename": "resume.pdf",
            }
        ],
    )

    final_prompt = str(chat_model.calls[0][-1].content)
    assert "Authorized document context" in final_prompt
    assert "Resume 2026" in final_prompt
    assert "Heecheon Park builds FastAPI LangGraph portfolio systems" in final_prompt
    assert "instead of saying you cannot access uploaded documents" in final_prompt


@pytest.mark.parametrize(
    ("route", "message", "expected_tools"),
    [
        ("research_helper", "Find sources about LangGraph memory", [[{"type": "web_search"}]]),
        ("general_assistant", "What is the latest LangGraph release?", [[{"type": "web_search"}]]),
        ("general_assistant", "Help me organize my next task", []),
        ("project_planner", "Plan my next milestone", []),
    ],
)
def test_openai_provider_binds_web_search_by_route_and_latest_message_need(
    route: str,
    message: str,
    expected_tools: list[list[dict[str, str]]],
) -> None:
    settings = Settings(_env_file=None, OPENAI_API_KEY="test-key")
    chat_model = FakeChatModel()
    provider = OpenAIResponseProvider(settings=settings, chat_model=chat_model)

    reply = provider.compose_reply(
        messages=[HumanMessage(content=message)],
        route=RouteDecision(label=route, explanation="test route"),  # type: ignore[arg-type]
        guidance="Answer clearly.",
    )

    assert reply == "LangChain OpenAI drafted reply."
    assert chat_model.bound_tools == expected_tools
    assert len(chat_model.calls) == 1


def test_openai_response_extraction_uses_message_text_property_for_responses_api_blocks() -> None:
    response = AIMessage(
        content=[
            {"type": "web_search_call", "id": "ws_test", "status": "completed"},
            {"type": "text", "text": "Search-backed answer."},
        ]
    )

    from my_agents.agents.general_assistant.responders import _extract_message_content

    assert _extract_message_content(response) == "Search-backed answer."


def test_openai_response_extraction_returns_fallback_for_tool_call_without_text() -> None:
    response = AIMessage(
        content=[{"type": "web_search_call", "id": "ws_test", "status": "completed"}],
        response_metadata={"status": "completed"},
    )

    from my_agents.agents.general_assistant.responders import _extract_message_content

    reply = _extract_message_content(response)

    assert "could not extract a final text answer" in reply
    assert "status=completed" in reply
    assert "Please try again with a narrower question" in reply


def test_openai_response_extraction_explains_max_output_token_failure() -> None:
    response = AIMessage(
        content=[
            {"type": "reasoning", "summary": []},
            {"type": "web_search_call", "id": "ws_test", "status": "completed"},
        ],
        response_metadata={
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
        },
    )

    from my_agents.agents.general_assistant.responders import _extract_message_content

    reply = _extract_message_content(response)

    assert "status=incomplete" in reply
    assert "max_output_tokens" in reply
    assert "used the full output token budget" in reply
    assert "MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS" in reply


def test_openai_response_extraction_can_include_raw_response_for_cli_debugging() -> None:
    response = AIMessage(
        content=[{"type": "web_search_call", "id": "ws_test", "status": "completed"}],
        response_metadata={"status": "completed"},
    )

    from my_agents.agents.general_assistant.responders import _extract_message_content

    reply = _extract_message_content(response, debug_empty_response=True)

    assert "Raw response object:" in reply
    assert "web_search_call" in reply
    assert "ws_test" in reply
