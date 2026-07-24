"""ContextForge reranking tests."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import pytest

from my_agents.agents.context_forge import ContextForgeService
from my_agents.agents.context_forge import debug as context_forge_debug
from my_agents.agents.context_forge.candidates import CandidateScouts
from my_agents.agents.context_forge.contracts import (
    ContextForgeRequest,
    RetrievalCandidate,
    RetrievalPlan,
)
from my_agents.agents.context_forge.debug import debug_agent_turn
from my_agents.agents.context_forge.fusion import fuse_candidates
from my_agents.agents.context_forge.reranking import (
    CrossEncoderReranker,
    DeterministicReranker,
    build_reranker,
)
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import DocumentChunkModel, DocumentModel
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import RetrievalRoutingDecision
from my_agents.observability.metrics import track_embedding_call, track_retrieval_phase
from my_agents.settings import Settings, get_settings


class FakeCrossEncoder:
    """Tiny deterministic stand-in for sentence-transformers CrossEncoder."""

    def predict(self, pairs: Sequence[tuple[str, str]], *, batch_size: int) -> list[float]:
        assert batch_size == 8
        return [10.0 if "categorical downcasting" in document else 1.0 for _, document in pairs]


class CapturingReranker:
    def __init__(self) -> None:
        self.seen_chunk_ids: list[str] = []

    @property
    def name(self) -> str:
        return "capturing"

    def rerank(
        self,
        *,
        plan: RetrievalPlan,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        _ = plan
        self.seen_chunk_ids = [candidate.chunk.chunk.id for candidate in candidates]
        return list(candidates)


class FakeAuthorizedRetrievalService:
    unauthorized_chunk_ids = ("unauthorized-chunk",)

    def authorized_document_count(self, **_: Any) -> int:
        return 45

    def retrieve_scoped(self, **_: Any) -> list[RetrievedChunk]:
        with track_retrieval_phase("document_metadata_match"):
            metadata_matches: list[RetrievedChunk] = []
        with track_embedding_call(
            provider="deterministic",
            model="lexical-hash-v1",
            operation="query",
        ):
            pass
        with track_retrieval_phase("direct_authorized_match"):
            direct_matches = [
                _retrieved_chunk(
                    f"authorized-chunk-{index:02d}",
                    ordinal=index,
                    content=f"Authorized context {index}",
                    score=1.0 - (index * 0.01),
                )
                for index in range(45)
            ]
        return [*metadata_matches, *direct_matches]

    def retrieve_lexical_scoped(self, **_: Any) -> list[RetrievedChunk]:
        return []

    def retrieve_structured_entities(self, **_: Any) -> list[object]:
        return []


def test_context_forge_sends_only_authorized_bounded_candidates_to_reranker() -> None:
    reranker = CapturingReranker()
    service = ContextForgeService(
        None,  # type: ignore[arg-type]
        retrieval_service=FakeAuthorizedRetrievalService(),  # type: ignore[arg-type]
        reranker=reranker,
    )

    result = service.retrieve(
        ContextForgeRequest(
            user_id="user-1",
            conversation_id="conversation-1",
            query="Based on my document, answer the memory question",
            messages=[],
            selection_context=KnowledgeBaseSelectionContext(
                mode="all",
                knowledge_base_ids=(),
                resolved_count=0,
            ),
        )
    )

    assert len(reranker.seen_chunk_ids) == result.plan.limits.rerank_limit
    assert set(reranker.seen_chunk_ids).isdisjoint(
        FakeAuthorizedRetrievalService.unauthorized_chunk_ids
    )
    assert all(chunk_id.startswith("authorized-chunk-") for chunk_id in reranker.seen_chunk_ids)
    assert result.evidence.reranker == "capturing"


def test_context_forge_uses_reranker_top_k_setting(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RERANKER_TOP_K", "7")

    reranker = CapturingReranker()
    service = ContextForgeService(
        None,  # type: ignore[arg-type]
        retrieval_service=FakeAuthorizedRetrievalService(),  # type: ignore[arg-type]
        reranker=reranker,
    )

    result = service.retrieve(
        ContextForgeRequest(
            user_id="user-1",
            conversation_id="conversation-1",
            query="Based on my document, answer the memory question",
            messages=[],
            selection_context=KnowledgeBaseSelectionContext(
                mode="all",
                knowledge_base_ids=(),
                resolved_count=0,
            ),
        )
    )

    assert result.plan.limits.rerank_limit == 7
    assert len(reranker.seen_chunk_ids) == 7


def test_candidate_scouts_gather_vector_and_lexical_rankings_independently() -> None:
    class CapturingRetrievalService:
        def __init__(self) -> None:
            self.vector_limit: int | None = None
            self.lexical_limit: int | None = None
            self.hybrid_search = False

        def retrieve_scoped(self, **kwargs: Any) -> list[RetrievedChunk]:
            self.vector_limit = kwargs["limit"]
            self.hybrid_search = kwargs["hybrid_search"]
            return [
                _retrieved_chunk(
                    "vector-chunk",
                    ordinal=0,
                    content="Semantic result",
                    score=0.9,
                )
            ]

        def retrieve_lexical_scoped(self, **kwargs: Any) -> list[RetrievedChunk]:
            self.lexical_limit = kwargs["limit"]
            return [
                _retrieved_chunk(
                    "lexical-chunk",
                    ordinal=1,
                    content="Exact-term result",
                    score=1.0,
                    source="keyword_match",
                )
            ]

        def retrieve_structured_entities(self, **_: Any) -> list[object]:
            return []

    retrieval_service = CapturingRetrievalService()
    plan = _plan("exact term")

    chunks = CandidateScouts(retrieval_service).gather(  # type: ignore[arg-type]
        user_id="user-1",
        plan=plan,
        knowledge_base_ids=("kb-1",),
    )

    assert retrieval_service.vector_limit == plan.limits.vector_limit
    assert retrieval_service.lexical_limit == plan.limits.lexical_limit
    assert retrieval_service.hybrid_search is True
    assert [chunk.source for chunk in chunks] == ["semantic_vector", "keyword_match"]


def test_context_forge_prints_human_readable_timing_trace_when_enabled(
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_DEBUG_RETRIEVAL_TIMING_LOGGING", "true")
    get_settings.cache_clear()
    reranker = CapturingReranker()
    service = ContextForgeService(
        None,  # type: ignore[arg-type]
        retrieval_service=FakeAuthorizedRetrievalService(),  # type: ignore[arg-type]
        reranker=reranker,
    )

    service.retrieve(
        ContextForgeRequest(
            user_id="user-1",
            conversation_id="conversation-1",
            query="Based on my document, answer the memory question",
            messages=[],
            selection_context=KnowledgeBaseSelectionContext(
                mode="all",
                knowledge_base_ids=(),
                resolved_count=0,
            ),
        )
    )

    output = capsys.readouterr().out
    assert "ContextForge timing" in output
    assert "candidate_gather" in output
    assert "candidate_gather.document_metadata_match" in output
    assert "candidate_gather.direct_authorized_match" in output
    assert "candidate_gather.embedding.query.deterministic" in output
    assert "candidate_fusion" in output
    assert "reranking" in output
    assert "context_pack" in output
    assert "raw_candidate_count" in output
    assert "retrieval_latency_ms" in output
    assert "Based on my document" not in output


def test_debug_agent_turn_rich_prints_handoff_when_enabled(capsys) -> None:  # noqa: ANN001
    original_level = context_forge_debug.logger.level
    context_forge_debug.logger.setLevel(logging.DEBUG)
    try:
        debug_agent_turn(
            sender="CandidateFusion",
            receiver="EvidenceJudge",
            message="Send bounded fused candidates for reranking.",
            payload={"sent_candidate_ids": ["chunk-1"]},
        )
    finally:
        context_forge_debug.logger.setLevel(original_level)

    output = capsys.readouterr().out
    assert "CandidateFusion" in output
    assert "EvidenceJudge" in output
    assert "chunk-1" in output


def test_rrf_promotes_chunk_found_by_vector_and_lexical_search() -> None:
    vector_only = _retrieved_chunk(
        "chunk-vector-only",
        ordinal=0,
        content="Strong semantic match.",
        score=0.99,
        source="semantic_vector",
    )
    shared_vector = _retrieved_chunk(
        "chunk-shared",
        ordinal=1,
        content="Shared semantic and lexical match.",
        score=0.80,
        source="semantic_vector",
    )
    lexical_only = _retrieved_chunk(
        "chunk-lexical-only",
        ordinal=2,
        content="Strong exact-term match.",
        score=1.0,
        source="keyword_match",
    )
    shared_lexical = _retrieved_chunk(
        "chunk-shared",
        ordinal=1,
        content="Shared semantic and lexical match.",
        score=0.75,
        source="keyword_match",
    )

    fused = fuse_candidates([vector_only, shared_vector, lexical_only, shared_lexical])

    assert [candidate.chunk.chunk.id for candidate in fused] == [
        "chunk-shared",
        "chunk-vector-only",
        "chunk-lexical-only",
    ]
    assert fused[0].sources == ("semantic_vector", "keyword_match")
    assert fused[0].score == pytest.approx(2 / 62)
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[2].score == pytest.approx(1 / 61)


def test_rrf_merges_by_chunk_id_instead_of_rank_position() -> None:
    vector_first = _retrieved_chunk(
        "chunk-vector",
        ordinal=0,
        content="Vector first.",
        score=0.9,
        source="semantic_vector",
    )
    lexical_first = _retrieved_chunk(
        "chunk-lexical",
        ordinal=1,
        content="Lexical first.",
        score=0.9,
        source="keyword_match",
    )

    fused = fuse_candidates([vector_first, lexical_first])

    assert {candidate.chunk.chunk.id for candidate in fused} == {
        "chunk-vector",
        "chunk-lexical",
    }
    assert all(len(candidate.sources) == 1 for candidate in fused)


def test_deterministic_reranker_preserves_fused_score_order() -> None:
    plan = _plan("How do I optimize pandas memory?")
    low = _candidate("chunk-low", ordinal=0, content="Python is general purpose.", score=0.2)
    high = _candidate("chunk-high", ordinal=1, content="Pandas memory tips.", score=0.8)

    reranked = DeterministicReranker().rerank(plan=plan, candidates=[low, high])

    assert [item.chunk.chunk.id for item in reranked] == ["chunk-high", "chunk-low"]
    assert [item.rerank_score for item in reranked] == [None, None]


def test_cross_encoder_reranker_scores_query_document_pairs() -> None:
    plan = _plan("How do I optimize pandas memory?")
    vector_high = _candidate(
        "chunk-semantic",
        ordinal=0,
        content="Python is a versatile language for data science.",
        score=0.9,
    )
    cross_encoder_high = _candidate(
        "chunk-precise",
        ordinal=1,
        content="Pandas memory optimization uses categorical downcasting.",
        score=0.4,
    )

    reranked = CrossEncoderReranker(
        model_name="fake-cross-encoder",
        batch_size=8,
        model=FakeCrossEncoder(),
    ).rerank(plan=plan, candidates=[vector_high, cross_encoder_high])

    assert [item.chunk.chunk.id for item in reranked] == ["chunk-precise", "chunk-semantic"]
    assert [item.rerank_score for item in reranked] == [10.0, 1.0]
    assert reranked[0].reasons[-1] == "cross_encoder:fake-cross-encoder"


def test_cross_encoder_reranker_loads_model_only_when_candidates_are_scored(
    monkeypatch,
) -> None:  # noqa: ANN001
    load_calls: list[tuple[str, str | None]] = []

    def fake_load_cross_encoder(model_name: str, device: str | None) -> FakeCrossEncoder:
        load_calls.append((model_name, device))
        return FakeCrossEncoder()

    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_RERANKER_MODE", "cross_encoder")
    monkeypatch.setenv("MY_AGENTS_CROSS_ENCODER_MODEL", "fake-cross-encoder")
    monkeypatch.setenv("MY_AGENTS_CROSS_ENCODER_BATCH_SIZE", "8")
    monkeypatch.setenv("MY_AGENTS_CROSS_ENCODER_DEVICE", "mps")
    monkeypatch.setattr(
        "my_agents.agents.context_forge.reranking._load_cross_encoder",
        fake_load_cross_encoder,
    )

    settings = Settings(_env_file=None)
    reranker = build_reranker(settings)

    assert isinstance(reranker, CrossEncoderReranker)
    assert load_calls == []
    assert reranker.rerank(plan=_plan("test"), candidates=[]) == []
    assert load_calls == []

    reranked = reranker.rerank(
        plan=_plan("How do I optimize pandas memory?"),
        candidates=[
            _candidate(
                "chunk-precise",
                ordinal=0,
                content="Pandas memory optimization uses categorical downcasting.",
                score=0.4,
            )
        ],
    )

    assert [item.rerank_score for item in reranked] == [10.0]
    assert load_calls == [("fake-cross-encoder", "mps")]


def test_build_reranker_defaults_to_offline_deterministic(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.delenv("MY_AGENTS_RERANKER_MODE", raising=False)

    settings = Settings(_env_file=None)

    assert isinstance(build_reranker(settings), DeterministicReranker)
    assert settings.reranker_mode == "deterministic"
    assert settings.reranker_top_k == 40
    assert settings.cross_encoder_model == "BAAI/bge-reranker-v2-m3"
    assert settings.cross_encoder_batch_size == 16


def test_cross_encoder_settings_accept_overrides(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_RERANKER_MODE", "cross_encoder")
    monkeypatch.setenv("MY_AGENTS_RERANKER_TOP_K", "24")
    monkeypatch.setenv("MY_AGENTS_CROSS_ENCODER_MODEL", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setenv("MY_AGENTS_CROSS_ENCODER_BATCH_SIZE", "32")
    monkeypatch.setenv("MY_AGENTS_CROSS_ENCODER_DEVICE", "mps")

    settings = Settings(_env_file=None)

    assert settings.reranker_mode == "cross_encoder"
    assert settings.reranker_top_k == 24
    assert settings.cross_encoder_model == "BAAI/bge-reranker-v2-m3"
    assert settings.cross_encoder_batch_size == 32
    assert settings.cross_encoder_device == "mps"


def _plan(query: str) -> RetrievalPlan:
    return RetrievalPlan(
        intent="semantic_qa",
        original_query=query,
        rewritten_query=query,
        route_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="test",
            rewritten_query=query,
            document_scope="user_documents",
        ),
    )


def _candidate(
    chunk_id: str,
    *,
    ordinal: int,
    content: str,
    score: float,
) -> RetrievalCandidate:
    document = DocumentModel(
        id="doc-" + chunk_id,
        title="Test doc",
        content=content,
        owner_user_id="user-1",
        knowledge_base_id="kb-1",
    )
    chunk = DocumentChunkModel(
        id=chunk_id,
        document_id=document.id,
        extraction_run_id="run-1",
        ordinal=ordinal,
        content=content,
        start_offset=0,
        end_offset=len(content),
        source_page=None,
        embedding_json="[]",
    )
    return RetrievalCandidate(
        chunk=_retrieved_chunk_from_models(chunk=chunk, document=document, score=score),
        sources=("semantic_vector",),
        score=score,
    )


def _retrieved_chunk(
    chunk_id: str,
    *,
    ordinal: int,
    content: str,
    score: float,
    source: str = "semantic_vector",
) -> RetrievedChunk:
    document = DocumentModel(
        id="doc-" + chunk_id,
        title="Test doc",
        content=content,
        owner_user_id="user-1",
        knowledge_base_id="kb-1",
    )
    chunk = DocumentChunkModel(
        id=chunk_id,
        document_id=document.id,
        extraction_run_id="run-1",
        ordinal=ordinal,
        content=content,
        start_offset=0,
        end_offset=len(content),
        source_page=None,
        embedding_json="[]",
    )
    return _retrieved_chunk_from_models(
        chunk=chunk,
        document=document,
        score=score,
        source=source,
    )


def _retrieved_chunk_from_models(
    *,
    chunk: DocumentChunkModel,
    document: DocumentModel,
    score: float,
    source: str = "semantic_vector",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=chunk,
        document=document,
        score=score,
        source=source,
    )
