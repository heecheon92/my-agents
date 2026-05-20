"""Deterministic document ingestion and extraction service."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

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

_ENTITY_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,2}\b")


@dataclass(frozen=True)
class ExtractionSummary:
    """Counts produced by one deterministic extraction run."""

    run: ExtractionRunModel
    chunk_count: int
    entity_count: int
    relationship_count: int


class KnowledgeExtractionService:
    """Create chunks, deterministic embeddings, entities, and relationships."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def ingest_document(self, document: DocumentModel) -> ExtractionSummary:
        run = ExtractionRunModel(document_id=document.id, status=ExtractionStatus.COMPLETED.value)
        self._db.add(run)
        self._db.flush()

        chunks = list(_chunk_document_text(document))
        entity_ids: set[str] = set()
        relationship_count = 0
        for ordinal, (content, start, end, source_page) in enumerate(chunks):
            chunk = DocumentChunkModel(
                document_id=document.id,
                extraction_run_id=run.id,
                ordinal=ordinal,
                content=content,
                start_offset=start,
                end_offset=end,
                source_page=source_page,
                embedding_json=json.dumps(_deterministic_embedding(content)),
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


def _chunk_text(text: str, max_chars: int = 500) -> list[tuple[str, int, int]]:
    stripped = text.strip()
    if not stripped:
        return [("", 0, 0)]
    chunks: list[tuple[str, int, int]] = []
    cursor = 0
    for raw_part in re.split(r"\n\s*\n", stripped):
        part = raw_part.strip()
        if not part:
            continue
        start = stripped.find(part, cursor)
        if start < 0:
            start = cursor
        while len(part) > max_chars:
            segment = part[:max_chars]
            chunks.append((segment, start, start + len(segment)))
            part = part[max_chars:]
            start += len(segment)
        chunks.append((part, start, start + len(part)))
        cursor = start + len(part)
    return chunks or [(stripped, 0, len(stripped))]


def _extract_entity_names(text: str) -> list[str]:
    seen: set[str] = set()
    entities: list[str] = []
    for match in _ENTITY_PATTERN.finditer(text):
        name = match.group(0).strip()
        if len(name) < 2 or name in seen:
            continue
        seen.add(name)
        entities.append(name)
    return entities


def _deterministic_embedding(text: str, dimensions: int = 8) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round(byte / 255, 6) for byte in digest[:dimensions]]
