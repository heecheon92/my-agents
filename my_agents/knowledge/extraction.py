"""Deterministic document ingestion and extraction service."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.knowledge.embeddings import deterministic_embedding, get_embedding_provider
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentModel,
    EntityMentionModel,
    EntityModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    ExtractionStatus,
)
from my_agents.knowledge.pdf_uploads import PDF_PAGE_SEPARATOR

_ENTITY_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*|[A-Z]{2,})"
    r"(?:\s+(?:[A-Z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*|[A-Z]{2,})){0,3}\b"
)
_TECH_TERM_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:Graph|Chain|Lang|API|SQL|DB)\b")
_CHUNK_TARGET_CHARS = 900
_CHUNK_OVERLAP_CHARS = 120


@dataclass(frozen=True)
class ExtractionSummary:
    """Counts produced by one deterministic extraction run."""

    run: ExtractionRunModel
    chunk_count: int
    entity_count: int
    relationship_count: int


class KnowledgeExtractionService:
    """Create chunks, embeddings, entities, and relationships."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._embedding_provider = get_embedding_provider()

    def ingest_document(self, document: DocumentModel) -> ExtractionSummary:
        run = ExtractionRunModel(document_id=document.id, status=ExtractionStatus.COMPLETED.value)
        self._db.add(run)
        self._db.flush()

        chunks = list(_chunk_document_text(document))
        embeddings = self._embedding_provider.embed_documents([content for content, *_ in chunks])
        entity_ids: set[str] = set()
        relationship_count = 0
        for ordinal, ((content, start, end, source_page), embedding) in enumerate(
            zip(chunks, embeddings, strict=True)
        ):
            chunk = DocumentChunkModel(
                document_id=document.id,
                extraction_run_id=run.id,
                ordinal=ordinal,
                content=content,
                start_offset=start,
                end_offset=end,
                source_page=source_page,
                embedding_json=json.dumps(embedding),
            )
            self._db.add(chunk)
            self._db.flush()
            chunk_entity_ids = []
            for entity_name in _extract_entity_names(content):
                entity = self._get_or_create_entity(entity_name)
                entity_ids.add(entity.id)
                chunk_entity_ids.append(entity.id)
                self._db.add(
                    EntityMentionModel(
                        entity_id=entity.id,
                        chunk_id=chunk.id,
                        document_id=document.id,
                        extraction_run_id=run.id,
                    )
                )
            for source_id, target_id in zip(chunk_entity_ids, chunk_entity_ids[1:], strict=False):
                self._db.add(
                    EntityRelationshipModel(
                        source_entity_id=source_id,
                        target_entity_id=target_id,
                        relation_type="co_occurs_with",
                        document_id=document.id,
                        chunk_id=chunk.id,
                        extraction_run_id=run.id,
                    )
                )
                relationship_count += 1
        self._db.commit()
        self._db.refresh(run)
        return ExtractionSummary(
            run=run,
            chunk_count=len(chunks),
            entity_count=len(entity_ids),
            relationship_count=relationship_count,
        )

    def _get_or_create_entity(self, name: str) -> EntityModel:
        entity = self._db.scalar(select(EntityModel).where(EntityModel.name == name))
        if entity is not None:
            return entity
        entity = EntityModel(name=name)
        self._db.add(entity)
        self._db.flush()
        return entity


def _chunk_document_text(document: DocumentModel) -> list[tuple[str, int, int, int | None]]:
    if document.source_type == "pdf":
        return _chunk_pdf_text(document.content)
    return [(content, start, end, None) for content, start, end in _chunk_text(document.content)]


def _chunk_pdf_text(text: str) -> list[tuple[str, int, int, int | None]]:
    pages = text.split(PDF_PAGE_SEPARATOR)
    chunks: list[tuple[str, int, int, int | None]] = []
    base_offset = 0
    for page_index, page_text in enumerate(pages, start=1):
        for content, start, end in _chunk_text(page_text):
            chunks.append((content, base_offset + start, base_offset + end, page_index))
        base_offset += len(page_text)
        if page_index < len(pages):
            base_offset += len(PDF_PAGE_SEPARATOR)
    return chunks or [("", 0, 0, None)]


def _chunk_text(
    text: str,
    *,
    max_chars: int = _CHUNK_TARGET_CHARS,
) -> list[tuple[str, int, int]]:
    stripped = text.strip()
    if not stripped:
        return [("", 0, 0)]
    chunks: list[tuple[str, int, int]] = []
    units = _semantic_units(stripped)
    for unit, start, end in units:
        if not unit:
            continue
        if len(unit) <= max_chars:
            chunks.append((unit, start, end))
            continue
        chunks.extend(_fixed_width_units(unit, base_offset=start))
    return chunks or [(stripped, 0, len(stripped))]


def _extract_entity_names(text: str) -> list[str]:
    seen: set[str] = set()
    entities: list[str] = []
    for match in [*_ENTITY_PATTERN.finditer(text), *_TECH_TERM_PATTERN.finditer(text)]:
        name = match.group(0).strip()
        if len(name) < 2 or name.casefold() in seen or _is_low_value_entity(name):
            continue
        seen.add(name.casefold())
        entities.append(name)
    return entities


def _deterministic_embedding(text: str, dimensions: int = 32) -> list[float]:
    """Backward-compatible wrapper around the deterministic embedding provider."""
    return deterministic_embedding(text, dimensions=dimensions)


def _semantic_units(text: str) -> list[tuple[str, int, int]]:
    units: list[tuple[str, int, int]] = []
    for match in re.finditer(r"\S(?:.*?\S)?(?=\n{2,}|\Z)", text, flags=re.DOTALL):
        paragraph = match.group(0).strip()
        if not paragraph:
            continue
        if len(paragraph) <= _CHUNK_TARGET_CHARS:
            units.append((paragraph, match.start(), match.end()))
            continue
        units.extend(_sentence_units(paragraph, base_offset=match.start()))
    return units or [(text, 0, len(text))]


def _sentence_units(text: str, *, base_offset: int) -> list[tuple[str, int, int]]:
    units: list[tuple[str, int, int]] = []
    cursor = 0
    pattern = re.compile(r".+?(?:[.!?。！？]+[\"')\]]?\s+|\Z)", flags=re.DOTALL)
    for match in pattern.finditer(text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        start = text.find(sentence, cursor)
        if start < 0:
            start = match.start()
        cursor = start + len(sentence)
        if len(sentence) <= _CHUNK_TARGET_CHARS:
            units.append((sentence, base_offset + start, base_offset + start + len(sentence)))
            continue
        units.extend(_fixed_width_units(sentence, base_offset=base_offset + start))
    return units


def _fixed_width_units(text: str, *, base_offset: int) -> list[tuple[str, int, int]]:
    units: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + _CHUNK_TARGET_CHARS)
        if end < len(text):
            split = text.rfind(" ", start, end)
            if split > start + 200:
                end = split
        segment = text[start:end].strip()
        if segment:
            segment_start = start + text[start:end].find(segment)
            units.append((segment, base_offset + segment_start, base_offset + end))
        if end >= len(text):
            break
        start = max(end - _CHUNK_OVERLAP_CHARS, start + 1)
    return units


def _is_low_value_entity(name: str) -> bool:
    return name.casefold() in {"the", "and", "for", "this", "that", "with", "page"}
