"""Reasoning-summary safety and ordering tests."""

from my_agents.reasoning_summaries import (
    bounded_reasoning_summary,
    summaries_from_graph_state,
)


def test_reasoning_summary_is_bounded_and_redacts_common_credentials() -> None:
    text = bounded_reasoning_summary(
        "I used sk-examplecredential123456 and then " + ("compared evidence " * 40)
    )

    assert text is not None
    assert len(text) == 500
    assert "sk-examplecredential123456" not in text
    assert "[redacted]" in text


def test_graph_summaries_are_ordered_and_nullable() -> None:
    assert summaries_from_graph_state({}) == []
    assert summaries_from_graph_state(
        {
            "answer_synthesis_summary": "I grouped the evidence.",
            "retrieval_planning_summary": "I selected focused search.",
        }
    ) == [
        {
            "stage": "retrieval_planning",
            "text": "I selected focused search.",
            "source": "model_generated",
        },
        {
            "stage": "answer_synthesis",
            "text": "I grouped the evidence.",
            "source": "provider_reasoning_summary",
        },
    ]
