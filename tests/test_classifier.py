"""Deterministic route-classifier contract tests."""

from __future__ import annotations

import pytest

from .conftest import REPRESENTATIVE_PROMPTS, assert_route_decision, get_classifier


@pytest.mark.parametrize("expected_label,prompt", REPRESENTATIVE_PROMPTS.items())
def test_representative_prompts_classify_to_each_route_label(
    expected_label: str, prompt: str
) -> None:
    classify = get_classifier()

    decision = classify(prompt)

    assert_route_decision(decision, expected_label=expected_label)
