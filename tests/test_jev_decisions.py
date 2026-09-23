"""Offline Decisions API contracts and the three production integrations."""

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from my_agents.agents.context_forge.planner import QueryCartographer
from my_agents.agents.general_assistant.retrieval_gate import (
    JevRetrievalSourceDecider,
    get_retrieval_source_decider,
)
from my_agents.agents.rag_agent.tool_selection import (
    JevRagRetrievalToolDecider,
    get_rag_retrieval_tool_decider,
)
from my_agents.decisions import JEV_ENDPOINT, JevDecisionClient, decision_state
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.settings import Settings


class FixedChoice:
    def __init__(self, choice):
        self.choice = choice
        self.calls = []

    def choose(self, **kwargs):
        self.calls.append(kwargs)
        return self.choice


def settings():
    return Settings(_env_file=None, OPENROUTER_API_KEY="test-only")


def test_wire_contract_and_allowed_choice():
    def respond(request):
        assert str(request.url) == JEV_ENDPOINT
        assert request.headers["authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        assert body["model"] == "typesafe/jev-1.13"
        assert body["questions"]["decision"]["type"] == "choice"
        return httpx.Response(
            200,
            json={
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": "yes",
                        "confidence": 0.9,
                    }
                }
            },
        )

    client = JevDecisionClient(settings(), transport=httpx.MockTransport(respond))
    assert client.choose(state={}, instructions="Choose", criteria={"yes": "Yes"}) == "yes"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answers": []},
        {"answers": {"decision": {"type": "choice", "choice": "bad"}}},
        {"answers": {"decision": {"type": "score", "choice": "yes"}}},
        {"answers": {"decision": {"type": "choice", "choice": "yes", "confidence": 2}}},
    ],
)
def test_invalid_provider_results_use_fallback(payload):
    client = JevDecisionClient(
        settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    assert client.choose(state={}, instructions="Choose", criteria={"yes": "Yes"}) is None


@pytest.mark.parametrize("failure", ["timeout", "http", "json"])
def test_provider_failures_do_not_expose_details(failure, caplog):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private prompt test-only")
        return httpx.Response(503 if failure == "http" else 200, text="private prompt test-only")

    client = JevDecisionClient(settings(), transport=httpx.MockTransport(respond))
    assert client.choose(state={}, instructions="Choose", criteria={"yes": "Yes"}) is None
    assert "test-only" not in caplog.text
    assert "private prompt" not in caplog.text


def test_missing_key_makes_no_request():
    client = JevDecisionClient(
        Settings(_env_file=None, OPENROUTER_API_KEY=""),
        transport=httpx.MockTransport(lambda request: pytest.fail("network")),
    )
    assert client.choose(state={}, instructions="Choose", criteria={"yes": "Yes"}) is None


def test_source_gate_and_explicit_override():
    client = FixedChoice("knowledge_base")
    gate = JevRetrievalSourceDecider(settings(), client)
    selection = KnowledgeBaseSelectionContext(mode="all", knowledge_base_ids=(), resolved_count=1)
    assert (
        gate.decide(
            messages=[HumanMessage(content="내 자료를 찾아줘")], selection_context=selection
        ).source
        == "knowledge_base"
    )
    assert (
        gate.decide(
            messages=[HumanMessage(content="Don't use saved docs")], selection_context=selection
        ).source
        == "bypass"
    )
    assert len(client.calls) == 1
    fallback = JevRetrievalSourceDecider(settings(), FixedChoice(None))
    assert (
        fallback.decide(
            messages=[HumanMessage(content="Hello")], selection_context=selection
        ).source
        == "bypass"
    )


@pytest.mark.parametrize(
    "choice", ["search_authorized_chunks", "read_authorized_document_comprehensively"]
)
def test_tool_choice_maps_to_existing_contract(choice):
    decider = JevRagRetrievalToolDecider(settings(), FixedChoice(choice))
    result = decider.decide(messages=[HumanMessage(content="문서를 검토해줘")])
    assert result.tool == choice
    assert result.approach_summary is None


def test_tool_failure_uses_local_comprehensive_detection():
    result = JevRagRetrievalToolDecider(settings(), FixedChoice(None)).decide(
        messages=[HumanMessage(content="SUMMARY.ko.md 문서 전체를 검토해줘")]
    )
    assert result.comprehensive


def test_cartographer_jev_changes_intent_without_changing_scope_or_limits():
    kwargs = dict(message="Explain these differences", history=[], authorized_document_count=2)
    baseline = QueryCartographer(decider=FixedChoice(None)).plan(**kwargs)
    result = QueryCartographer(decider=FixedChoice("comparison")).plan(**kwargs)
    assert result.intent == "comparison"
    assert result.route_decision == baseline.route_decision
    assert result.limits == baseline.limits
    assert result.structured_entity_types == baseline.structured_entity_types


def test_production_factories_select_jev_and_offline_mode_wins(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "openai")
    monkeypatch.setenv("MY_AGENTS_DECISION_PROVIDER", "jev")
    from my_agents.settings import get_settings

    get_settings.cache_clear()
    assert isinstance(get_retrieval_source_decider(), JevRetrievalSourceDecider)
    assert isinstance(get_rag_retrieval_tool_decider(), JevRagRetrievalToolDecider)
    assert isinstance(QueryCartographer()._decider, JevDecisionClient)
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    get_settings.cache_clear()
    get_retrieval_source_decider.cache_clear()
    get_rag_retrieval_tool_decider.cache_clear()
    assert not isinstance(get_retrieval_source_decider(), JevRetrievalSourceDecider)
    assert QueryCartographer()._decider is None


def test_decision_context_is_bounded_and_excludes_system():
    state = decision_state(
        [
            SystemMessage(content="private system prompt"),
            *[HumanMessage(content="x" * 5000) for _ in range(8)],
        ]
    )
    assert len(state["recent_conversation"]) == 4
    assert all(len(item["text"]) == 4000 for item in state["recent_conversation"])


def test_text_blocks_are_preserved_without_images():
    state = decision_state(
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": "문서 전체를 읽어줘"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/private"}},
                ]
            )
        ]
    )
    assert state == {"recent_conversation": [{"role": "human", "text": "문서 전체를 읽어줘"}]}
