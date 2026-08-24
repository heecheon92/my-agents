"""RAG Agent retrieval-tool selection tests."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from my_agents.agents.rag_agent.tool_selection import (
    RAG_AGENT_PLANNER_MODEL,
    RAG_AGENT_PLANNER_REASONING_EFFORT,
    DeterministicRagRetrievalToolDecider,
    OpenAIRagRetrievalToolDecider,
    _build_luna_model_args,
)
from my_agents.settings import Settings


class FakeToolSelectingChatModel:
    def __init__(self, *, tool_name: str | None = None, error: Exception | None = None) -> None:
        self._tool_name = tool_name
        self._error = error
        self.bind_calls: list[dict[str, object]] = []
        self.calls: list[list[BaseMessage]] = []

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN201
        self.bind_calls.append({"tools": tools, **kwargs})
        return self

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:  # noqa: ANN003
        if self._error is not None:
            raise self._error
        self.calls.append(messages)
        assert kwargs["reasoning"] == {"mode": "standard", "effort": "low"}
        tool_calls = []
        if self._tool_name is not None:
            tool_calls.append(
                {
                    "name": self._tool_name,
                    "args": {},
                    "id": "call-rag-tool",
                    "type": "tool_call",
                }
            )
        return AIMessage(content="", tool_calls=tool_calls)


def test_luna_planner_uses_fixed_internal_model_and_low_effort() -> None:
    settings = Settings(_env_file=None, OPENAI_API_KEY="test-key")

    args = _build_luna_model_args(settings)

    assert RAG_AGENT_PLANNER_MODEL == "gpt-5.6-luna"
    assert RAG_AGENT_PLANNER_REASONING_EFFORT == "low"
    assert args["model"] == "gpt-5.6-luna"
    assert args["max_completion_tokens"] == 256
    assert args["use_responses_api"] is True


def test_luna_planner_selects_comprehensive_document_tool_for_natural_korean_request() -> None:
    chat_model = FakeToolSelectingChatModel(tool_name="read_authorized_document_comprehensively")
    decider = OpenAIRagRetrievalToolDecider(
        Settings(_env_file=None, OPENAI_API_KEY="test-key"),
        chat_model=chat_model,
    )

    decision = decider.decide(
        messages=[
            HumanMessage(
                content=(
                    "SUMMARY.ko.md에 AxSystem에 관한 내용이 있는데 해당 문서에서 "
                    "빠짐없이 검토해서 나한테 정리해줘"
                )
            )
        ]
    )

    assert decision.tool == "read_authorized_document_comprehensively"
    assert decision.comprehensive is True
    assert chat_model.bind_calls[0]["tool_choice"] == "required"
    assert chat_model.bind_calls[0]["strict"] is True
    assert chat_model.bind_calls[0]["parallel_tool_calls"] is False
    assert "Natural multilingual phrasing counts" in str(chat_model.calls[0][0].content)


def test_luna_planner_selects_focused_tool_for_targeted_document_question() -> None:
    chat_model = FakeToolSelectingChatModel(tool_name="search_authorized_chunks")
    decider = OpenAIRagRetrievalToolDecider(
        Settings(_env_file=None, OPENAI_API_KEY="test-key"),
        chat_model=chat_model,
    )

    decision = decider.decide(
        messages=[HumanMessage(content="SUMMARY.ko.md에서 AxSystem의 정의만 찾아줘")]
    )

    assert decision.tool == "search_authorized_chunks"
    assert decision.comprehensive is False


def test_luna_planner_falls_back_deterministically_for_invalid_tool() -> None:
    chat_model = FakeToolSelectingChatModel(tool_name="unknown_tool")
    decider = OpenAIRagRetrievalToolDecider(
        Settings(_env_file=None, OPENAI_API_KEY="test-key"),
        chat_model=chat_model,
    )

    decision = decider.decide(messages=[HumanMessage(content="SUMMARY.ko.md 문서 전체를 검토해줘")])

    assert decision.tool == "read_authorized_document_comprehensively"


def test_luna_planner_falls_back_deterministically_for_provider_failure() -> None:
    chat_model = FakeToolSelectingChatModel(error=TimeoutError("provider timeout"))
    decider = OpenAIRagRetrievalToolDecider(
        Settings(_env_file=None, OPENAI_API_KEY="test-key"),
        chat_model=chat_model,
    )

    decision = decider.decide(
        messages=[HumanMessage(content="SUMMARY.ko.md에서 관련 부분만 찾아줘")]
    )

    assert decision.tool == "search_authorized_chunks"


def test_deterministic_planner_composes_document_reference_exhaustiveness_and_task() -> None:
    decision = DeterministicRagRetrievalToolDecider().decide(
        messages=[
            HumanMessage(
                content=(
                    "SUMMARY.ko.md에 AxSystem에 관한 내용이 있는데 해당 문서에서 "
                    "빠짐없이 검토해서 나한테 정리해줘"
                )
            )
        ]
    )

    assert decision.tool == "read_authorized_document_comprehensively"


def test_deterministic_planner_does_not_treat_generic_exhaustiveness_as_document_read() -> None:
    decision = DeterministicRagRetrievalToolDecider().decide(
        messages=[HumanMessage(content="이 개념을 빠짐없이 설명해줘")]
    )

    assert decision.tool == "search_authorized_chunks"
