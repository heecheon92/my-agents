"""Document metadata enrichment provider tests."""

from __future__ import annotations

from langchain_core.messages import BaseMessage

from my_agents.knowledge.metadata_enrichment import (
    GeneratedDocumentMetadata,
    OpenAIDocumentMetadataGenerator,
    _build_metadata_chat_model_args,
)
from my_agents.knowledge.models import DocumentModel
from my_agents.settings import Settings


class FakeStructuredMetadataModel:
    def __init__(self) -> None:
        self.messages: list[list[BaseMessage]] = []

    def invoke(self, messages: list[BaseMessage]) -> GeneratedDocumentMetadata:
        self.messages.append(messages)
        return GeneratedDocumentMetadata(
            title="Searchable markdown notes",
            description="Vector metadata for LangGraph study notes and agent routing.",
            summary="Study notes about LangGraph, retrieval, and agent orchestration.",
            keywords=["LangGraph", "retrieval", "agent orchestration"],
            topics=["RAG"],
            entities=["ContextForge"],
            language="en",
            confidence="high",
        )


class FakeMetadataChatModel:
    def __init__(self) -> None:
        self.schema = None
        self.method = None
        self.structured = FakeStructuredMetadataModel()

    def with_structured_output(self, schema, *, method: str):  # noqa: ANN001, ANN201
        self.schema = schema
        self.method = method
        return self.structured


def test_metadata_chat_args_do_not_use_responses_api_parsed_output(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_METADATA_MODEL", "gpt-metadata")
    settings = Settings(_env_file=None)

    args = _build_metadata_chat_model_args(settings)

    assert args["model"] == "gpt-metadata"
    assert args["api_key"] == "test-key"
    assert "use_responses_api" not in args
    assert "output_version" not in args


def test_openai_metadata_generator_uses_function_calling_structured_output(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = Settings(_env_file=None)
    chat_model = FakeMetadataChatModel()
    generator = OpenAIDocumentMetadataGenerator(settings, chat_model=chat_model)
    document = DocumentModel(
        title="00 LangGraph Study Notes Index",
        content="# LangGraph Study Notes\n\nContextForge retrieves relevant notes.",
        source_type="markdown",
        source_filename="00 LangGraph Study Notes Index.md",
        owner_user_id="user-1",
        knowledge_base_id="kb-1",
    )

    profile = generator.generate(document)

    assert chat_model.schema is GeneratedDocumentMetadata
    assert chat_model.method == "function_calling"
    assert profile.generator == "openai"
    assert "LangGraph" in profile.search_text
    assert chat_model.structured.messages
