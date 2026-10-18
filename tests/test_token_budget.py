"""Model-aware estimates stay offline and distinguish unknown tokenizers."""

import hashlib
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from my_agents import token_budget
from my_agents.agents.general_assistant.responders import _build_input_messages
from my_agents.schemas import RouteDecision
from my_agents.settings import get_settings


class TinyEncoding:
    def encode(self, text, **kwargs):
        return text.split()


def test_counter_uses_selected_model_mapping(monkeypatch):
    seen = []

    def load(name):
        seen.append(name)
        return TinyEncoding()

    monkeypatch.setattr(token_budget, "_cached_encoding", load)
    counter = token_budget.text_token_counter("gpt-4o")
    assert seen == ["o200k_base"]
    assert counter.count("one two three") == 3
    assert counter.method == "model_tokenizer"
    assert counter.encoding_name == "o200k_base"


def test_unknown_model_never_guesses_an_encoding(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Unknown model must not load a guessed tokenizer")

    monkeypatch.setattr(token_budget, "_cached_encoding", forbidden)
    counter = token_budget.text_token_counter("unregistered-test-model")
    assert counter.method == "utf8_byte_fallback"
    assert counter.encoding_name is None
    assert counter.count("한글🙂") == len("한글🙂".encode())


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_cache_cannot_trigger_network_download(tmp_path, monkeypatch, corrupt):
    import tiktoken

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    token_budget._cached_encoding.cache_clear()
    if corrupt:
        key = hashlib.sha1(
            b"https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
        ).hexdigest()
        (Path(tmp_path) / key).write_bytes(b"broken cache")

    def forbidden(*args):
        raise AssertionError("Request-time tokenizer download is forbidden")

    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    try:
        assert token_budget.text_token_counter("gpt-4o").method == "utf8_byte_fallback"
    finally:
        token_budget._cached_encoding.cache_clear()


def test_answer_budget_counts_final_disclosure_and_preserves_current_question(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_CONTEXT_INPUT_TOKENS", "8000")
    get_settings.cache_clear()
    question = "What does the document say about recovery?"
    rendered = _build_input_messages(
        model_name="unknown-fixture-model",
        messages=[HumanMessage(content=question)],
        route=RouteDecision(label="research_helper", explanation="document question"),
        guidance="Use the authorized evidence.",
        answer_mode="document_grounded",
        retrieved_context=[{"document_id": "doc", "title": "Fixture", "snippet": "한글" * 20000}],
    )
    manifest = rendered[-1].additional_kwargs["context_delivery"]
    measured = sum(len(str(message.content).encode()) + 12 for message in rendered)
    assert manifest["input_model"] == "unknown-fixture-model"
    assert manifest["token_counting_method"] == "utf8_byte_fallback"
    assert manifest["estimated_text_input_tokens"] == measured <= 8000
    assert manifest["budget_truncated"]
    assert question in str(rendered[-1].content)
    assert "Earlier context is incomplete." in str(rendered[-1].content)
    assert "한글" not in str(rendered[-1].content)


def test_workspace_uses_its_own_model_and_counts_disclosure(monkeypatch):
    from my_agents.document_workspace.service import (
        _document_workspace_instructions,
        _document_workspace_prompt,
    )

    prompt = _document_workspace_prompt(
        model_name="unknown-workspace-model",
        input_budget=8000,
        messages=[HumanMessage(content="Explain the authorized document.")],
        route=RouteDecision(label="research_helper", explanation="documents"),
        guidance="Read the file.",
        capability=None,
        answer_mode="document_grounded",
        retrieved_context=[{"title": "Fixture", "snippet": "한글" * 20000}],
        memory_context=[],
        source_conflicts=[],
        attachments=[],
    )
    assert len(prompt.encode()) + len(_document_workspace_instructions().encode()) <= 8000
    assert "Explain the authorized document." in prompt
    assert "Earlier context is incomplete." in prompt
