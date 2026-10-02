"""Jev reranking contracts, budgets and failure paths stay credential-free."""

import json
from contextvars import copy_context

import httpx
import pytest

from my_agents.agents.context_forge import ContextForgeService
from my_agents.agents.context_forge.contracts import ContextForgeRequest
from my_agents.agents.context_forge.reranking import (
    CrossEncoderReranker,
    DeterministicReranker,
    JevReranker,
    build_reranker,
)
from my_agents.decisions import JEV_ENDPOINT, JevScoreClient
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.settings import Settings
from tests.test_context_forge_reranking import FakeAuthorizedRetrievalService, _candidate, _plan


def _settings(**overrides):
    return Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="openai",
        OPENAI_API_KEY="test-only-openai",
        OPENROUTER_API_KEY="test-only",
        **overrides,
    )


class CapturingScores:
    def __init__(self, scores=None):
        self.calls = []
        self.scores = scores

    def score(self, **kwargs):
        self.calls.append(kwargs)
        return {
            key: self.scores.get(key, 1.0) if self.scores is not None else 1.0
            for key in kwargs["questions"]
        }


def _candidates(count=3, content="evidence"):
    return [
        _candidate(f"chunk-{i}", ordinal=count - i, content=content, score=1 / (i + 1))
        for i in range(count)
    ]


def test_default_is_jev_but_global_deterministic_remains_offline():
    settings = _settings(MY_AGENTS_DECISION_PROVIDER="deterministic")
    assert settings.reranker_mode == "jev"
    assert isinstance(build_reranker(settings), JevReranker)
    assert isinstance(
        build_reranker(settings.model_copy(update={"response_mode": "deterministic"})),
        DeterministicReranker,
    )
    assert isinstance(
        build_reranker(settings.model_copy(update={"reranker_mode": "deterministic"})),
        DeterministicReranker,
    )
    assert isinstance(
        build_reranker(settings.model_copy(update={"reranker_mode": "cross_encoder"})),
        CrossEncoderReranker,
    )


def test_score_wire_contract_and_complete_mapping():
    def respond(request):
        assert str(request.url) == JEV_ENDPOINT
        assert request.headers["authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        assert body["model"] == "typesafe/jev-1.13"
        assert body["questions"]["candidate_0"]["type"] == "score"
        assert isinstance(body["questions"]["candidate_0"]["criteria"], list)
        assert body["state"]["candidates"][0]["id"] == "candidate_0"
        return httpx.Response(
            200,
            json={"answers": {key: {"type": "score", "score": 2.5} for key in body["questions"]}},
        )

    client = JevScoreClient(_settings(), transport=httpx.MockTransport(respond))
    result = JevReranker(_settings(), client=client).rerank(
        plan=_plan("질문"), candidates=_candidates(2)
    )
    assert [item.rerank_score for item in result] == [2.5, 2.5]


def test_reordering_preserves_identity_original_scores_and_equal_score_order():
    candidates = _candidates()
    client = CapturingScores({"candidate_0": 1.0, "candidate_1": 4.0, "candidate_2": 4.0})
    reranked = JevReranker(_settings(), client=client).rerank(
        plan=_plan("query"), candidates=candidates
    )
    assert [item.chunk.chunk.id for item in reranked] == ["chunk-1", "chunk-2", "chunk-0"]
    assert reranked[0].chunk is candidates[1].chunk
    assert reranked[0].score == candidates[1].score
    assert candidates[1].rerank_score is None
    assert reranked[0].reasons[-1] == "jev:typesafe/jev-1.13"


def test_unicode_excerpts_and_payloads_are_bounded_without_altering_chunks():
    captured = []

    def respond(request):
        assert len(request.content) <= 6000
        body = json.loads(request.content)
        captured.append(body)
        for record in body["state"]["candidates"]:
            assert len(record["excerpt"].encode()) <= 1000
            assert record["excerpt_truncated"]
        return httpx.Response(
            200,
            json={"answers": {key: {"type": "score", "score": 1.0} for key in body["questions"]}},
        )

    settings = _settings(
        MY_AGENTS_JEV_RERANKER_BATCH_SIZE=8,
        MY_AGENTS_JEV_RERANKER_MAX_INPUT_BYTES=6000,
        MY_AGENTS_JEV_RERANKER_MAX_EXCERPT_BYTES=1000,
    )
    candidates = _candidates(9, "한글🙂" * 1000)
    client = JevScoreClient(settings, transport=httpx.MockTransport(respond))
    output = JevReranker(settings, client=client).rerank(
        plan=_plan("한글 질문"), candidates=candidates
    )
    assert len(captured) > 1
    assert len(output) == len(candidates)
    assert output[0].chunk.chunk.content == candidates[0].chunk.chunk.content
    assert "jev:excerpt_truncated" in output[0].reasons


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"type": "choice", "score": 2.0},
        {"type": "score", "score": "2"},
        {"type": "score", "score": -1},
        {"type": "score", "score": 5},
        {"type": "score", "score": float("nan")},
        {"type": "score", "score": True},
        {"type": "score", "score": 2, "confidence": 2},
    ],
)
def test_invalid_scores_fall_back_to_exact_input_order(answer):
    client = JevScoreClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"answers": {"candidate_0": answer}})
        ),
    )
    candidates = _candidates(1)
    reranker = JevReranker(_settings(), client=client)
    assert reranker.rerank(plan=_plan("query"), candidates=candidates) == candidates
    assert reranker.effective_name == "jev_fallback_deterministic"


@pytest.mark.parametrize("answers", [{}, {"wrong": {"type": "score", "score": 2}}])
def test_unknown_or_missing_answer_ids_fall_back(answers):
    client = JevScoreClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"answers": answers})
        ),
    )
    candidates = _candidates(1)
    assert (
        JevReranker(_settings(), client=client).rerank(plan=_plan("query"), candidates=candidates)
        == candidates
    )


def test_partial_batch_failure_never_mixes_scored_and_unscored_candidates():
    class PartialScores(CapturingScores):
        def score(self, **kwargs):
            return None if self.calls else super().score(**kwargs)

    candidates = _candidates(3)
    client = PartialScores({"candidate_0": 0.0, "candidate_1": 4.0})
    settings = _settings(MY_AGENTS_JEV_RERANKER_BATCH_SIZE=2)
    output = JevReranker(settings, client=client).rerank(plan=_plan("query"), candidates=candidates)
    assert output == candidates
    assert all(item.rerank_score is None for item in output)


def test_fallback_evidence_does_not_leak_into_another_request_context():
    reranker = JevReranker(_settings(), client=CapturingScores())
    other_request = copy_context()
    other_request.run(reranker.rerank, plan=_plan("q" * 5000), candidates=_candidates())
    assert other_request.run(lambda: reranker.effective_name) == "jev_fallback_deterministic"
    assert reranker.effective_name == "jev"


@pytest.mark.parametrize("failure", ["http", "timeout", "invalid_json"])
def test_provider_failures_have_no_retry_and_preserve_input(failure):
    requests = []

    def respond(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private provider detail")
        if failure == "invalid_json":
            return httpx.Response(200, text="not-json")
        return httpx.Response(503, text="private provider detail")

    settings = _settings()
    client = JevScoreClient(settings, transport=httpx.MockTransport(respond))
    candidates = _candidates()
    assert (
        JevReranker(settings, client=client).rerank(plan=_plan("query"), candidates=candidates)
        == candidates
    )
    assert len(requests) == 1


@pytest.mark.parametrize("reason", ["empty", "offline", "missing_key", "query_budget"])
def test_no_network_or_model_loading_on_short_circuit(reason):
    candidates = _candidates() if reason != "empty" else []
    settings = _settings()
    query = "q"
    if reason == "offline":
        settings = settings.model_copy(update={"response_mode": "deterministic"})
    if reason == "missing_key":
        settings = settings.model_copy(update={"openrouter_api_key": None})
    if reason == "query_budget":
        query = "q" * 5000
    client = CapturingScores()
    assert (
        JevReranker(settings, client=client).rerank(plan=_plan(query), candidates=candidates)
        == candidates
    )
    assert client.calls == []


def test_fallback_diagnostics_are_recorded_and_private_text_is_never_logged(caplog):
    def fail(request):
        raise httpx.ReadTimeout("PRIVATE-DOCUMENT SECRET-KEY")

    client = JevScoreClient(_settings(), transport=httpx.MockTransport(fail))
    reranker = JevReranker(_settings(), client=client)
    service = ContextForgeService(
        None, retrieval_service=FakeAuthorizedRetrievalService(), reranker=reranker
    )
    result = service.retrieve(
        ContextForgeRequest(
            user_id="user-1",
            conversation_id="conversation-1",
            query="Based on my document, answer the memory question",
            messages=[],
            selection_context=KnowledgeBaseSelectionContext(
                mode="all", knowledge_base_ids=(), resolved_count=0
            ),
        )
    )
    assert result.evidence.reranker == "jev_fallback_deterministic"
    assert "PRIVATE-DOCUMENT" not in caplog.text
    assert "SECRET-KEY" not in caplog.text


def test_deadline_is_shared_across_batches(monkeypatch):
    import my_agents.agents.context_forge.reranking as module

    clock = [0.0]

    class SlowScores(CapturingScores):
        def score(self, **kwargs):
            result = super().score(**kwargs)
            clock[0] += 6.0
            return result

    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    client = SlowScores()
    settings = _settings(MY_AGENTS_JEV_RERANKER_BATCH_SIZE=1, MY_AGENTS_JEV_TIMEOUT_SECONDS=10)
    candidates = _candidates(3)
    assert (
        JevReranker(settings, client=client).rerank(plan=_plan("query"), candidates=candidates)
        == candidates
    )
    assert [call["timeout_seconds"] for call in client.calls] == [10.0, 4.0]


def test_permission_filtering_and_candidate_limit_still_precede_jev():
    client = CapturingScores()
    service = ContextForgeService(
        None,
        retrieval_service=FakeAuthorizedRetrievalService(),
        reranker=JevReranker(_settings(), client=client),
    )
    result = service.retrieve(
        ContextForgeRequest(
            user_id="user-1",
            conversation_id="conversation-1",
            query="Based on my document, answer the memory question",
            messages=[],
            selection_context=KnowledgeBaseSelectionContext(
                mode="all", knowledge_base_ids=(), resolved_count=0
            ),
        )
    )
    records = [record for call in client.calls for record in call["state"]["candidates"]]
    assert len(records) == 40
    assert "unauthorized-chunk" not in json.dumps(records)
    assert all("Authorized context" in record["excerpt"] for record in records)
    assert result.evidence.reranker == "jev"
