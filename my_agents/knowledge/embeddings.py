"""Embedding provider boundary for document ingestion and retrieval ranking."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from typing import Protocol

from langchain_openai import OpenAIEmbeddings

from my_agents.observability.metrics import track_embedding_call
from my_agents.settings import Settings, get_settings

DETERMINISTIC_EMBEDDING_DIMENSIONS = 32


class EmbeddingProviderConfigurationError(RuntimeError):
    """Raised when the selected embedding provider is missing required settings."""


class EmbeddingProvider(Protocol):
    """Provider interface for chunk and query embeddings."""

    provider: str
    model: str
    dimensions: int | None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed one or more document chunks."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed one retrieval query."""
        ...


class DeterministicEmbeddingProvider:
    """Offline lexical-hash embedding provider for tests and local demos."""

    provider = "deterministic"
    model = "lexical-hash-v1"
    dimensions = DETERMINISTIC_EMBEDDING_DIMENSIONS

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with track_embedding_call(
            provider=self.provider,
            model=self.model,
            operation="documents",
        ):
            return [deterministic_embedding(text, dimensions=self.dimensions) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        with track_embedding_call(
            provider=self.provider,
            model=self.model,
            operation="query",
        ):
            return deterministic_embedding(text, dimensions=self.dimensions)


class OpenAIEmbeddingProvider:
    """OpenAI embedding provider backed by langchain-openai."""

    provider = "openai"

    def __init__(self, settings: Settings, embeddings: OpenAIEmbeddings | None = None) -> None:
        api_key = settings.openai_api_key_value()
        if embeddings is None and not api_key:
            raise EmbeddingProviderConfigurationError(
                "OPENAI_API_KEY is required when MY_AGENTS_EMBEDDING_MODE=openai"
            )
        self.model = settings.openai_embedding_model
        self.dimensions = settings.openai_embedding_dimensions
        self._embeddings = embeddings or OpenAIEmbeddings(
            model=self.model,
            dimensions=self.dimensions,
            api_key=api_key,
            timeout=settings.openai_embedding_timeout_seconds,
            chunk_size=settings.embedding_batch_size,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with track_embedding_call(
            provider=self.provider,
            model=self.model,
            operation="documents",
        ):
            return [list(vector) for vector in self._embeddings.embed_documents(texts)]

    def embed_query(self, text: str) -> list[float]:
        with track_embedding_call(
            provider=self.provider,
            model=self.model,
            operation="query",
        ):
            return list(self._embeddings.embed_query(text))


def deterministic_embedding(
    text: str,
    dimensions: int = DETERMINISTIC_EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Return a deterministic lexical-hash embedding fixture for offline tests."""
    vector = [0.0] * dimensions
    tokens = re.findall(r"[A-Za-z0-9가-힣]+", text.casefold())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:2], "big") % dimensions
        sign = 1 if digest[2] % 2 == 0 else -1
        vector[bucket] += sign * (1.0 + min(len(token), 12) / 12)
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Build the configured embedding provider."""
    if settings.embedding_mode == "openai":
        return OpenAIEmbeddingProvider(settings)
    return DeterministicEmbeddingProvider()


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Return the process-level embedding provider."""
    return build_embedding_provider(get_settings())
