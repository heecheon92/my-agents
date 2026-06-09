"""Search-oriented document metadata enrichment for retrieval."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from my_agents.knowledge.models import DocumentModel
from my_agents.settings import Settings, get_settings

_METADATA_PROMPT_VERSION = "search-metadata-v1"
_SYSTEM_PROMPT = (
    "You generate document metadata only for retrieval/search quality. "
    "Prefer terms that a future user might type in any language, filename handles, "
    "domain vocabulary, abbreviations, aliases, and concrete concepts. Do not answer "
    "the document; produce compact metadata that improves semantic vector search."
)


class DocumentMetadataGenerationError(RuntimeError):
    """Raised when document metadata enrichment cannot be generated."""


class GeneratedDocumentMetadata(BaseModel):
    """LLM/deterministic document profile optimized for retrieval."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=700)
    summary: str = Field(min_length=1, max_length=2200)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    topics: list[str] = Field(default_factory=list, max_length=24)
    entities: list[str] = Field(default_factory=list, max_length=40)
    language: str = Field(default="unknown", min_length=2, max_length=40)
    confidence: str = Field(default="medium", min_length=1, max_length=40)

    @field_validator("keywords", "topics", "entities", mode="after")
    @classmethod
    def normalize_terms(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            term = " ".join(str(item).strip().split())
            if not term:
                continue
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(term[:120])
        return normalized


@dataclass(frozen=True)
class DocumentMetadataProfile:
    """Generated profile plus vector-search text and provenance."""

    metadata: GeneratedDocumentMetadata
    search_text: str
    generator: str
    model: str
    prompt_version: str = _METADATA_PROMPT_VERSION


class DocumentMetadataGenerator(Protocol):
    """Provider interface for document-level retrieval metadata."""

    name: str
    model: str

    def generate(self, document: DocumentModel) -> DocumentMetadataProfile:
        """Generate search-oriented metadata for one parsed document."""
        ...


class DeterministicDocumentMetadataGenerator:
    """Credential-free metadata generator used in tests and offline mode."""

    name = "deterministic"
    model = "deterministic-keyphrase-v1"

    def __init__(self, *, max_input_chars: int = 24000) -> None:
        self._max_input_chars = max_input_chars

    def generate(self, document: DocumentModel) -> DocumentMetadataProfile:
        sample = _document_sample(document, max_chars=self._max_input_chars)
        keywords = _top_keywords(sample, limit=24)
        entities = _entity_like_terms(sample, limit=24)
        topics = keywords[:10]
        title = _safe_title(document.title or document.source_filename or "Untitled document")
        description = _bounded_sentence(
            "Search profile for "
            f"{title}; useful queries include {', '.join([*keywords[:8], *entities[:4]])}."
        )
        summary = _bounded_sentence(_first_sentences(sample, max_chars=1000) or description)
        metadata = GeneratedDocumentMetadata(
            title=title,
            description=description,
            summary=summary,
            keywords=keywords,
            topics=topics,
            entities=entities,
            language=_guess_language(sample),
            confidence="deterministic",
        )
        return DocumentMetadataProfile(
            metadata=metadata,
            search_text=build_vector_search_text(
                metadata,
                source_filename=document.source_filename,
                explicit_title=document.title,
            ),
            generator=self.name,
            model=self.model,
        )


class OpenAIDocumentMetadataGenerator:
    """OpenAI-backed metadata generator using LangChain's ChatOpenAI boundary."""

    name = "openai"

    def __init__(self, settings: Settings, chat_model: Any | None = None) -> None:
        self._settings = settings
        self.model = settings.document_metadata_model or settings.openai_model
        api_key = settings.openai_api_key_value()
        if chat_model is None and not api_key:
            raise DocumentMetadataGenerationError(
                "OPENAI_API_KEY is required for OpenAI document metadata enrichment"
            )
        self._chat_model = chat_model or ChatOpenAI(**_build_metadata_chat_model_args(settings))

    def generate(self, document: DocumentModel) -> DocumentMetadataProfile:
        sample = _document_sample(
            document, max_chars=self._settings.document_metadata_max_input_chars
        )
        structured_model = self._chat_model.with_structured_output(
            GeneratedDocumentMetadata, method="function_calling"
        )
        response = structured_model.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        "Create metadata optimized for vector retrieval, not display copy.\n"
                        "Include likely search phrases, synonyms, abbreviations, filename/title "
                        "handles, domain-specific terminology, and multilingual hints if obvious.\n"
                        "Keep factual claims grounded in the provided document sample.\n\n"
                        f"Uploaded title: {document.title}\n"
                        f"Source filename: {document.source_filename or 'none'}\n"
                        f"Source type: {document.source_type}\n"
                        f"Page count: {document.source_page_count or 'unknown'}\n\n"
                        "Document sample:\n"
                        f"{sample}"
                    )
                ),
            ]
        )
        try:
            metadata = (
                response
                if isinstance(response, GeneratedDocumentMetadata)
                else GeneratedDocumentMetadata.model_validate(response)
            )
        except ValidationError as exc:
            raise DocumentMetadataGenerationError(str(exc)) from exc
        return DocumentMetadataProfile(
            metadata=metadata,
            search_text=build_vector_search_text(
                metadata,
                source_filename=document.source_filename,
                explicit_title=document.title,
            ),
            generator=self.name,
            model=self.model,
        )


def _build_metadata_chat_model_args(settings: Settings) -> dict[str, Any]:
    """Build ChatOpenAI args for metadata extraction over the Responses API."""
    return {
        "model": settings.document_metadata_model or settings.openai_model,
        "api_key": settings.openai_api_key_value(),
        "timeout": settings.openai_timeout_seconds,
        "max_completion_tokens": min(settings.openai_max_output_tokens, 1600),
        "use_responses_api": True,
        "output_version": "responses/v1",
    }


def build_document_metadata_generator(
    settings: Settings | None = None,
) -> DocumentMetadataGenerator:
    """Return the configured document metadata generator."""
    settings = settings or get_settings()
    if settings.document_metadata_enrichment_mode == "deterministic":
        return DeterministicDocumentMetadataGenerator(
            max_input_chars=settings.document_metadata_max_input_chars
        )
    if settings.document_metadata_enrichment_mode == "openai":
        return OpenAIDocumentMetadataGenerator(settings)
    if settings.response_mode == "openai" and settings.openai_api_key_value():
        return OpenAIDocumentMetadataGenerator(settings)
    return DeterministicDocumentMetadataGenerator(
        max_input_chars=settings.document_metadata_max_input_chars
    )


def build_vector_search_text(
    metadata: GeneratedDocumentMetadata,
    *,
    source_filename: str | None,
    explicit_title: str | None,
) -> str:
    """Build dense, search-focused text to embed for the metadata retrieval lane."""
    parts = [
        f"title: {metadata.title}",
        f"uploaded title: {explicit_title or ''}",
        f"filename: {source_filename or ''}",
        f"description: {metadata.description}",
        f"summary: {metadata.summary}",
        "keywords: " + ", ".join(metadata.keywords),
        "topics: " + ", ".join(metadata.topics),
        "entities: " + ", ".join(metadata.entities),
        f"language: {metadata.language}",
    ]
    return "\n".join(part for part in parts if part.strip())[:8000]


def metadata_json(values: list[str]) -> str:
    """Serialize generated metadata lists consistently."""
    return json.dumps(values, ensure_ascii=False)


def _document_sample(document: DocumentModel, *, max_chars: int) -> str:
    text = "\n".join(
        part
        for part in (
            f"Title: {document.title}",
            f"Filename: {document.source_filename}" if document.source_filename else "",
            document.content,
        )
        if part
    )
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) <= max_chars:
        return text
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    return (
        text[:head_chars]
        + "\n\n[...middle omitted for metadata extraction...]\n\n"
        + text[-tail_chars:]
    )


def _top_keywords(text: str, *, limit: int) -> list[str]:
    terms = [term for term in re.findall(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣._/-]{2,}", text)]
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "document",
        "title",
        "filename",
        "page",
        "pages",
        "are",
        "was",
        "were",
        "will",
        "can",
        "has",
        "have",
        "about",
        "into",
        "uses",
        "using",
        "대한",
        "문서",
        "파일",
        "그리고",
    }
    counts: Counter[str] = Counter()
    original: dict[str, str] = {}
    for term in terms:
        key = term.casefold().strip("._/-")
        if len(key) < 3 or key in stop:
            continue
        counts[key] += 1
        original.setdefault(key, term.strip("._/-"))
    return [original[key] for key, _ in counts.most_common(limit)]


def _entity_like_terms(text: str, *, limit: int) -> list[str]:
    patterns = [
        r"\b[A-Z][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+\b",
        r"\b[A-Z]{2,}[0-9][A-Z0-9_-]*\b",
        r"\bNCT\d+[_A-Za-z0-9-]*\b",
        r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,3}\b",
    ]
    seen: set[str] = set()
    entities: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = " ".join(str(match).split())[:120]
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            entities.append(value)
            if len(entities) >= limit:
                return entities
    return entities


def _first_sentences(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    boundary = max(compact.rfind(". ", 0, max_chars), compact.rfind("\n", 0, max_chars))
    if boundary < max_chars // 3:
        boundary = max_chars
    return compact[:boundary].strip()


def _safe_title(value: str) -> str:
    return " ".join(value.strip().split())[:240] or "Untitled document"


def _bounded_sentence(value: str) -> str:
    compact = " ".join(value.split())
    return compact[:2200] or "Searchable document metadata."


def _guess_language(text: str) -> str:
    korean = len(re.findall(r"[가-힣]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if korean and latin:
        return "mixed-ko-en" if korean > latin * 0.2 else "mostly-en"
    if korean:
        return "ko"
    if latin:
        return "en"
    return "unknown"
