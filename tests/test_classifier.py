"""Deterministic route-classifier contract tests."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from my_agents.agents.general_assistant.classifier import classify_messages

from .conftest import REPRESENTATIVE_PROMPTS, assert_route_decision, get_classifier


@pytest.mark.parametrize("expected_label,prompt", REPRESENTATIVE_PROMPTS.items())
def test_representative_prompts_classify_to_each_route_label(
    expected_label: str, prompt: str
) -> None:
    classify = get_classifier()

    decision = classify(prompt)

    assert_route_decision(decision, expected_label=expected_label)


def test_latest_document_question_is_not_polluted_by_prior_project_history() -> None:
    decision = classify_messages(
        [
            HumanMessage(content="Plan the backend milestone roadmap."),
            AIMessage(content="A useful project plan has tasks and next steps."),
            HumanMessage(content="연말정산 관련 문서 업로드 했는데 내용좀 알려줘"),
        ]
    )

    assert_route_decision(decision, expected_label="general_assistant")
