"""Conservative answer-to-source attribution tests."""

from my_agents.agent_runtime.citation_attribution import answer_supported_source_indices


def test_attribution_selects_only_sources_with_answer_support() -> None:
    indexes = answer_supported_source_indices(
        reply=(
            "The service uses permission-first retrieval before candidate reranking, "
            "which prevents unauthorized chunks from entering context."
        ),
        source_texts=[
            "Permission-first retrieval runs before candidate reranking and context packing.",
            "SMTP delivery uses a verified sender domain and retry policy.",
        ],
    )

    assert indexes == [0]


def test_attribution_prefers_no_citation_for_unmatched_paraphrase() -> None:
    indexes = answer_supported_source_indices(
        reply="The design limits access before relevance processing.",
        source_texts=[
            "Permission-first retrieval runs before candidate reranking and context packing."
        ],
    )

    assert indexes == []


def test_attribution_does_not_match_generic_document_language() -> None:
    indexes = answer_supported_source_indices(
        reply="This document provides a concise summary.",
        source_texts=["The document contains unrelated deployment procedures."],
    )

    assert indexes == []


def test_attribution_can_match_one_distinctive_long_identifier() -> None:
    indexes = answer_supported_source_indices(
        reply="The active identifier is NCT06159946_Prot_000.",
        source_texts=["Protocol identifier NCT06159946_Prot_000 controls the source lookup."],
    )

    assert indexes == [0]
