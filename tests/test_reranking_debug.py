"""Readable reranking comparisons remain bounded and local-development only."""

from dataclasses import replace

import pytest
from rich.console import Console

from my_agents.agents.context_forge import ContextForgeService, debug
from my_agents.agents.context_forge.contracts import ContextForgeRequest
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.settings import get_settings
from tests.test_context_forge_reranking import FakeAuthorizedRetrievalService, _candidate


def _candidates(count=2):
    candidates = []
    for index in range(count):
        candidate = _candidate(
            f"chunk-{index}", ordinal=index, content=f"Evidence {index}", score=1 / (index + 1)
        )
        candidate.chunk.document.title = f"Document_{index}"
        candidates.append(candidate)
    return candidates


def _render(monkeypatch, **kwargs):
    panels = []
    monkeypatch.setattr(debug, "rich_print", panels.append)
    debug.debug_reranking_comparison(**kwargs)
    console = Console(record=True, width=240)
    for panel in panels:
        console.print(panel)
    return console.export_text()


@pytest.mark.parametrize(
    ("environment", "enabled", "empty"),
    [
        ("production", True, False),
        ("preview", True, False),
        ("local", False, False),
        ("local", True, True),
    ],
)
def test_no_output_outside_opted_in_local_nonempty_runs(monkeypatch, environment, enabled, empty):
    candidates = [] if empty else _candidates()
    assert (
        _render(
            monkeypatch,
            environment=environment,
            enabled=enabled,
            query="private query",
            reranker="jev",
            before=candidates,
            after=candidates,
        )
        == ""
    )


def test_comparison_shows_promotions_and_both_score_types(monkeypatch):
    before = _candidates()
    before[1].chunk.chunk.content = "[red]literal excerpt[/red]"
    after = [replace(before[1], rerank_score=4), replace(before[0], rerank_score=1)]
    output = _render(
        monkeypatch,
        environment="local",
        enabled=True,
        query="question",
        reranker="jev",
        before=before,
        after=after,
    )
    assert "reranker=jev" in output
    assert "Before" in output and "After" in output
    assert output.index("Document_1") < output.index("Document_0")
    assert "0.500000" in output and "4.000" in output
    assert "[red]literal excerpt[/red]" in output
    assert before[1].rerank_score is None


def test_rows_excerpts_and_query_are_bounded_and_fallback_is_explicit(monkeypatch):
    before = _candidates(30)
    before[0].chunk.chunk.content = "x" * 500 + "EXCERPT_TAIL"
    output = _render(
        monkeypatch,
        environment="local",
        enabled=True,
        query="q" * 500 + "QUERY_TAIL",
        reranker="jev_fallback_deterministic",
        before=before,
        after=list(reversed(before)),
    )
    assert "jev_fallback_deterministic" in output
    assert "Document_29" in output and "Document_0" in output
    assert "Document_15" not in output
    assert "EXCERPT_TAIL" not in output and "QUERY_TAIL" not in output


def test_service_passes_authorized_shortlist_to_the_local_comparison(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("MY_AGENTS_DEBUG_KNOWLEDGE_CONTEXT_LOGGING", "true")
    get_settings.cache_clear()
    comparisons = []
    monkeypatch.setattr(
        "my_agents.agents.context_forge.service.debug_reranking_comparison",
        lambda **kwargs: comparisons.append(kwargs),
    )
    service = ContextForgeService(None, retrieval_service=FakeAuthorizedRetrievalService())
    service.retrieve(
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
    assert len(comparisons) == 1
    assert comparisons[0]["environment"] == "local"
    assert comparisons[0]["enabled"] is True
    assert comparisons[0]["reranker"] == "deterministic"
    for stage in ("before", "after"):
        assert len(comparisons[0][stage]) == 40
        assert all(item.chunk.chunk.id != "unauthorized-chunk" for item in comparisons[0][stage])
