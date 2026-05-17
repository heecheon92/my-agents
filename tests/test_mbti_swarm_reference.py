"""Tests for the reference MBTI swarm implementation."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from my_agents.simulated_agents.mbti.swarm_reference import (
    FAGENT_NAME,
    TAGENT_NAME,
    build_fagent,
    build_mbti_swarm_reference,
    build_tagent,
)


def test_reference_agents_are_named_for_swarm_handoffs() -> None:
    model = FakeListChatModel(responses=["ok"])

    tagent = build_tagent(model)
    fagent = build_fagent(model)

    assert tagent.name == TAGENT_NAME
    assert fagent.name == FAGENT_NAME
    assert "tools" in tagent.get_graph().nodes
    assert "tools" in fagent.get_graph().nodes


def test_reference_swarm_contains_parent_nodes_for_both_agents() -> None:
    model = FakeListChatModel(responses=["ok"])

    swarm = build_mbti_swarm_reference(model)
    nodes = set(swarm.get_graph().nodes)

    assert TAGENT_NAME in nodes
    assert FAGENT_NAME in nodes
    assert "__start__" in nodes


def test_reference_module_import_is_credential_free() -> None:
    import my_agents.simulated_agents.mbti.swarm_reference as reference

    assert callable(reference.build_mbti_swarm_reference)
