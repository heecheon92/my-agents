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
    route = RouteDecision(label="general_assistant", explanation="planning request")

    reply = provider.compose_reply(
        messages=[HumanMessage(content="Plan the next milestone")],
        route=route,
        guidance="Break the work into one verifiable milestone.",
    )

    assert "general_assistant" in reply
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
    route = RouteDecision(label="general_assistant", explanation="study request")

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
    assert "Route label: general_assistant" in str(messages[-1].content)

    model_args = _build_chat_model_args(settings)
    assert model_args["model"] == "gpt-5.5"
    assert model_args["max_completion_tokens"] == 123
    assert model_args["use_responses_api"] is True
    assert model_args["output_version"] == "responses/v1"
    assert model_args["reasoning_effort"] == "low"
    assert model_args["verbosity"] == "low"


def test_deterministic_provider_discloses_capability_boundaries() -> None:
    from my_agents.agents.capabilities import get_capability_for_route

    provider = DeterministicResponseProvider()
    route = RouteDecision(label="general_assistant", explanation="planning request")

    reply = provider.compose_reply(
        messages=[HumanMessage(content="Plan the next milestone")],
        route=route,
        capability=get_capability_for_route("general_assistant"),
        guidance="Break the work into one verifiable milestone.",
    )

    assert "Capability `general_assistant_router`" in reply
    assert "OpenAI API call when OpenAI mode is enabled" in reply
    assert "simulation" not in reply.lower()


def test_openai_provider_includes_capability_metadata_in_prompt() -> None:
    from my_agents.agents.capabilities import get_capability_for_route

    settings = Settings(_env_file=None, OPENAI_API_KEY="test-key")
    chat_model = FakeChatModel()
    provider = OpenAIResponseProvider(settings=settings, chat_model=chat_model)

    provider.compose_reply(
        messages=[HumanMessage(content="Help me study LangGraph")],
        route=RouteDecision(label="general_assistant", explanation="study request"),
        capability=get_capability_for_route("general_assistant"),
        guidance="Define, build, and test one tiny example.",
    )

    final_prompt = str(chat_model.calls[0][-1].content)
    assert "Capability name: general_assistant_router" in final_prompt
    assert "OpenAI API call when OpenAI mode is enabled" in final_prompt
    assert "Capability mode" not in final_prompt
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
                "snippet": "Heecheon Park builds FastAPI LangGraph product systems.",
                "source_page": 1,
                "source_filename": "resume.pdf",
            }
        ],
    )

    final_prompt = str(chat_model.calls[0][-1].content)
    assert "Authorized document context" in final_prompt
    assert "Resume 2026" in final_prompt
    assert "Heecheon Park builds FastAPI LangGraph product systems" in final_prompt
    assert "instead of saying you cannot access uploaded documents" in final_prompt


@pytest.mark.parametrize(
    ("route", "message", "expected_tools"),
    [
        ("research_helper", "Find sources about LangGraph memory", [[{"type": "web_search"}]]),
        ("general_assistant", "What is the latest LangGraph release?", [[{"type": "web_search"}]]),
        ("general_assistant", "Help me organize my next task", []),
        ("general_assistant", "Plan my next milestone", []),
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


def test_source_context_bundle_selects_recent_product_db_messages_explicitly() -> None:
    from my_agents.agents.general_assistant.context import build_source_context_bundle

    messages: list[BaseMessage] = [
        HumanMessage(content="old user 1"),
        AIMessage(content="old assistant 1"),
        HumanMessage(content="old user 2"),
        AIMessage(content="old assistant 2"),
        HumanMessage(content="recent user 3"),
        AIMessage(content="recent assistant 3"),
        HumanMessage(content="latest user 4"),
    ]

    bundle = build_source_context_bundle(messages=messages, recent_message_limit=4)

    assert [message.content for message in bundle.recent_conversation] == [
        "old assistant 2",
        "recent user 3",
        "recent assistant 3",
        "latest user 4",
    ]
    assert [message.content for message in bundle.prior_provider_messages] == [
        "old assistant 2",
        "recent user 3",
        "recent assistant 3",
    ]
    assert bundle.latest_user_message == "latest user 4"
    assert bundle.recent_message_limit == 4


def test_openai_provider_uses_explicit_source_context_bundle_policy() -> None:
    settings = Settings(_env_file=None, OPENAI_API_KEY="test-key")
    chat_model = FakeChatModel()
    provider = OpenAIResponseProvider(settings=settings, chat_model=chat_model)
    messages: list[BaseMessage] = [
        HumanMessage(content="old user 1"),
        AIMessage(content="old assistant 1"),
        HumanMessage(content="old user 2"),
        AIMessage(content="old assistant 2"),
        HumanMessage(content="recent user 3"),
        AIMessage(content="recent assistant 3"),
        HumanMessage(content="latest user 4"),
    ]

    provider.compose_reply(
        messages=messages,
        route=RouteDecision(label="general_assistant", explanation="general request"),
        guidance="Answer from explicit source context.",
        retrieved_context=[
            {
                "title": "Project notes",
                "snippet": "Memory architecture keeps Product DB authoritative.",
                "source_filename": "notes.md",
            }
        ],
    )

    provider_messages = chat_model.calls[0]
    provider_contents = [str(message.content) for message in provider_messages]
    final_prompt = provider_contents[-1]

    assert "old user 1" not in provider_contents
    assert provider_contents[1:6] == [
        "old assistant 1",
        "old user 2",
        "old assistant 2",
        "recent user 3",
        "recent assistant 3",
    ]
    assert (
        "Conversation context policy: using the latest 6 persisted Product DB message(s)"
        in final_prompt
    )
    assert "Stored memory context: none" in final_prompt
    assert "Authorized document context:" in final_prompt
    assert "Project notes" in final_prompt
    assert "Material source conflicts: none" in final_prompt
    assert "User message: latest user 4" in final_prompt


def test_source_context_bundle_formats_memory_and_conflicts() -> None:
    from my_agents.agents.general_assistant.context import (
        build_source_context_bundle,
        format_conflict_context,
        format_memory_context,
    )

    bundle = build_source_context_bundle(
        messages=[HumanMessage(content="Actually I no longer prefer concise answers")],
        memory_context=[
            {
                "id": "memory-1",
                "category": "stable_preference",
                "content": "User prefers concise answers",
                "provenance_type": "explicit_user",
            }
        ],
        source_conflicts=[
            {
                "primary": "conversation",
                "secondary": "memory",
                "description": "Latest message revises memory-1",
                "material": True,
            }
        ],
    )

    assert '"category":"stable_preference"' in format_memory_context(bundle)
    assert "untrusted_memory_json=" in format_memory_context(bundle)
    assert "User prefers concise answers" in format_memory_context(bundle)
    assert '"primary":"conversation"' in format_conflict_context(bundle)
    assert '"secondary":"memory"' in format_conflict_context(bundle)
    assert "Latest message revises memory-1" in format_conflict_context(bundle)


def test_openai_provider_includes_memory_and_conflict_context_in_prompt() -> None:
    settings = Settings(_env_file=None, OPENAI_API_KEY="test-key")
    chat_model = FakeChatModel()
    provider = OpenAIResponseProvider(settings=settings, chat_model=chat_model)

    provider.compose_reply(
        messages=[HumanMessage(content="Actually I no longer prefer concise answers")],
        route=RouteDecision(label="general_assistant", explanation="general request"),
        guidance="Answer from available context.",
        memory_context=[
            {
                "id": "memory-1",
                "category": "stable_preference",
                "content": "User prefers concise answers",
                "provenance_type": "explicit_user",
            }
        ],
        source_conflicts=[
            {
                "primary": "conversation",
                "secondary": "memory",
                "description": "The latest message revises memory-1",
                "material": True,
            }
        ],
    )

    final_prompt = str(chat_model.calls[0][-1].content)
    assert "Stored memory context:" in final_prompt
    assert "User prefers concise answers" in final_prompt
    assert "Material source conflicts:" in final_prompt
    assert '"primary":"conversation"' in final_prompt
    assert '"secondary":"memory"' in final_prompt
    assert "prefer the latest conversation" in final_prompt
