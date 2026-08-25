"""Permission-aware retrieval with JSON-backed semantic vector ranking."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from rank_bm25 import BM25Okapi
from sqlalchemy import and_, desc, false, func, inspect, or_, select, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, defer

from my_agents.groups.models import MembershipModel
from my_agents.knowledge.auth import retrievable_knowledge_base_filter
from my_agents.knowledge.embeddings import EmbeddingProvider, get_embedding_provider
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    DocumentPermissionModel,
    EntityMentionModel,
    KnowledgeBaseModel,
    KnowledgeBasePurpose,
    KnowledgeBaseScope,
    StructuredKnowledgeEntityModel,
)
from my_agents.observability.metrics import track_retrieval_phase


@dataclass(frozen=True)
class RetrievedChunk:
    """Authorized retrieved context chunk."""

    chunk: DocumentChunkModel
    document: DocumentModel
    score: float
    source: str


@dataclass(frozen=True)
class AuthorizedDocumentOption:
    """Compact display-safe document option inside an authorized retrieval scope."""

    document_id: str
    title: str
    source_filename: str | None
    knowledge_base_id: str | None
    knowledge_base_name: str | None


@dataclass(frozen=True)
class FullDocumentTargetResolution:
    """One authorized full-document target, or an ambiguity/unavailable result."""

    target: AuthorizedDocumentOption | None
    option_count: int


@dataclass(frozen=True)
class FullDocumentReadResult:
    """Bounded authorized extracted text plus chunk provenance for one document."""

    document: DocumentModel
    content: str
    retrieved_chunks: tuple[RetrievedChunk, ...]
    start_offset: int
    end_offset: int
    total_chars: int
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True)
class RetrievedStructuredEntity:
    """Authorized structured fact with chunk/document provenance."""

    entity: StructuredKnowledgeEntityModel
    chunk: DocumentChunkModel
    document: DocumentModel
    score: float
    source: str = "structured_entity"


@dataclass(frozen=True)
class Bm25CorpusRow:
    """Lightweight authorized corpus row used before top-k model hydration."""

    chunk_id: str
    document_id: str
    ordinal: int
    content: str


@dataclass(frozen=True)
class RankedBm25Chunk:
    """BM25 rank result that can hydrate one authorized chunk by ID."""

    chunk_id: str
    score: float


MatchedDocumentChunkRowsCache = dict[str, list[tuple[DocumentChunkModel, DocumentModel]]]
_FULL_DOCUMENT_TARGET_SCAN_LIMIT = 500
_FULL_DOCUMENT_MAX_CITATION_CHUNKS = 100
_FULL_DOCUMENT_MAX_PROVENANCE_SCAN_CHUNKS = 2_000


class RetrievalService:
    """Retrieve only authorized chunks, then expand through authorized entity links."""

    def __init__(self, db: Session, embedding_provider: EmbeddingProvider | None = None) -> None:
        self._db = db
        self._embedding_provider = embedding_provider or get_embedding_provider()

    def retrieve(self, *, user_id: str, query: str, limit: int = 5) -> list[RetrievedChunk]:
        return self.retrieve_scoped(user_id=user_id, query=query, limit=limit)

    def retrieve_scoped(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
        knowledge_base_ids: Sequence[str] | None = None,
        hybrid_search: bool = False,
    ) -> list[RetrievedChunk]:
        terms = _query_terms(query)
        matched_document_chunk_rows_cache: MatchedDocumentChunkRowsCache = {}
        with track_retrieval_phase("document_metadata_match"):
            metadata_matches = self._document_metadata_matches(
                user_id=user_id,
                query=query,
                terms=terms,
                knowledge_base_ids=knowledge_base_ids,
                limit=limit,
                matched_document_chunk_rows_cache=matched_document_chunk_rows_cache,
            )
        query_embedding = self._embedding_provider.embed_query(query)
        with track_retrieval_phase("document_metadata_profile_match"):
            metadata_profile_matches = self._document_metadata_profile_matches(
                user_id=user_id,
                terms=terms,
                query_embedding=query_embedding,
                knowledge_base_ids=knowledge_base_ids,
                limit=limit,
                matched_document_chunk_rows_cache=matched_document_chunk_rows_cache,
            )
        with track_retrieval_phase("direct_authorized_match"):
            direct_matches = self._direct_authorized_matches(
                user_id=user_id,
                query_embedding=query_embedding,
                terms=terms,
                knowledge_base_ids=knowledge_base_ids,
                semantic_only=hybrid_search,
            )
        direct = [
            *metadata_matches,
            *direct_matches,
            *metadata_profile_matches,
        ]
        with track_retrieval_phase("authorized_related_expansion"):
            expanded = self._expand_authorized_related(
                user_id=user_id,
                direct=direct,
                knowledge_base_ids=knowledge_base_ids,
            )
        if hybrid_search:
            ranked_by_source = _rank_retrieval_sources([*direct, *expanded], limit=limit)
            if not ranked_by_source and _needs_personal_document_fallback(query):
                with track_retrieval_phase("personal_document_fallback"):
                    return _dedupe_retrieved_chunks(
                        self._recent_authorized_chunks(
                            user_id=user_id,
                            limit=limit,
                            knowledge_base_ids=knowledge_base_ids,
                        ),
                        limit=limit,
                    )
            if _needs_document_overview(query):
                with track_retrieval_phase("document_overview_supplement"):
                    overview_seed = _dedupe_retrieved_chunks(
                        sorted(
                            ranked_by_source,
                            key=lambda item: (-item.score, item.chunk.ordinal),
                        ),
                        limit=limit,
                    )
                    supplemented = self._with_small_document_overview_chunks(
                        overview_seed,
                        user_id=user_id,
                        knowledge_base_ids=knowledge_base_ids,
                        char_budget=6000,
                        matched_document_chunk_rows_cache=matched_document_chunk_rows_cache,
                    )
                    ranked_by_source.extend(
                        item for item in supplemented if item.source == "document_overview"
                    )
            return ranked_by_source
        combined: dict[str, RetrievedChunk] = {}
        for item in direct:
            existing = combined.get(item.chunk.id)
            if existing is None or _prefer_retrieved_chunk(item, existing):
                combined[item.chunk.id] = item
        for item in expanded:
            existing = combined.get(item.chunk.id)
            if existing is None:
                combined[item.chunk.id] = item
                continue
            if existing.source == "document_metadata_profile":
                combined[item.chunk.id] = RetrievedChunk(
                    chunk=item.chunk,
                    document=item.document,
                    score=max(item.score, existing.score),
                    source=item.source,
                )
        if not combined and _needs_personal_document_fallback(query):
            with track_retrieval_phase("personal_document_fallback"):
                return _dedupe_retrieved_chunks(
                    self._recent_authorized_chunks(
                        user_id=user_id, limit=limit, knowledge_base_ids=knowledge_base_ids
                    ),
                    limit=limit,
                )
        ranked = sorted(combined.values(), key=lambda item: (-item.score, item.chunk.ordinal))
        if _needs_document_overview(query):
            with track_retrieval_phase("document_overview_supplement"):
                ranked = self._with_small_document_overview_chunks(
                    ranked,
                    user_id=user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    char_budget=6000,
                    matched_document_chunk_rows_cache=matched_document_chunk_rows_cache,
                )
        return _dedupe_retrieved_chunks(ranked, limit=limit)

    def retrieve_lexical_scoped(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
        knowledge_base_ids: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Return an independent BM25Okapi ranking over authorized chunk text."""
        if limit <= 0:
            return []
        corpus_rows = self._authorized_bm25_corpus_rows(
            user_id,
            knowledge_base_ids=knowledge_base_ids,
        )
        with track_retrieval_phase("bm25_rank"):
            ranked = _rank_bm25_rows(corpus_rows, query=query, limit=limit)
        if not ranked:
            return []
        hydrated_rows = self._authorized_bm25_top_chunks(
            user_id,
            chunk_ids=[item.chunk_id for item in ranked],
            knowledge_base_ids=knowledge_base_ids,
        )
        rows_by_chunk_id = {chunk.id: (chunk, document) for chunk, document in hydrated_rows}
        return [
            RetrievedChunk(
                chunk=rows_by_chunk_id[item.chunk_id][0],
                document=rows_by_chunk_id[item.chunk_id][1],
                score=item.score,
                source="keyword_match",
            )
            for item in ranked
            if item.chunk_id in rows_by_chunk_id
        ]

    def authorized_document_count(
        self, *, user_id: str, knowledge_base_ids: Sequence[str] | None = None
    ) -> int:
        """Return how many distinct documents the user can read."""
        with track_retrieval_phase("authorized_document_count_sql"):
            return (
                self._db.scalar(
                    select(func.count(DocumentModel.id.distinct())).where(
                        _authorized_document_filter(
                            user_id,
                            knowledge_base_ids=knowledge_base_ids,
                            require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                        )
                    )
                )
                or 0
            )

    def user_selectable_document_count(
        self, *, user_id: str, knowledge_base_ids: Sequence[str] | None = None
    ) -> int:
        """Count personal/group documents that may participate in user clarification."""
        with track_retrieval_phase("user_selectable_document_count_sql"):
            return (
                self._db.scalar(
                    select(func.count(DocumentModel.id.distinct())).where(
                        _user_selectable_document_filter(
                            user_id,
                            knowledge_base_ids=knowledge_base_ids,
                            require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                        )
                    )
                )
                or 0
            )

    def authorized_document_options(
        self,
        *,
        user_id: str,
        knowledge_base_ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuthorizedDocumentOption], int]:
        """Return user-controllable personal/group options for source clarification.

        Ambient system knowledge may support every answer, but it is not a user-visible
        or user-selectable source axis and must never appear in this list.
        """
        predicate = _user_selectable_document_filter(
            user_id,
            knowledge_base_ids=knowledge_base_ids,
            require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
        )
        total = (
            self._db.scalar(select(func.count(DocumentModel.id.distinct())).where(predicate)) or 0
        )
        rows = self._db.execute(
            select(DocumentModel, KnowledgeBaseModel.name)
            .join(KnowledgeBaseModel, KnowledgeBaseModel.id == DocumentModel.knowledge_base_id)
            .where(predicate)
            .order_by(DocumentModel.title, DocumentModel.id)
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 50)))
        ).all()
        return (
            [
                AuthorizedDocumentOption(
                    document_id=document.id,
                    title=document.title,
                    source_filename=document.source_filename,
                    knowledge_base_id=document.knowledge_base_id,
                    knowledge_base_name=knowledge_base_name,
                )
                for document, knowledge_base_name in rows
            ],
            total,
        )

    def resolve_full_document_target(
        self,
        *,
        user_id: str,
        query: str,
        knowledge_base_ids: Sequence[str] | None = None,
        selected_document_id: str | None = None,
    ) -> FullDocumentTargetResolution:
        """Resolve one user-controllable document without loading its body text."""
        predicate = _user_selectable_document_filter(
            user_id,
            knowledge_base_ids=knowledge_base_ids,
            require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
        )
        statement = (
            select(
                DocumentModel.id,
                DocumentModel.title,
                DocumentModel.source_filename,
                DocumentModel.knowledge_base_id,
                KnowledgeBaseModel.name,
            )
            .join(KnowledgeBaseModel, KnowledgeBaseModel.id == DocumentModel.knowledge_base_id)
            .where(predicate)
            .order_by(DocumentModel.title, DocumentModel.id)
        )
        total = (
            self._db.scalar(select(func.count(DocumentModel.id.distinct())).where(predicate)) or 0
        )
        if selected_document_id is not None:
            selected_row = self._db.execute(
                statement.where(DocumentModel.id == selected_document_id).limit(1)
            ).first()
            return FullDocumentTargetResolution(
                target=_authorized_document_option(selected_row) if selected_row else None,
                option_count=total,
            )
        rows = self._db.execute(statement.limit(_FULL_DOCUMENT_TARGET_SCAN_LIMIT + 1)).all()
        options = [
            _authorized_document_option(row) for row in rows[:_FULL_DOCUMENT_TARGET_SCAN_LIMIT]
        ]
        if total == 1 and options:
            return FullDocumentTargetResolution(target=options[0], option_count=1)
        if total > _FULL_DOCUMENT_TARGET_SCAN_LIMIT:
            return FullDocumentTargetResolution(target=None, option_count=total)
        matched = _matching_document_options(options, query=query)
        return FullDocumentTargetResolution(
            target=matched[0] if len(matched) == 1 else None,
            option_count=total,
        )

    def read_full_document_range(
        self,
        *,
        user_id: str,
        document_id: str,
        knowledge_base_ids: Sequence[str] | None = None,
        cursor: str | None = None,
        full_document_max_chars: int = 24_000,
        range_chars: int = 12_000,
    ) -> FullDocumentReadResult | None:
        """Read one bounded extracted-text range after revalidating document access."""
        if full_document_max_chars <= 0 or range_chars <= 0:
            raise ValueError("full-document character limits must be positive")
        if range_chars > full_document_max_chars:
            raise ValueError("full-document range cannot exceed the complete-read limit")
        start_offset = _full_document_cursor_offset(cursor)
        document = self._db.scalar(
            select(DocumentModel)
            .where(
                DocumentModel.id == document_id,
                _user_selectable_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                ),
            )
            .execution_options(populate_existing=True)
        )
        if document is None:
            return None
        total_chars = len(document.content)
        if start_offset > total_chars or (start_offset == total_chars and total_chars > 0):
            raise ValueError("full-document cursor is outside the document")
        if start_offset == 0 and total_chars <= full_document_max_chars:
            end_offset = total_chars
        else:
            end_offset = min(start_offset + range_chars, total_chars)
        chunks = list(
            self._db.scalars(
                select(DocumentChunkModel)
                .options(defer(DocumentChunkModel.embedding_json))
                .where(
                    DocumentChunkModel.document_id == document.id,
                    DocumentChunkModel.start_offset < end_offset,
                    DocumentChunkModel.end_offset > start_offset,
                )
                .order_by(DocumentChunkModel.ordinal)
                .limit(_FULL_DOCUMENT_MAX_PROVENANCE_SCAN_CHUNKS + 1)
            ).all()
        )
        if len(chunks) > _FULL_DOCUMENT_MAX_PROVENANCE_SCAN_CHUNKS or any(
            not _chunk_matches_document_content(chunk, document.content) for chunk in chunks
        ):
            chunks = []
        else:
            chunks = _distributed_chunk_sample(
                chunks,
                limit=_FULL_DOCUMENT_MAX_CITATION_CHUNKS,
            )
        retrieved_chunks = tuple(
            RetrievedChunk(
                chunk=chunk,
                document=document,
                score=1.0,
                source="full_document",
            )
            for chunk in chunks
        )
        return FullDocumentReadResult(
            document=document,
            content=document.content[start_offset:end_offset],
            retrieved_chunks=retrieved_chunks,
            start_offset=start_offset,
            end_offset=end_offset,
            total_chars=total_chars,
            next_cursor=str(end_offset) if end_offset < total_chars else None,
            complete=start_offset == 0 and end_offset == total_chars,
        )

    def retrieve_selected_document(
        self,
        *,
        user_id: str,
        document_id: str,
        query: str,
        knowledge_base_ids: Sequence[str] | None = None,
        limit: int = 12,
    ) -> list[RetrievedChunk]:
        """Rank chunks only from one currently authorized selected document."""
        rows = self._load_authorized_chunks_for_document_ids(
            user_id,
            document_ids=(document_id,),
            knowledge_base_ids=knowledge_base_ids,
        )
        if not rows:
            return []
        terms = _query_terms(query)
        query_embedding = self._embedding_provider.embed_query(query)
        ranked: list[RetrievedChunk] = []
        for chunk, document in rows:
            keyword_rank = _normalized_keyword_score(_keyword_score(chunk.content, terms), terms)
            chunk_embedding = _embedding_from_json(chunk.embedding_json)
            semantic_rank = (
                max(_cosine_similarity(query_embedding, chunk_embedding), 0.0)
                if _compatible_embeddings(query_embedding, chunk_embedding)
                else 0.0
            )
            score = max((0.8 * semantic_rank) + (0.2 * keyword_rank), 0.5)
            ranked.append(
                RetrievedChunk(
                    chunk=chunk,
                    document=document,
                    score=round(score, 6),
                    source="selected_document",
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.chunk.ordinal))
        return ranked[:limit]

    def document_is_user_selectable(
        self,
        *,
        user_id: str,
        document_id: str,
        knowledge_base_ids: Sequence[str] | None = None,
    ) -> bool:
        """Revalidate one user-controllable document against the current scope."""
        return bool(
            self._db.scalar(
                select(DocumentModel.id).where(
                    DocumentModel.id == document_id,
                    _user_selectable_document_filter(
                        user_id,
                        knowledge_base_ids=knowledge_base_ids,
                        require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                    ),
                )
            )
        )

    def retrieve_structured_entities(
        self,
        *,
        user_id: str,
        query: str,
        entity_types: Sequence[str],
        limit: int = 50,
        knowledge_base_ids: Sequence[str] | None = None,
    ) -> list[RetrievedStructuredEntity]:
        """Return authorized structured facts for intent-aware enumeration retrieval."""
        unique_entity_types = tuple(dict.fromkeys(entity_types))
        if not unique_entity_types:
            return []
        statement = (
            select(StructuredKnowledgeEntityModel, DocumentChunkModel, DocumentModel)
            .options(
                defer(DocumentChunkModel.embedding_json),
                defer(DocumentModel.content),
            )
            .join(
                DocumentChunkModel,
                StructuredKnowledgeEntityModel.chunk_id == DocumentChunkModel.id,
            )
            .join(DocumentModel, StructuredKnowledgeEntityModel.document_id == DocumentModel.id)
            .where(
                _authorized_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                ),
                StructuredKnowledgeEntityModel.entity_type.in_(unique_entity_types),
            )
            .order_by(
                DocumentModel.created_at.desc(),
                DocumentChunkModel.ordinal,
                StructuredKnowledgeEntityModel.start_offset,
            )
            .limit(limit)
        )
        terms = _query_terms(query)
        with track_retrieval_phase("structured_entity_sql"):
            rows = self._db.execute(statement).all()
        matches = [
            RetrievedStructuredEntity(
                entity=entity,
                chunk=chunk,
                document=document,
                score=_structured_entity_score(entity, terms),
            )
            for entity, chunk, document in rows
        ]
        return sorted(matches, key=lambda item: (-item.score, item.chunk.ordinal))

    def _direct_authorized_matches(
        self,
        *,
        user_id: str,
        query_embedding: list[float],
        terms: set[str],
        knowledge_base_ids: Sequence[str] | None,
        semantic_only: bool,
    ) -> list[RetrievedChunk]:
        if _uses_postgres(self._db):
            sql_matches = self._postgres_vector_authorized_matches(
                user_id=user_id,
                query_embedding=query_embedding,
                terms=terms,
                knowledge_base_ids=knowledge_base_ids,
                limit=20,
                semantic_only=semantic_only,
            )
            if sql_matches:
                return sql_matches
        return self._json_authorized_matches(
            user_id=user_id,
            query_embedding=query_embedding,
            terms=terms,
            knowledge_base_ids=knowledge_base_ids,
            semantic_only=semantic_only,
        )

    def _json_authorized_matches(
        self,
        *,
        user_id: str,
        query_embedding: list[float],
        terms: set[str],
        knowledge_base_ids: Sequence[str] | None,
        semantic_only: bool,
    ) -> list[RetrievedChunk]:
        rows = self._authorized_chunk_rows(user_id, knowledge_base_ids=knowledge_base_ids)
        if not rows:
            return []
        matches: list[RetrievedChunk] = []
        for chunk, document in rows:
            keyword_score = _keyword_score(chunk.content, terms)
            keyword_rank = _normalized_keyword_score(keyword_score, terms)
            chunk_embedding = _embedding_from_json(chunk.embedding_json)
            if _compatible_embeddings(query_embedding, chunk_embedding):
                cosine = _cosine_similarity(query_embedding, chunk_embedding)
                positive_cosine = max(cosine, 0.0)
                combined_score = (
                    positive_cosine
                    if semantic_only
                    else (0.75 * positive_cosine) + (0.25 * keyword_rank)
                )
                if combined_score > 0:
                    matches.append(
                        RetrievedChunk(
                            chunk=chunk,
                            document=document,
                            score=round(combined_score, 6),
                            source="semantic_vector",
                        )
                    )
                continue
            if keyword_score > 0 and not semantic_only:
                matches.append(
                    RetrievedChunk(
                        chunk=chunk,
                        document=document,
                        score=round(keyword_rank, 6),
                        source="keyword_match",
                    )
                )
        # Future seam: a cross-encoder reranker should run only here, after
        # `_authorized_chunk_rows(...)` has already enforced permission filtering and
        # after vector/keyword scoring has narrowed candidates to a small top-k set.
        return sorted(matches, key=lambda item: (-item.score, item.chunk.ordinal))

    def _postgres_vector_authorized_matches(
        self,
        *,
        user_id: str,
        query_embedding: list[float],
        terms: set[str],
        knowledge_base_ids: Sequence[str] | None,
        limit: int,
        semantic_only: bool,
    ) -> list[RetrievedChunk]:
        if not query_embedding:
            return []
        try:
            with track_retrieval_phase("postgres_vector_sql"):
                rows = self._db.execute(
                    _postgres_vector_authorized_statement(
                        user_id=user_id,
                        query_embedding=query_embedding,
                        knowledge_base_ids=knowledge_base_ids,
                        limit=limit,
                    )
                ).all()
        except SQLAlchemyError:
            self._db.rollback()
            return []

        matches: list[RetrievedChunk] = []
        for chunk, document, vector_distance in rows:
            if vector_distance is None:
                continue
            cosine = 1 - float(vector_distance)
            keyword_score = _keyword_score(chunk.content, terms)
            keyword_rank = _normalized_keyword_score(keyword_score, terms)
            positive_cosine = max(cosine, 0.0)
            combined_score = (
                positive_cosine
                if semantic_only
                else (0.75 * positive_cosine) + (0.25 * keyword_rank)
            )
            if combined_score > 0:
                matches.append(
                    RetrievedChunk(
                        chunk=chunk,
                        document=document,
                        score=round(combined_score, 6),
                        source="semantic_vector",
                    )
                )
        # Future seam: a cross-encoder reranker should run only here, after
        # `_postgres_vector_authorized_statement(...)` has already enforced
        # permission filtering in SQL and vector search has narrowed candidates
        # to a small authorized top-k set.
        return sorted(matches, key=lambda item: (-item.score, item.chunk.ordinal))

    def _expand_authorized_related(
        self,
        *,
        user_id: str,
        direct: list[RetrievedChunk],
        knowledge_base_ids: Sequence[str] | None,
    ) -> list[RetrievedChunk]:
        expansion_seeds = [item for item in direct if item.source != "document_metadata_profile"]
        if not expansion_seeds:
            return []
        seed_chunk_ids = [item.chunk.id for item in expansion_seeds]
        entity_ids = {
            mention.entity_id for mention in _entity_mentions_for_chunks(self._db, seed_chunk_ids)
        }
        if not entity_ids:
            return []
        statement = (
            select(DocumentChunkModel, DocumentModel)
            .options(
                defer(DocumentChunkModel.embedding_json),
                defer(DocumentModel.content),
            )
            .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
            .join(EntityMentionModel, EntityMentionModel.chunk_id == DocumentChunkModel.id)
            .where(
                _authorized_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    include_published_personal_kbs=_schema_has_knowledge_base_publications(
                        self._db
                    ),
                    require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                ),
                EntityMentionModel.entity_id.in_(entity_ids),
                ~DocumentChunkModel.id.in_(seed_chunk_ids),
            )
            .order_by(desc(DocumentModel.created_at), DocumentChunkModel.ordinal)
        )
        with track_retrieval_phase("related_entity_chunks_sql"):
            authorized_rows = self._db.execute(statement).all()
        expanded: list[RetrievedChunk] = []
        expanded_chunk_ids: set[str] = set()
        for chunk, document in authorized_rows:
            if chunk.id in expanded_chunk_ids:
                continue
            expanded_chunk_ids.add(chunk.id)
            expanded.append(
                RetrievedChunk(
                    chunk=chunk,
                    document=document,
                    score=0.1,
                    source="graph_expansion",
                )
            )
        return expanded

    def _recent_authorized_chunks(
        self, *, user_id: str, limit: int, knowledge_base_ids: Sequence[str] | None
    ) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk=chunk,
                document=document,
                score=0.1,
                source="document_fallback",
            )
            for chunk, document in self._authorized_chunk_rows(
                user_id, knowledge_base_ids=knowledge_base_ids
            )[:limit]
        ]

    def _document_metadata_matches(
        self,
        *,
        user_id: str,
        query: str,
        terms: set[str],
        knowledge_base_ids: Sequence[str] | None,
        limit: int,
        matched_document_chunk_rows_cache: MatchedDocumentChunkRowsCache,
    ) -> list[RetrievedChunk]:
        """Return chunks from authorized documents whose title/filename matches the query."""
        signal_terms = _metadata_signal_terms(terms)
        if not signal_terms:
            return []
        documents: dict[str, tuple[DocumentModel, float]] = {}
        for document in self._authorized_document_rows(
            user_id, knowledge_base_ids=knowledge_base_ids
        ):
            if document.id in documents:
                continue
            score = _document_metadata_score(
                query=query,
                signal_terms=signal_terms,
                title=document.title,
                source_filename=document.source_filename,
            )
            if score > 0:
                documents[document.id] = (document, score)
        if not documents:
            return []
        rows = [
            (chunk, document, documents[document.id][1])
            for chunk, document in self._authorized_chunks_for_document_ids(
                user_id,
                document_ids=documents.keys(),
                knowledge_base_ids=knowledge_base_ids,
                matched_document_chunk_rows_cache=matched_document_chunk_rows_cache,
            )
        ]
        rows.sort(key=lambda row: (-row[2], -row[1].created_at.timestamp(), row[0].ordinal))
        return [
            RetrievedChunk(
                chunk=chunk,
                document=document,
                score=round(max(score - (chunk.ordinal * 0.001), 0.01), 6),
                source="document_metadata",
            )
            for chunk, document, score in rows[:limit]
        ]

    def _with_small_document_overview_chunks(
        self,
        ranked: list[RetrievedChunk],
        *,
        user_id: str,
        knowledge_base_ids: Sequence[str] | None,
        char_budget: int,
        matched_document_chunk_rows_cache: MatchedDocumentChunkRowsCache,
    ) -> list[RetrievedChunk]:
        """Supplement summary-style queries with broader coverage for small matched docs."""
        if not ranked:
            return ranked
        matched_document_ids = {item.document.id for item in ranked}
        existing_chunk_ids = {item.chunk.id for item in ranked}
        used_chars = sum(len(item.chunk.content) for item in ranked)
        supplemented = list(ranked)
        for chunk, document in self._authorized_chunks_for_document_ids(
            user_id,
            document_ids=matched_document_ids,
            knowledge_base_ids=knowledge_base_ids,
            matched_document_chunk_rows_cache=matched_document_chunk_rows_cache,
        ):
            if chunk.id in existing_chunk_ids:
                continue
            next_size = len(chunk.content)
            if used_chars + next_size > char_budget and supplemented:
                continue
            supplemented.append(
                RetrievedChunk(
                    chunk=chunk,
                    document=document,
                    score=0.05,
                    source="document_overview",
                )
            )
            existing_chunk_ids.add(chunk.id)
            used_chars += next_size
        return supplemented

    def _document_metadata_profile_matches(
        self,
        *,
        user_id: str,
        terms: set[str],
        query_embedding: list[float],
        knowledge_base_ids: Sequence[str] | None,
        limit: int,
        matched_document_chunk_rows_cache: MatchedDocumentChunkRowsCache,
    ) -> list[RetrievedChunk]:
        """Return source chunks from documents matched by generated metadata."""
        if not _schema_has_document_metadata_profiles(self._db):
            return []
        signal_terms = _metadata_signal_terms(terms)
        if not signal_terms:
            return []
        if not query_embedding:
            return []
        document_scores: dict[str, tuple[DocumentModel, float]] = {}
        for profile, document in self._authorized_metadata_profile_rows(
            user_id, knowledge_base_ids=knowledge_base_ids
        ):
            profile_embedding = _embedding_from_json(profile.embedding_json)
            keyword_rank = _normalized_keyword_score(
                _keyword_score(profile.search_text, signal_terms), signal_terms
            )
            if not _compatible_embeddings(query_embedding, profile_embedding):
                if keyword_rank <= 0:
                    continue
                score = 0.55 * keyword_rank
            else:
                cosine = max(_cosine_similarity(query_embedding, profile_embedding), 0.0)
                score = (0.8 * cosine) + (0.2 * keyword_rank)
            if score <= 0.05:
                continue
            existing = document_scores.get(document.id)
            rounded = round(score, 6)
            if existing is None or rounded > existing[1]:
                document_scores[document.id] = (document, rounded)
        if not document_scores:
            return []
        rows_by_document: dict[str, list[DocumentChunkModel]] = {}
        for chunk, document in self._authorized_chunks_for_document_ids(
            user_id,
            document_ids=document_scores.keys(),
            knowledge_base_ids=knowledge_base_ids,
            matched_document_chunk_rows_cache=matched_document_chunk_rows_cache,
        ):
            rows_by_document.setdefault(document.id, []).append(chunk)
        matches: list[RetrievedChunk] = []
        ranked_documents = sorted(
            document_scores.values(),
            key=lambda item: (-item[1], -item[0].created_at.timestamp(), item[0].id),
        )
        for document, score in ranked_documents:
            matches.extend(
                _metadata_profile_document_chunks(
                    document=document,
                    chunks=rows_by_document.get(document.id, []),
                    score=score,
                    signal_terms=signal_terms,
                )
            )
            if len(matches) >= limit:
                break
        return sorted(matches, key=lambda item: (-item.score, item.chunk.ordinal))[:limit]

    def _authorized_metadata_profile_rows(
        self, user_id: str, *, knowledge_base_ids: Sequence[str] | None
    ) -> list[tuple[DocumentMetadataProfileModel, DocumentModel]]:
        statement = (
            select(DocumentMetadataProfileModel, DocumentModel)
            .options(defer(DocumentModel.content))
            .join(DocumentModel, DocumentMetadataProfileModel.document_id == DocumentModel.id)
            .where(
                _authorized_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    include_published_personal_kbs=_schema_has_knowledge_base_publications(
                        self._db
                    ),
                    require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                )
            )
            .order_by(desc(DocumentModel.created_at), desc(DocumentMetadataProfileModel.created_at))
        )
        with track_retrieval_phase("metadata_profile_rows_sql"):
            return list(self._db.execute(statement).all())

    def _authorized_chunk_rows(
        self, user_id: str, *, knowledge_base_ids: Sequence[str] | None
    ) -> list[tuple[DocumentChunkModel, DocumentModel]]:
        include_published_personal_kbs = _schema_has_knowledge_base_publications(self._db)
        statement = (
            select(DocumentChunkModel, DocumentModel)
            .options(defer(DocumentModel.content))
            .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
            .where(
                _authorized_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    include_published_personal_kbs=include_published_personal_kbs,
                    require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                )
            )
            .order_by(desc(DocumentModel.created_at), DocumentChunkModel.ordinal)
        )
        with track_retrieval_phase("authorized_chunk_rows_sql"):
            return list(self._db.execute(statement).all())

    def _authorized_bm25_corpus_rows(
        self,
        user_id: str,
        *,
        knowledge_base_ids: Sequence[str] | None,
    ) -> list[Bm25CorpusRow]:
        statement = _authorized_bm25_corpus_statement(
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
            include_published_personal_kbs=_schema_has_knowledge_base_publications(self._db),
            require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
        )
        with track_retrieval_phase("authorized_bm25_corpus_rows_sql"):
            return [
                Bm25CorpusRow(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    ordinal=ordinal,
                    content=content,
                )
                for chunk_id, document_id, ordinal, content in self._db.execute(statement).all()
            ]

    def _authorized_bm25_top_chunks(
        self,
        user_id: str,
        *,
        chunk_ids: Sequence[str],
        knowledge_base_ids: Sequence[str] | None,
    ) -> list[tuple[DocumentChunkModel, DocumentModel]]:
        if not chunk_ids:
            return []
        statement = _authorized_bm25_top_chunks_statement(
            user_id=user_id,
            chunk_ids=chunk_ids,
            knowledge_base_ids=knowledge_base_ids,
            include_published_personal_kbs=_schema_has_knowledge_base_publications(self._db),
            require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
        )
        with track_retrieval_phase("authorized_bm25_top_chunks_sql"):
            return list(self._db.execute(statement).all())

    def _authorized_document_rows(
        self, user_id: str, *, knowledge_base_ids: Sequence[str] | None
    ) -> list[DocumentModel]:
        include_published_personal_kbs = _schema_has_knowledge_base_publications(self._db)
        statement = (
            select(DocumentModel)
            .options(defer(DocumentModel.content))
            .where(
                _authorized_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    include_published_personal_kbs=include_published_personal_kbs,
                    require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                )
            )
            .order_by(desc(DocumentModel.created_at), DocumentModel.id)
        )
        with track_retrieval_phase("authorized_document_rows_sql"):
            return list(self._db.scalars(statement).all())

    def _authorized_chunks_for_document_ids(
        self,
        user_id: str,
        *,
        document_ids: Iterable[str],
        knowledge_base_ids: Sequence[str] | None,
        matched_document_chunk_rows_cache: MatchedDocumentChunkRowsCache | None = None,
    ) -> list[tuple[DocumentChunkModel, DocumentModel]]:
        unique_document_ids = tuple(dict.fromkeys(document_ids))
        if not unique_document_ids:
            return []
        if matched_document_chunk_rows_cache is not None:
            missing_document_ids = tuple(
                document_id
                for document_id in unique_document_ids
                if document_id not in matched_document_chunk_rows_cache
            )
            if missing_document_ids:
                for document_id in missing_document_ids:
                    matched_document_chunk_rows_cache[document_id] = []
                for chunk, document in self._load_authorized_chunks_for_document_ids(
                    user_id,
                    document_ids=missing_document_ids,
                    knowledge_base_ids=knowledge_base_ids,
                ):
                    matched_document_chunk_rows_cache[document.id].append((chunk, document))
            return _sort_authorized_chunk_rows(
                row
                for document_id in unique_document_ids
                for row in matched_document_chunk_rows_cache.get(document_id, ())
            )

        return self._load_authorized_chunks_for_document_ids(
            user_id,
            document_ids=unique_document_ids,
            knowledge_base_ids=knowledge_base_ids,
        )

    def _load_authorized_chunks_for_document_ids(
        self,
        user_id: str,
        *,
        document_ids: Iterable[str],
        knowledge_base_ids: Sequence[str] | None,
    ) -> list[tuple[DocumentChunkModel, DocumentModel]]:
        unique_document_ids = tuple(dict.fromkeys(document_ids))
        if not unique_document_ids:
            return []
        statement = (
            select(DocumentChunkModel, DocumentModel)
            .options(
                defer(DocumentChunkModel.embedding_json),
                defer(DocumentModel.content),
            )
            .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
            .where(
                DocumentModel.id.in_(unique_document_ids),
                _authorized_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    include_published_personal_kbs=_schema_has_knowledge_base_publications(
                        self._db
                    ),
                    require_standard_purpose=_schema_has_knowledge_base_purpose(self._db),
                ),
            )
            .order_by(desc(DocumentModel.created_at), DocumentChunkModel.ordinal)
        )
        with track_retrieval_phase("authorized_matched_chunk_rows_sql"):
            return list(self._db.execute(statement).all())


def _authorized_bm25_corpus_statement(
    *,
    user_id: str,
    knowledge_base_ids: Sequence[str] | None = None,
    include_published_personal_kbs: bool = True,
    require_standard_purpose: bool = True,
):
    return (
        select(
            DocumentChunkModel.id,
            DocumentChunkModel.document_id,
            DocumentChunkModel.ordinal,
            DocumentChunkModel.content,
        )
        .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
        .where(
            _authorized_document_filter(
                user_id,
                knowledge_base_ids=knowledge_base_ids,
                include_published_personal_kbs=include_published_personal_kbs,
                require_standard_purpose=require_standard_purpose,
            )
        )
    )


def _authorized_bm25_top_chunks_statement(
    *,
    user_id: str,
    chunk_ids: Sequence[str],
    knowledge_base_ids: Sequence[str] | None = None,
    include_published_personal_kbs: bool = True,
    require_standard_purpose: bool = True,
):
    return (
        select(DocumentChunkModel, DocumentModel)
        .options(
            defer(DocumentChunkModel.embedding_json),
            defer(DocumentModel.content),
        )
        .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
        .where(
            DocumentChunkModel.id.in_(tuple(dict.fromkeys(chunk_ids))),
            _authorized_document_filter(
                user_id,
                knowledge_base_ids=knowledge_base_ids,
                include_published_personal_kbs=include_published_personal_kbs,
                require_standard_purpose=require_standard_purpose,
            ),
        )
    )


def _postgres_vector_authorized_statement(
    *,
    user_id: str,
    query_embedding: list[float],
    limit: int,
    knowledge_base_ids: Sequence[str] | None = None,
):
    embedding_vector = _embedding_vector_column()
    vector_distance = embedding_vector.cosine_distance(query_embedding).label("vector_distance")
    return (
        select(DocumentChunkModel, DocumentModel, vector_distance)
        .options(
            defer(DocumentChunkModel.embedding_json),
            defer(DocumentModel.content),
        )
        .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
        .where(
            _authorized_document_filter(user_id),
            _knowledge_base_scope_filter(user_id, knowledge_base_ids),
            embedding_vector.is_not(None),
        )
        .order_by(vector_distance, desc(DocumentModel.created_at), DocumentChunkModel.ordinal)
        .limit(limit)
    )


def _entity_mentions_for_chunks(db: Session, chunk_ids: Iterable[str]) -> list[EntityMentionModel]:
    unique_chunk_ids = tuple(dict.fromkeys(chunk_ids))
    if not unique_chunk_ids:
        return []
    with track_retrieval_phase("entity_mentions_sql"):
        return list(
            db.scalars(
                select(EntityMentionModel).where(EntityMentionModel.chunk_id.in_(unique_chunk_ids))
            ).all()
        )


def _sort_authorized_chunk_rows(
    rows: Iterable[tuple[DocumentChunkModel, DocumentModel]],
) -> list[tuple[DocumentChunkModel, DocumentModel]]:
    """Match the SQL ordering used for authorized chunk rows."""
    return sorted(
        rows,
        key=lambda row: (
            -row[1].created_at.timestamp(),
            row[0].ordinal,
        ),
    )


def _authorized_document_filter(
    user_id: str,
    *,
    knowledge_base_ids: Sequence[str] | None = None,
    include_published_personal_kbs: bool = True,
    require_standard_purpose: bool = True,
):
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    explicit_doc_ids = select(DocumentPermissionModel.document_id).where(
        DocumentPermissionModel.user_id == user_id,
        DocumentPermissionModel.can_read.is_(True),
    )
    knowledge_base_scope = _knowledge_base_scope_filter(
        user_id,
        knowledge_base_ids,
        include_published_personal_kbs=include_published_personal_kbs,
        require_standard_purpose=require_standard_purpose,
    )
    knowledge_base_scoped_predicates = [
        and_(DocumentModel.group_id.is_(None), DocumentModel.owner_user_id == user_id),
        DocumentModel.group_id.in_(group_ids),
        DocumentModel.knowledge_base_id.in_(
            _system_knowledge_base_ids(require_standard_purpose=require_standard_purpose)
        ),
    ]
    explicit_scope_predicates = [
        (
            DocumentModel.knowledge_base_id.in_(tuple(dict.fromkeys(knowledge_base_ids)))
            if knowledge_base_ids is not None
            else true()
        )
    ]
    if require_standard_purpose:
        explicit_scope_predicates.append(
            DocumentModel.knowledge_base_id.in_(
                select(KnowledgeBaseModel.id).where(
                    KnowledgeBaseModel.purpose == KnowledgeBasePurpose.STANDARD.value
                )
            )
        )
    return or_(
        and_(knowledge_base_scope, or_(*knowledge_base_scoped_predicates)),
        and_(DocumentModel.id.in_(explicit_doc_ids), *explicit_scope_predicates),
    )


def _user_selectable_document_filter(
    user_id: str,
    *,
    knowledge_base_ids: Sequence[str] | None = None,
    require_standard_purpose: bool = True,
):
    """Exclude ambient system knowledge from every user-controlled source choice."""
    return and_(
        _authorized_document_filter(
            user_id,
            knowledge_base_ids=knowledge_base_ids,
            require_standard_purpose=require_standard_purpose,
        ),
        ~DocumentModel.knowledge_base_id.in_(
            _system_knowledge_base_ids(require_standard_purpose=require_standard_purpose)
        ),
    )


def _knowledge_base_scope_filter(
    user_id: str,
    knowledge_base_ids: Sequence[str] | None = None,
    *,
    include_published_personal_kbs: bool = True,
    require_standard_purpose: bool = True,
):
    if include_published_personal_kbs:
        if require_standard_purpose:
            authorized_filter = retrievable_knowledge_base_filter(user_id)
        else:
            from my_agents.knowledge.auth import authorized_knowledge_base_filter

            authorized_filter = authorized_knowledge_base_filter(user_id)
    else:
        group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
        authorized_filter = or_(
            and_(
                KnowledgeBaseModel.scope == "personal",
                KnowledgeBaseModel.group_id.is_(None),
                KnowledgeBaseModel.owner_user_id == user_id,
                *([KnowledgeBaseModel.purpose == "standard"] if require_standard_purpose else []),
            ),
            and_(
                KnowledgeBaseModel.scope == "group",
                KnowledgeBaseModel.group_id.in_(group_ids),
                *([KnowledgeBaseModel.purpose == "standard"] if require_standard_purpose else []),
            ),
            and_(
                KnowledgeBaseModel.scope == KnowledgeBaseScope.SYSTEM.value,
                KnowledgeBaseModel.group_id.is_(None),
                *(
                    [KnowledgeBaseModel.purpose == KnowledgeBasePurpose.STANDARD.value]
                    if require_standard_purpose
                    else []
                ),
            ),
        )
    authorized_kb_ids = select(KnowledgeBaseModel.id).where(authorized_filter)
    if knowledge_base_ids is None:
        return DocumentModel.knowledge_base_id.in_(authorized_kb_ids)
    unique_ids = tuple(dict.fromkeys(knowledge_base_ids))
    if not unique_ids:
        return false()
    return and_(
        DocumentModel.knowledge_base_id.in_(unique_ids),
        DocumentModel.knowledge_base_id.in_(authorized_kb_ids),
    )


def _system_knowledge_base_ids(*, require_standard_purpose: bool):
    predicates = [
        KnowledgeBaseModel.scope == KnowledgeBaseScope.SYSTEM.value,
        KnowledgeBaseModel.group_id.is_(None),
    ]
    if require_standard_purpose:
        predicates.append(KnowledgeBaseModel.purpose == KnowledgeBasePurpose.STANDARD.value)
    return select(KnowledgeBaseModel.id).where(and_(*predicates))


def _schema_has_knowledge_base_publications(db: Session) -> bool:
    """Return whether legacy publication rows should grant retrieval.

    The table can remain present while backfill deletes old rows, but after the
    group-owned-copy cutover it must not authorize personal source KB access.
    """
    return False


def _schema_has_knowledge_base_purpose(db: Session) -> bool:
    if not inspect(db.get_bind()).has_table("knowledge_bases"):
        return False
    return "purpose" in {
        column["name"] for column in inspect(db.get_bind()).get_columns("knowledge_bases")
    }


def _schema_has_document_metadata_profiles(db: Session) -> bool:
    return inspect(db.get_bind()).has_table("document_metadata_profiles")


def _uses_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind.dialect.name == "postgresql"


def _embedding_vector_column():
    return DocumentChunkModel.__table__.c.embedding_vector


_PERSONAL_DOCUMENT_FALLBACK_HINTS = (
    "about me",
    "my resume",
    "my cv",
    "my profile",
    "my background",
    "my experience",
    "my document",
    "uploaded document",
    "uploaded file",
    "resume",
    "cv",
    "product",
    "나에 대해",
    "내 이력서",
    "이력서",
    "내 문서",
    "업로드한 문서",
    "업로드 해놓은",
    "문서 업로드",
    "자기소개",
    "경력",
)


_DOCUMENT_METADATA_STOPWORDS = {
    "about",
    "according",
    "based",
    "does",
    "document",
    "file",
    "from",
    "give",
    "me",
    "my",
    "please",
    "say",
    "tell",
    "that",
    "the",
    "this",
    "uploaded",
    "what",
    "which",
    "with",
    "그럼",
    "기준으로",
    "대해",
    "대해서",
    "문서",
    "보여줘",
    "설명",
    "설명해줘",
    "알려줘",
    "자료",
    "파일",
    "해당",
}
_METADATA_PROFILE_CHUNKS_PER_DOCUMENT = 4


def _metadata_signal_terms(terms: set[str]) -> set[str]:
    return {term for term in terms if term not in _DOCUMENT_METADATA_STOPWORDS}


def _document_metadata_score(
    *,
    query: str,
    signal_terms: set[str],
    title: str,
    source_filename: str | None,
) -> float:
    metadata_values = [title]
    if source_filename:
        metadata_values.append(source_filename)
        metadata_values.append(source_filename.rsplit(".", 1)[0])
    metadata_terms = {
        term for value in metadata_values for term in _metadata_terms(value) if len(term) > 1
    }
    if not metadata_terms:
        return 0.0
    normalized_query = _normalize_metadata_text(query)
    normalized_metadata_values = [
        normalized for value in metadata_values if (normalized := _normalize_metadata_text(value))
    ]
    if any(value and value in normalized_query for value in normalized_metadata_values):
        return 1.2
    overlap = signal_terms & metadata_terms
    overlap_score = len(overlap) / max(len(signal_terms), 1)
    compact_query = "".join(
        term for term in _metadata_terms(query) if term not in _DOCUMENT_METADATA_STOPWORDS
    )
    fuzzy_score = max(
        (
            SequenceMatcher(None, compact_query, "".join(_metadata_terms(value))).ratio()
            for value in metadata_values
            if value and any(char.isdigit() for char in value)
        ),
        default=0.0,
    )
    if fuzzy_score >= 0.72:
        return round(min(1.0, 0.75 + (0.25 * fuzzy_score)), 6)
    if len(overlap) < 2:
        return 0.0
    return round(min(0.85, 0.55 + (0.3 * overlap_score)), 6)


def _metadata_terms(value: str) -> list[str]:
    return [term.casefold() for term in re.findall(r"[A-Za-z0-9가-힣]+", value)]


def _normalize_metadata_text(value: str) -> str:
    return " ".join(_metadata_terms(value))


def _query_terms(query: str) -> set[str]:
    return {term.casefold() for term in re.findall(r"[A-Za-z0-9가-힣]+", query) if len(term) > 1}


def _bm25_tokens(value: str) -> list[str]:
    return [term.casefold() for term in re.findall(r"[A-Za-z0-9가-힣]+", value) if len(term) > 1]


def _needs_personal_document_fallback(query: str) -> bool:
    normalized = query.casefold()
    return any(hint in normalized for hint in _PERSONAL_DOCUMENT_FALLBACK_HINTS)


_DOCUMENT_OVERVIEW_HINTS = (
    "explain",
    "summarize",
    "summary",
    "overview",
    "what is this document",
    "what does this document",
    "what does my uploaded document",
    "tell me about my uploaded document",
    "문서 요약",
    "설명",
    "요약",
    "어떤 문서",
    "업로드한 문서",
)


def _needs_document_overview(query: str) -> bool:
    normalized = query.casefold()
    return any(hint in normalized for hint in _DOCUMENT_OVERVIEW_HINTS)


def _metadata_profile_document_chunks(
    *,
    document: DocumentModel,
    chunks: Sequence[DocumentChunkModel],
    score: float,
    signal_terms: set[str],
) -> list[RetrievedChunk]:
    """Return answer-bearing body chunks from a document-level metadata match.

    Metadata profiles are document locators. They should earn the source document
    consideration, but the LLM should still receive source-text chunks rather than
    only the profile/title/header that matched the query.
    """
    if not chunks:
        return []
    scored_chunks = [
        (chunk, _metadata_profile_chunk_body_score(chunk.content, signal_terms)) for chunk in chunks
    ]
    ranked_chunks = sorted(scored_chunks, key=lambda item: (-item[1], item[0].ordinal))[
        :_METADATA_PROFILE_CHUNKS_PER_DOCUMENT
    ]
    return [
        RetrievedChunk(
            chunk=chunk,
            document=document,
            score=round(
                max(
                    score + min(chunk_score, 0.35) - (chunk.ordinal * 0.001),
                    0.01,
                ),
                6,
            ),
            source="document_metadata_profile",
        )
        for chunk, chunk_score in ranked_chunks
    ]


def _metadata_profile_chunk_body_score(content: str, signal_terms: set[str]) -> float:
    keyword_rank = _normalized_keyword_score(_keyword_score(content, signal_terms), signal_terms)
    non_heading_text = _non_heading_text(content)
    body_bonus = min(len(non_heading_text) / 600, 0.25)
    heading_penalty = 0.25 if _is_heading_only(content) else 0.0
    return max((0.75 * keyword_rank) + body_bonus - heading_penalty, 0.0)


def _non_heading_text(content: str) -> str:
    lines = [line.strip() for line in content.splitlines()]
    return "\n".join(line for line in lines if line and not line.startswith("#")).strip()


def _is_heading_only(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return bool(lines) and all(line.startswith("#") for line in lines)


def _dedupe_retrieved_chunks(
    chunks: Sequence[RetrievedChunk], *, limit: int
) -> list[RetrievedChunk]:
    deduped: list[RetrievedChunk] = []
    seen_chunk_ids: set[str] = set()
    seen_ordinals: set[tuple[str, int]] = set()
    seen_content: set[tuple[str, str]] = set()
    for item in chunks:
        ordinal_key = (item.document.id, item.chunk.ordinal)
        content_key = (item.document.id, _normalize_chunk_content(item.chunk.content))
        if (
            item.chunk.id in seen_chunk_ids
            or ordinal_key in seen_ordinals
            or content_key in seen_content
        ):
            continue
        deduped.append(item)
        seen_chunk_ids.add(item.chunk.id)
        seen_ordinals.add(ordinal_key)
        seen_content.add(content_key)
        if len(deduped) >= limit:
            break
    return deduped


def _rank_retrieval_sources(
    chunks: Sequence[RetrievedChunk],
    *,
    limit: int,
) -> list[RetrievedChunk]:
    """Keep bounded independent rankings so downstream RRF can observe overlap."""
    by_source: dict[str, list[RetrievedChunk]] = {}
    for item in chunks:
        by_source.setdefault(item.source, []).append(item)
    ranked: list[RetrievedChunk] = []
    for source_chunks in by_source.values():
        ranked.extend(
            _dedupe_retrieved_chunks(
                sorted(
                    source_chunks,
                    key=lambda item: (-item.score, item.chunk.ordinal, item.chunk.id),
                ),
                limit=limit,
            )
        )
    return ranked


def _rank_bm25_rows(
    rows: Sequence[Bm25CorpusRow],
    *,
    query: str,
    limit: int,
) -> list[RankedBm25Chunk]:
    """Build a request-local BM25Okapi corpus and rank matched authorized chunks."""
    if limit <= 0 or not rows:
        return []
    query_tokens = _bm25_tokens(query)
    if not query_tokens:
        return []
    tokenized_corpus = [_bm25_tokens(row.content) for row in rows]
    if not any(tokenized_corpus):
        return []
    ranker = BM25Okapi(tokenized_corpus)
    raw_scores = ranker.get_scores(query_tokens)
    query_token_set = set(query_tokens)
    matched = [
        (float(raw_score), row)
        for row, chunk_tokens, raw_score in zip(
            rows,
            tokenized_corpus,
            raw_scores,
            strict=True,
        )
        if query_token_set.intersection(chunk_tokens)
    ]
    if not matched:
        return []
    minimum_score = min(score for score, _row in matched)
    positive_offset = (1e-9 - minimum_score) if minimum_score <= 0 else 0.0
    ranked = sorted(
        matched,
        key=lambda item: (-item[0], item[1].ordinal, item[1].chunk_id),
    )[:limit]
    return [
        RankedBm25Chunk(
            chunk_id=row.chunk_id,
            score=max(score + positive_offset, 1e-9),
        )
        for score, row in ranked
    ]


def _prefer_retrieved_chunk(candidate: RetrievedChunk, existing: RetrievedChunk) -> bool:
    candidate_priority = _retrieval_source_priority(candidate.source)
    existing_priority = _retrieval_source_priority(existing.source)
    if candidate_priority != existing_priority:
        return candidate_priority > existing_priority
    return candidate.score > existing.score


def _retrieval_source_priority(source: str) -> int:
    if source == "document_metadata":
        return 40
    if source.startswith("structured_entity:"):
        return 30
    if source == "semantic_vector":
        return 20
    if source == "document_metadata_profile":
        return 15
    return 10


def _normalize_chunk_content(content: str) -> str:
    return " ".join(content.casefold().split())


def _keyword_score(content: str, terms: set[str]) -> int:
    lowered = content.casefold()
    return sum(1 for term in terms if term in lowered)


def _normalized_keyword_score(score: int, terms: set[str]) -> float:
    if not terms:
        return 0.0
    return min(score / len(terms), 1.0)


def _full_document_cursor_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor or not cursor.isascii() or not cursor.isdecimal():
        raise ValueError("full-document cursor must be a non-negative integer")
    offset = int(cursor)
    if cursor != str(offset):
        raise ValueError("full-document cursor must use its canonical decimal form")
    return offset


def _matching_document_options(
    options: Sequence[AuthorizedDocumentOption], *, query: str
) -> list[AuthorizedDocumentOption]:
    normalized_query = _normalize_document_reference(query)
    matched: list[AuthorizedDocumentOption] = []
    for option in options:
        references = [option.title]
        if option.source_filename:
            references.extend(
                (
                    option.source_filename,
                    option.source_filename.rsplit(".", maxsplit=1)[0],
                )
            )
        references_match = any(
            reference in normalized_query
            for raw_reference in references
            if len(reference := _normalize_document_reference(raw_reference)) >= 4
        )
        if references_match:
            matched.append(option)
    return matched


def _normalize_document_reference(value: str) -> str:
    return " ".join(value.casefold().split())


def _distributed_chunk_sample(
    chunks: Sequence[DocumentChunkModel], *, limit: int
) -> list[DocumentChunkModel]:
    """Keep bounded provenance spread across the entire covered range."""
    if limit < 1:
        raise ValueError("distributed chunk sample limit must be positive")
    if len(chunks) <= limit:
        return list(chunks)
    if limit == 1:
        return [chunks[0]]
    last_index = len(chunks) - 1
    return [chunks[(sample_index * last_index) // (limit - 1)] for sample_index in range(limit)]


def _authorized_document_option(row: object) -> AuthorizedDocumentOption:
    document_id, title, source_filename, knowledge_base_id, knowledge_base_name = row  # type: ignore[misc]
    return AuthorizedDocumentOption(
        document_id=document_id,
        title=title,
        source_filename=source_filename,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name=knowledge_base_name,
    )


def _chunk_matches_document_content(chunk: DocumentChunkModel, document_content: str) -> bool:
    if chunk.start_offset < 0 or chunk.end_offset < chunk.start_offset:
        return False
    if chunk.end_offset > len(document_content):
        return False
    window_start = max(chunk.start_offset - 512, 0)
    window_end = min(chunk.end_offset + 512, len(document_content))
    expected = _normalize_chunk_content(document_content[window_start:window_end])
    actual = _normalize_chunk_content(chunk.content)
    return bool(actual) and (actual == expected or actual in expected)


def _structured_entity_score(entity: StructuredKnowledgeEntityModel, terms: set[str]) -> float:
    label_score = _normalized_keyword_score(_keyword_score(entity.label, terms), terms)
    return round(0.9 + (0.1 * label_score), 6)


def _embedding_from_json(value: str) -> list[float]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    vector: list[float] = []
    for item in parsed:
        if isinstance(item, (int, float)):
            vector.append(float(item))
        else:
            return []
    return vector


def _compatible_embeddings(left: list[float], right: list[float]) -> bool:
    return bool(left) and bool(right) and len(left) == len(right)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
