"""Embedding provider boundary tests."""

from __future__ import annotations

from my_agents.knowledge.embeddings import DeterministicEmbeddingProvider, OpenAIEmbeddingProvider
from my_agents.settings import Settings


class FakeLangChainEmbeddings:
    """Tiny stand-in for langchain-openai embeddings without network calls."""

    def __init__(self) -> None:
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(texts)
        return [[float(index), 1.0] for index, _text in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [1.0, 0.0]


def test_deterministic_embedding_provider_is_offline_and_stable() -> None:
    provider = DeterministicEmbeddingProvider()

    first = provider.embed_query("LangGraph retrieval")
    second = provider.embed_query("LangGraph retrieval")

    assert provider.provider == "deterministic"
    assert provider.model == "lexical-hash-v1"
    assert provider.dimensions == 32
    assert first == second
    assert len(first) == 32


def test_openai_embedding_provider_wraps_langchain_embeddings_without_direct_sdk() -> None:
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_EMBEDDING_MODE="openai",
        OPENAI_API_KEY="test-key",
        MY_AGENTS_OPENAI_EMBEDDING_MODEL="text-embedding-3-small",
        MY_AGENTS_OPENAI_EMBEDDING_DIMENSIONS=2,
    )
    fake_embeddings = FakeLangChainEmbeddings()
    provider = OpenAIEmbeddingProvider(settings, embeddings=fake_embeddings)  # type: ignore[arg-type]

    documents = provider.embed_documents(["alpha", "beta"])
    query = provider.embed_query("find alpha")

    assert provider.provider == "openai"
    assert provider.model == "text-embedding-3-small"
    assert provider.dimensions == 2
    assert documents == [[0.0, 1.0], [1.0, 1.0]]
    assert query == [1.0, 0.0]
    assert fake_embeddings.document_calls == [["alpha", "beta"]]
    assert fake_embeddings.query_calls == ["find alpha"]
