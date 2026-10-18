"""Synthetic evidence-coverage checks; provider scoring is mocked, not a quality claim."""

import json
from pathlib import Path

import pytest

from my_agents.agents.context_forge.reranking import JevReranker
from tests.test_context_forge_reranking import _candidate, _plan
from tests.test_jev_reranking import _settings

CASES = json.loads((Path(__file__).parent / "fixtures/retrieval_reliability.json").read_text())


class EvidenceScorer:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    def score(self, *, state, questions, timeout_seconds):
        self.calls.append(state)
        return {
            record["id"]: 4.0 if self.evidence in record["excerpt"] else 1.0
            for record in state["candidates"]
        }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_jev_budget_preserves_relevant_tail_evidence(case):
    content = case["padding"] * case["repeat"] + case["evidence"]
    scorer = EvidenceScorer(case["evidence"])
    candidates = [
        _candidate("decoy", ordinal=0, content=case["decoy"], score=1.0),
        _candidate("relevant", ordinal=1, content=content, score=0.5),
    ]
    ranked = JevReranker(_settings(), client=scorer).rerank(
        plan=_plan(case["query"]), candidates=candidates
    )
    assert ranked[0].chunk.chunk.id == "relevant"
    assert ranked[0].chunk.chunk.content == content
    for state in scorer.calls:
        assert set(state) == {"query", "candidates"}
        assert all(len(c["excerpt"].encode()) <= 3000 for c in state["candidates"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_cross_encoder_windows_preserve_tail_and_fit_matching_tokenizer(case):
    from my_agents.agents.context_forge.reranking import CrossEncoderReranker
    from tests.test_context_forge_reranking import CharacterTokenizer

    class Model:
        tokenizer = CharacterTokenizer()
        max_seq_length = 1024
        calls = []

        def predict(self, pairs, *, batch_size):
            self.calls.extend(pairs)
            assert all(len(self.tokenizer.encode(q, d)) <= self.max_seq_length for q, d in pairs)
            return [4.0 if case["evidence"] in d else 1.0 for q, d in pairs]

    content = case["padding"] * case["repeat"] + case["evidence"]
    candidates = [
        _candidate("decoy", ordinal=0, content=case["decoy"], score=1),
        _candidate("relevant", ordinal=1, content=content, score=0.5),
    ]
    model = Model()
    ranked = CrossEncoderReranker(model_name="fixture", batch_size=8, model=model).rerank(
        plan=_plan(case["query"]), candidates=candidates
    )
    assert ranked[0].chunk.chunk.id == "relevant"
    assert ranked[0].chunk.chunk.content == content
    assert len(model.calls) > 2
    assert any(reason.startswith("cross_encoder:windows:") for reason in ranked[0].reasons)


@pytest.mark.parametrize("query,content", [("long query " * 100, "evidence"), ("q", "x" * 1000)])
def test_cross_encoder_never_silently_truncates_unbudgetable_inputs(query, content):
    from my_agents.agents.context_forge.reranking import CrossEncoderReranker
    from tests.test_context_forge_reranking import CharacterTokenizer

    class Model:
        tokenizer = CharacterTokenizer()
        max_seq_length = 10

        def predict(self, *args, **kwargs):
            raise AssertionError("No provider call with an over-limit pair")

    candidates = [_candidate("first", ordinal=0, content=content, score=1)]
    reranker = CrossEncoderReranker(model_name="fixture", batch_size=8, model=Model())
    result = reranker.rerank(plan=_plan(query), candidates=candidates)
    assert result == candidates
    assert reranker.effective_name == "cross_encoder_fallback_deterministic"


def test_query_window_is_contiguous_bounded_and_does_not_invent_content():
    from my_agents.knowledge.evidence_windows import select_evidence_window

    text = "한글🙂 irrelevant\n" * 100 + "ORION_TIMEOUT = 47\n" + "다음 절" * 100
    window = select_evidence_window(text, "What is ORION_TIMEOUT?", max_bytes=256)
    assert "ORION_TIMEOUT = 47" in window.text
    assert window.text == text[window.start : window.end]
    assert len(window.text.encode()) <= 256
    assert window.truncated and window.start > 0
    prefix = select_evidence_window(text, "no matching term", max_bytes=256)
    assert prefix.start == 0


@pytest.mark.parametrize("scores", [None, ["bad-score"], [], [float("nan")]])
def test_invalid_cross_encoder_scores_preserve_the_entire_shortlist(scores):
    from my_agents.agents.context_forge.reranking import CrossEncoderReranker
    from tests.test_context_forge_reranking import CharacterTokenizer

    class Model:
        tokenizer = CharacterTokenizer()
        max_seq_length = 1000

        def predict(self, pairs, *, batch_size):
            return scores

    candidates = [_candidate("first", ordinal=0, content="safe evidence", score=1)]
    assert (
        CrossEncoderReranker(model_name="fixture", batch_size=8, model=Model()).rerank(
            plan=_plan("question"), candidates=candidates
        )
        == candidates
    )
