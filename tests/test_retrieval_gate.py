"""Source-selection gate tests for graph-owned RAG bypass."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from my_agents.agents.general_assistant.retrieval_gate import (
    DeterministicRetrievalSourceDecider,
    OpenAIRetrievalSourceDecider,
)
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.settings import Settings


class FakeGateChatModel:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[list[BaseMessage]] = []

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:  # noqa: ANN003
        self.calls.append(messages)
        assert kwargs["reasoning"]["mode"] == "standard"
        return AIMessage(content=self._response)


def test_deterministic_gate_honors_explicit_saved_doc_bypass() -> None:
    decision = DeterministicRetrievalSourceDecider().decide(
        messages=[HumanMessage(content="Don't use saved docs. 웹에서 찾아줘.")],
        selection_context=_selection_context(),
    )

    assert decision.source == "bypass"
    assert "explicitly asks not to use saved documents" in decision.reason


def test_deterministic_gate_selects_knowledge_base_for_uploaded_document_request() -> None:
    decision = DeterministicRetrievalSourceDecider().decide(
        messages=[HumanMessage(content="Please summarize my uploaded document")],
        selection_context=_selection_context(),
    )

    assert decision.source == "knowledge_base"
    assert "document-backed" in decision.reason


def test_deterministic_gate_selects_knowledge_base_for_private_identifier_lookup() -> None:
    decision = DeterministicRetrievalSourceDecider().decide(
        messages=[HumanMessage(content="What is StageOnlyAlpha?")],
        selection_context=_selection_context(),
    )

    assert decision.source == "knowledge_base"
    assert "distinctive term" in decision.reason


def test_deterministic_gate_bypasses_current_web_requests_before_identifier_lookup() -> None:
    decision = DeterministicRetrievalSourceDecider().decide(
        messages=[HumanMessage(content="What is the latest LangGraph release?")],
        selection_context=_selection_context(),
    )

    assert decision.source == "bypass"
    assert "current or web-backed" in decision.reason


def test_deterministic_gate_inherits_recent_web_context_for_follow_up() -> None:
    decision = DeterministicRetrievalSourceDecider().decide(
        messages=[
            HumanMessage(content="Search the web for the latest LangGraph release."),
            AIMessage(content="The latest release details are..."),
            HumanMessage(content="What about breaking changes?"),
        ],
        selection_context=_selection_context(),
    )

    assert decision.source == "bypass"
    assert "continue a recent current/web-backed conversation" in decision.reason


def test_deterministic_gate_lets_latest_document_request_override_recent_web_context() -> None:
    decision = DeterministicRetrievalSourceDecider().decide(
        messages=[
            HumanMessage(content="Search the web for the latest LangGraph release."),
            AIMessage(content="The latest release details are..."),
            HumanMessage(content="Now summarize my uploaded document."),
        ],
        selection_context=_selection_context(),
    )

    assert decision.source == "knowledge_base"
    assert "document-backed" in decision.reason


def test_deterministic_gate_selects_knowledge_base_for_source_lookup_verbs() -> None:
    decision = DeterministicRetrievalSourceDecider().decide(
        messages=[HumanMessage(content="What does Delete Cleanup PDF mention?")],
        selection_context=_selection_context(),
    )

    assert decision.source == "knowledge_base"
    assert "source lookup" in decision.reason


def test_openai_gate_parses_json_source_decision() -> None:
    chat_model = FakeGateChatModel(
        '{"source":"knowledge_base","reason":"The user asks about saved project docs."}'
    )
    decider = OpenAIRetrievalSourceDecider(
        settings=Settings(_env_file=None, OPENAI_API_KEY="test-key"),
        chat_model=chat_model,
    )

    decision = decider.decide(
        messages=[HumanMessage(content="내 프로젝트 문서에서 관련 내용을 찾아줘")],
        selection_context=_selection_context(),
    )

    assert decision.source == "knowledge_base"
    assert decision.reason == "The user asks about saved project docs."
    assert len(chat_model.calls) == 1
    assert "Latest user message: 내 프로젝트 문서에서 관련 내용을 찾아줘" in str(
        chat_model.calls[0][-1].content
    )
    assert "Recent conversation:" in str(chat_model.calls[0][-1].content)
    assert "follow-up to a recent web/current/external request" in str(
        chat_model.calls[0][0].content
    )


def test_openai_gate_falls_back_to_deterministic_decision_for_invalid_json() -> None:
    chat_model = FakeGateChatModel("not json")
    decider = OpenAIRetrievalSourceDecider(
        settings=Settings(_env_file=None, OPENAI_API_KEY="test-key"),
        chat_model=chat_model,
    )

    decision = decider.decide(
        messages=[HumanMessage(content="What is RAG?")],
        selection_context=_selection_context(),
    )

    assert decision.source == "bypass"


def _selection_context() -> KnowledgeBaseSelectionContext:
    return KnowledgeBaseSelectionContext(
        mode="all",
        knowledge_base_ids=(),
        resolved_count=1,
        ambient_system_knowledge_base_count=1,
    )
