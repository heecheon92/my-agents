"""Deterministic document ingestion and extraction service."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from my_agents.knowledge.embeddings import deterministic_embedding, get_embedding_provider
from my_agents.knowledge.metadata_enrichment import (
    DeterministicDocumentMetadataGenerator,
    build_document_metadata_generator,
    metadata_json,
)
from my_agents.knowledge.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    EntityMentionModel,
    EntityModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    ExtractionStatus,
    StructuredKnowledgeEntityModel,
    StructuredKnowledgeEntityType,
)
from my_agents.knowledge.pdf_uploads import PDF_PAGE_SEPARATOR
from my_agents.settings import get_settings

_ENTITY_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*|[A-Z]{2,})"
    r"(?:\s+(?:[A-Z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*|[A-Z]{2,})){0,3}\b"
)
_TECH_TERM_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:Graph|Chain|Lang|API|SQL|DB)\b")
_API_ENDPOINT_PATTERN = re.compile(
    r"\b(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+"
    r"(?P<path>/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;={}\-%]+)"
)
_CONFIG_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b")
_SHELL_COMMAND_PATTERN = re.compile(r"(?m)^\s*(?:\$|>)\s*(?P<command>[A-Za-z][^\n]{2,160})$")
_ERROR_CODE_PATTERN = re.compile(r"\b(?:HTTP\s*)?(?:[45]\d{2}|ERR_[A-Z0-9_]+)\b")
_DATABASE_TABLE_PATTERN = re.compile(
    r"\b(?:table|from|join|into|update)\s+[`\"]?(?P<table>[A-Za-z][A-Za-z0-9_]{2,})[`\"]?",
    flags=re.IGNORECASE,
)
_CHUNK_TARGET_CHARS = 1500
_CHUNK_OVERLAP_CHARS = 200
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionSummary:
    """Counts produced by one deterministic extraction run."""

    run: ExtractionRunModel
    chunk_count: int
    entity_count: int
    relationship_count: int


@dataclass(frozen=True)
class StructuredEntityExtraction:
    """One deterministic structured fact extracted from a chunk."""

    entity_type: StructuredKnowledgeEntityType
    label: str
    attributes: dict[str, str]
    start: int
    end: int
    confidence: str


class KnowledgeExtractionService:
    """Create chunks, embeddings, entities, and relationships."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._embedding_provider = get_embedding_provider()

    def create_extraction_run(self, document_id: str) -> ExtractionRunModel:
        """Create a queued extraction run without doing document work."""
        run = ExtractionRunModel(
            document_id=document_id,
            status=ExtractionStatus.PENDING.value,
            stage="queued",
            progress_percent=0,
            chunk_count=0,
            entity_count=0,
            relationship_count=0,
        )
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)
        return run

    def ingest_document(
        self,
        document: DocumentModel,
        *,
        run: ExtractionRunModel | None = None,
    ) -> ExtractionSummary:
        """Execute extraction for a document, optionally into an existing queued run."""
        if run is None:
            run = ExtractionRunModel(document_id=document.id, status=ExtractionStatus.PENDING.value)
            self._db.add(run)
            self._db.flush()
        run_id = run.id
        logger.info(
            "knowledge_ingestion.start run_id=%s document_id=%s knowledge_base_id=%s "
            "source_type=%s parser=%s chars=%d",
            run_id,
            document.id,
            document.knowledge_base_id,
            document.source_type,
            document.parser_name,
            len(document.content),
        )
        try:
            self._mark_progress(run, status=ExtractionStatus.RUNNING, stage="chunking", percent=15)
            self._clear_prior_extraction_artifacts(document.id)
            chunks = list(_chunk_document_text(document))
            logger.info(
                "knowledge_ingestion.chunked run_id=%s document_id=%s chunk_count=%d",
                run_id,
                document.id,
                len(chunks),
            )
            run.chunk_count = len(chunks)
            self._db.commit()

            self._mark_progress(run, status=ExtractionStatus.RUNNING, stage="embedding", percent=45)
            embeddings = self._embedding_provider.embed_documents(
                [content for content, *_ in chunks]
            )
            logger.info(
                "knowledge_ingestion.embedded run_id=%s document_id=%s embedding_count=%d "
                "provider=%s model=%s",
                run_id,
                document.id,
                len(embeddings),
                getattr(
                    self._embedding_provider, "provider", type(self._embedding_provider).__name__
                ),
                getattr(self._embedding_provider, "model", None),
            )
            entity_ids: set[str] = set()
            relationship_count = 0
            stores_sql_vector = _stores_sql_embedding_vector(self._db)
            if stores_sql_vector:
                self._mark_progress(
                    run,
                    status=ExtractionStatus.RUNNING,
                    stage="indexing",
                    percent=70,
                )
            self._mark_progress(
                run,
                status=ExtractionStatus.RUNNING,
                stage="entities",
                percent=85,
            )
            entity_names_by_chunk = [_extract_entity_names(content) for content, *_ in chunks]
            entity_by_name = self._get_or_create_entities(
                name for names in entity_names_by_chunk for name in names
            )
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
                if stores_sql_vector:
                    self._store_sql_embedding_vector(
                        table=DocumentChunkModel.__table__, row_id=chunk.id, embedding=embedding
                    )
                chunk_entity_ids = []
                for entity_name in entity_names_by_chunk[ordinal]:
                    entity = entity_by_name[entity_name]
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
                for source_id, target_id in zip(
                    chunk_entity_ids, chunk_entity_ids[1:], strict=False
                ):
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
                for structured in _extract_structured_entities(content):
                    self._db.add(
                        StructuredKnowledgeEntityModel(
                            document_id=document.id,
                            chunk_id=chunk.id,
                            extraction_run_id=run.id,
                            entity_type=structured.entity_type.value,
                            label=structured.label,
                            attributes_json=json.dumps(
                                structured.attributes,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            source_page=source_page,
                            start_offset=start + structured.start,
                            end_offset=start + structured.end,
                            confidence=structured.confidence,
                        )
                    )

            self._mark_progress(
                run,
                status=ExtractionStatus.RUNNING,
                stage="metadata",
                percent=95,
            )
            self._store_document_metadata_profile(document=document, run=run)

            run.status = ExtractionStatus.COMPLETED.value
            run.stage = "completed"
            run.progress_percent = 100
            run.chunk_count = len(chunks)
            run.entity_count = len(entity_ids)
            run.relationship_count = relationship_count
            run.error = None
            run.completed_at = datetime.now(UTC)
            self._db.commit()
            self._db.refresh(run)
            logger.info(
                "knowledge_ingestion.completed run_id=%s document_id=%s chunks=%d entities=%d "
                "relationships=%d",
                run.id,
                document.id,
                len(chunks),
                len(entity_ids),
                relationship_count,
            )
            return ExtractionSummary(
                run=run,
                chunk_count=len(chunks),
                entity_count=len(entity_ids),
                relationship_count=relationship_count,
            )
        except Exception as exc:
            self._db.rollback()
            run = self._db.get(ExtractionRunModel, run_id)
            if run is not None:
                run.status = ExtractionStatus.FAILED.value
                run.stage = "failed"
                run.error = _safe_error_message(exc)
                run.completed_at = datetime.now(UTC)
                self._db.commit()
            logger.exception(
                "knowledge_ingestion.failed run_id=%s document_id=%s error=%s",
                run_id,
                document.id,
                _safe_error_message(exc),
            )
            raise

    def _clear_prior_extraction_artifacts(self, document_id: str) -> None:
        """Remove stale document-derived rows so repeated ingest is idempotent.

        Completed chat runs and assistant messages are preserved, but citations for old
        chunk IDs are removed because they can no longer point at the refreshed chunk set
        safely across databases with foreign-key enforcement.
        """
        self._db.execute(delete(CitationModel).where(CitationModel.document_id == document_id))
        self._db.execute(
            delete(EntityRelationshipModel).where(
                EntityRelationshipModel.document_id == document_id
            )
        )
        self._db.execute(
            delete(EntityMentionModel).where(EntityMentionModel.document_id == document_id)
        )
        self._db.execute(
            delete(StructuredKnowledgeEntityModel).where(
                StructuredKnowledgeEntityModel.document_id == document_id
            )
        )
        self._db.execute(
            delete(DocumentMetadataProfileModel).where(
                DocumentMetadataProfileModel.document_id == document_id
            )
        )
        self._db.execute(
            delete(DocumentChunkModel).where(DocumentChunkModel.document_id == document_id)
        )
        self._db.flush()

    def _mark_progress(
        self,
        run: ExtractionRunModel,
        *,
        status: ExtractionStatus,
        stage: str,
        percent: int,
    ) -> None:
        run.status = status.value
        run.stage = stage
        run.progress_percent = percent
        run.error = None
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        self._db.commit()
        self._db.refresh(run)

    def _get_or_create_entity(self, name: str) -> EntityModel:
        """Create an entity safely when parallel ingestions share names."""
        entity = self._db.scalar(select(EntityModel).where(EntityModel.name == name))
        if entity is not None:
            return entity
        if self._db.get_bind().dialect.name in {"postgresql", "sqlite"}:
            self._insert_entity_if_missing(name)
            entity = self._db.scalar(select(EntityModel).where(EntityModel.name == name))
            if entity is not None:
                return entity
        entity = EntityModel(name=name)
        self._db.add(entity)
        self._db.flush()
        return entity

    def _get_or_create_entities(self, names: Iterable[str]) -> dict[str, EntityModel]:
        """Create entities in a stable order to avoid unique-index lock cycles."""
        unique_names = sorted(set(names), key=lambda value: (value.casefold(), value))
        return {name: self._get_or_create_entity(name) for name in unique_names}

    def _insert_entity_if_missing(self, name: str) -> None:
        dialect = self._db.get_bind().dialect.name
        values = {"id": str(uuid.uuid4()), "name": name}
        if dialect == "postgresql":
            statement = (
                postgresql_insert(EntityModel)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[EntityModel.name])
            )
        else:
            statement = (
                sqlite_insert(EntityModel)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[EntityModel.name])
            )
        self._db.execute(statement)

    def _store_document_metadata_profile(
        self, *, document: DocumentModel, run: ExtractionRunModel
    ) -> None:
        settings = get_settings()
        generator = build_document_metadata_generator(settings)
        try:
            profile = generator.generate(document)
        except Exception:
            if settings.document_metadata_enrichment_mode == "openai":
                raise
            fallback = DeterministicDocumentMetadataGenerator(
                max_input_chars=settings.document_metadata_max_input_chars
            )
            profile = fallback.generate(document)
        embedding = self._embedding_provider.embed_documents([profile.search_text])[0]
        row = DocumentMetadataProfileModel(
            document_id=document.id,
            extraction_run_id=run.id,
            generated_title=profile.metadata.title,
            description=profile.metadata.description,
            summary=profile.metadata.summary,
            keywords_json=metadata_json(profile.metadata.keywords),
            topics_json=metadata_json(profile.metadata.topics),
            entities_json=metadata_json(profile.metadata.entities),
            language=profile.metadata.language,
            confidence=profile.metadata.confidence,
            generator=profile.generator,
            model=profile.model,
            prompt_version=profile.prompt_version,
            search_text=profile.search_text,
            embedding_json=json.dumps(embedding),
        )
        self._db.add(row)
        self._db.flush()
        if _stores_sql_embedding_vector(self._db):
            self._store_sql_embedding_vector(
                table=DocumentMetadataProfileModel.__table__, row_id=row.id, embedding=embedding
            )

    def _store_sql_embedding_vector(self, *, table, row_id: str, embedding: list[float]) -> None:
        embedding_vector = table.c.embedding_vector
        self._db.execute(
            update(table).where(table.c.id == row_id).values({embedding_vector: embedding})
        )


def _chunk_document_text(document: DocumentModel) -> list[tuple[str, int, int, int | None]]:
    if document.source_type == "pdf":
        return _chunk_pdf_text(document.content)
    return [(content, start, end, None) for content, start, end in _chunk_text(document.content)]


def _stores_sql_embedding_vector(db: Session) -> bool:
    """Return whether chunks should persist the pgvector column for SQL search."""
    bind = db.get_bind()
    return bind.dialect.name == "postgresql"


def _safe_error_message(exc: Exception) -> str:
    """Return a bounded display-safe failure reason for extraction polling."""
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"[:300]


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


def _extract_structured_entities(text: str) -> list[StructuredEntityExtraction]:
    seen: set[tuple[str, str]] = set()
    entities: list[StructuredEntityExtraction] = []

    def add(entity: StructuredEntityExtraction) -> None:
        key = (entity.entity_type.value, entity.label.casefold())
        if key in seen:
            return
        seen.add(key)
        entities.append(entity)

    for match in _API_ENDPOINT_PATTERN.finditer(text):
        method = match.group("method").upper()
        path = match.group("path").rstrip(".,);")
        add(
            StructuredEntityExtraction(
                entity_type=StructuredKnowledgeEntityType.API_ENDPOINT,
                label=f"{method} {path}",
                attributes={"method": method, "path": path},
                start=match.start(),
                end=match.start() + len(f"{method} {path}"),
                confidence="pattern:http_method_path",
            )
        )

    for match in _CONFIG_KEY_PATTERN.finditer(text):
        key = match.group(0)
        add(
            StructuredEntityExtraction(
                entity_type=StructuredKnowledgeEntityType.CONFIG_KEY,
                label=key,
                attributes={"key": key},
                start=match.start(),
                end=match.end(),
                confidence="pattern:uppercase_key",
            )
        )

    for match in _SHELL_COMMAND_PATTERN.finditer(text):
        command = match.group("command").strip()
        add(
            StructuredEntityExtraction(
                entity_type=StructuredKnowledgeEntityType.COMMAND,
                label=command.split()[0],
                attributes={"command": command},
                start=match.start("command"),
                end=match.end("command"),
                confidence="pattern:shell_prompt",
            )
        )

    for match in _ERROR_CODE_PATTERN.finditer(text):
        code = match.group(0)
        add(
            StructuredEntityExtraction(
                entity_type=StructuredKnowledgeEntityType.ERROR_CODE,
                label=code,
                attributes={"code": code},
                start=match.start(),
                end=match.end(),
                confidence="pattern:error_code",
            )
        )

    for match in _DATABASE_TABLE_PATTERN.finditer(text):
        table_name = match.group("table")
        add(
            StructuredEntityExtraction(
                entity_type=StructuredKnowledgeEntityType.DATABASE_TABLE,
                label=table_name,
                attributes={"table": table_name},
                start=match.start("table"),
                end=match.end("table"),
                confidence="pattern:sql_table_reference",
            )
        )

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
