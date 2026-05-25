"""Permission-aware retrieval with JSON-backed semantic vector ranking."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import and_, desc, false, func, inspect, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from my_agents.groups.models import MembershipModel
from my_agents.knowledge.auth import (
    authorized_knowledge_base_filter,
    published_personal_knowledge_base_ids_for_user,
)
from my_agents.knowledge.embeddings import EmbeddingProvider, get_embedding_provider
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    DocumentPermissionModel,
    EntityMentionModel,
    StructuredKnowledgeEntityModel,
)


@dataclass(frozen=True)
class RetrievedChunk:
    """Authorized retrieved context chunk."""

    chunk: DocumentChunkModel
    document: DocumentModel
    score: float
    source: str


@dataclass(frozen=True)
class RetrievedStructuredEntity:
    """Authorized structured fact with chunk/document provenance."""

    entity: StructuredKnowledgeEntityModel
    chunk: DocumentChunkModel
    document: DocumentModel
    score: float
    source: str = "structured_entity"


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
    ) -> list[RetrievedChunk]:
        terms = _query_terms(query)
        metadata_matches = self._document_metadata_matches(
            user_id=user_id,
            query=query,
            terms=terms,
            knowledge_base_ids=knowledge_base_ids,
            limit=limit,
        )
        metadata_profile_matches = self._document_metadata_profile_matches(
            user_id=user_id,
            query=query,
            terms=terms,
            knowledge_base_ids=knowledge_base_ids,
            limit=limit,
        )
        direct = [
            *metadata_matches,
            *self._direct_authorized_matches(
                user_id=user_id,
                query=query,
                terms=terms,
                knowledge_base_ids=knowledge_base_ids,
            ),
            *metadata_profile_matches,
        ]
        expanded = self._expand_authorized_related(
            user_id=user_id,
            direct=direct,
            knowledge_base_ids=knowledge_base_ids,
        )
        combined: dict[str, RetrievedChunk] = {}
        for item in direct:
            existing = combined.get(item.chunk.id)
            if existing is None or _prefer_retrieved_chunk(item, existing):
                combined[item.chunk.id] = item
        for item in expanded:
            combined.setdefault(item.chunk.id, item)
        if not combined and _needs_personal_document_fallback(query):
            return _dedupe_retrieved_chunks(
                self._recent_authorized_chunks(
                    user_id=user_id, limit=limit, knowledge_base_ids=knowledge_base_ids
                ),
                limit=limit,
            )
        ranked = sorted(combined.values(), key=lambda item: (-item.score, item.chunk.ordinal))
        if _needs_document_overview(query):
            ranked = self._with_small_document_overview_chunks(
                ranked,
                user_id=user_id,
                knowledge_base_ids=knowledge_base_ids,
                char_budget=6000,
            )
        return _dedupe_retrieved_chunks(ranked, limit=limit)

    def authorized_document_count(
        self, *, user_id: str, knowledge_base_ids: Sequence[str] | None = None
    ) -> int:
        """Return how many distinct documents the user can read."""
        return (
            self._db.scalar(
                select(func.count(DocumentModel.id.distinct())).where(
                    _authorized_document_filter(user_id, knowledge_base_ids=knowledge_base_ids)
                )
            )
            or 0
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
            .join(
                DocumentChunkModel,
                StructuredKnowledgeEntityModel.chunk_id == DocumentChunkModel.id,
            )
            .join(DocumentModel, StructuredKnowledgeEntityModel.document_id == DocumentModel.id)
            .where(
                _authorized_document_filter(user_id, knowledge_base_ids=knowledge_base_ids),
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
        matches = [
            RetrievedStructuredEntity(
                entity=entity,
                chunk=chunk,
                document=document,
                score=_structured_entity_score(entity, terms),
            )
            for entity, chunk, document in self._db.execute(statement).all()
        ]
        return sorted(matches, key=lambda item: (-item.score, item.chunk.ordinal))

    def _direct_authorized_matches(
        self,
        *,
        user_id: str,
        query: str,
        terms: set[str],
        knowledge_base_ids: Sequence[str] | None,
    ) -> list[RetrievedChunk]:
        query_embedding = self._embedding_provider.embed_query(query)
        if _uses_postgres(self._db):
            sql_matches = self._postgres_vector_authorized_matches(
                user_id=user_id,
                query_embedding=query_embedding,
                terms=terms,
                knowledge_base_ids=knowledge_base_ids,
                limit=20,
            )
            if sql_matches:
                return sql_matches
        return self._json_authorized_matches(
            user_id=user_id,
            query_embedding=query_embedding,
            terms=terms,
            knowledge_base_ids=knowledge_base_ids,
        )

    def _json_authorized_matches(
        self,
        *,
        user_id: str,
        query_embedding: list[float],
        terms: set[str],
        knowledge_base_ids: Sequence[str] | None,
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
                combined_score = (0.75 * positive_cosine) + (0.25 * keyword_rank)
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
            if keyword_score > 0:
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
    ) -> list[RetrievedChunk]:
        if not query_embedding:
            return []
        try:
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
            combined_score = (0.75 * positive_cosine) + (0.25 * keyword_rank)
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
        if not direct:
            return []
        entity_ids = {
            mention.entity_id
            for item in direct
            for mention in self._db.scalars(
                select(EntityMentionModel).where(EntityMentionModel.chunk_id == item.chunk.id)
            ).all()
        }
        if not entity_ids:
            return []
        authorized_rows = self._authorized_chunk_rows(
            user_id, knowledge_base_ids=knowledge_base_ids
        )
        expanded: list[RetrievedChunk] = []
        direct_chunk_ids = {item.chunk.id for item in direct}
        for chunk, document in authorized_rows:
            if chunk.id in direct_chunk_ids:
                continue
            mentions = self._db.scalars(
                select(EntityMentionModel).where(EntityMentionModel.chunk_id == chunk.id)
            ).all()
            if any(mention.entity_id in entity_ids for mention in mentions):
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
    ) -> list[RetrievedChunk]:
        """Return chunks from authorized documents whose title/filename matches the query."""
        signal_terms = _metadata_signal_terms(terms)
        if not signal_terms:
            return []
        documents: dict[str, tuple[DocumentModel, float]] = {}
        for _, document in self._authorized_chunk_rows(
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
            for chunk, document in self._authorized_chunk_rows(
                user_id, knowledge_base_ids=knowledge_base_ids
            )
            if document.id in documents
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
    ) -> list[RetrievedChunk]:
        """Supplement summary-style queries with broader coverage for small matched docs."""
        if not ranked:
            return ranked
        matched_document_ids = {item.document.id for item in ranked}
        existing_chunk_ids = {item.chunk.id for item in ranked}
        used_chars = sum(len(item.chunk.content) for item in ranked)
        supplemented = list(ranked)
        for chunk, document in self._authorized_chunk_rows(
            user_id, knowledge_base_ids=knowledge_base_ids
        ):
            if document.id not in matched_document_ids or chunk.id in existing_chunk_ids:
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
        query: str,
        terms: set[str],
        knowledge_base_ids: Sequence[str] | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        """Return representative chunks from documents matched by generated metadata."""
        if not _schema_has_document_metadata_profiles(self._db):
            return []
        signal_terms = _metadata_signal_terms(terms)
        if not signal_terms:
            return []
        query_embedding = self._embedding_provider.embed_query(query)
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
        rows = [
            (chunk, document, document_scores[document.id][1])
            for chunk, document in self._authorized_chunk_rows(
                user_id, knowledge_base_ids=knowledge_base_ids
            )
            if document.id in document_scores
        ]
        rows.sort(key=lambda row: (-row[2], -row[1].created_at.timestamp(), row[0].ordinal))
        matches: list[RetrievedChunk] = []
        seen_document_ids: set[str] = set()
        for chunk, document, score in rows:
            if document.id in seen_document_ids:
                continue
            matches.append(
                RetrievedChunk(
                    chunk=chunk,
                    document=document,
                    score=round(max(score - (chunk.ordinal * 0.001), 0.01), 6),
                    source="document_metadata_profile",
                )
            )
            seen_document_ids.add(document.id)
            if len(matches) >= limit:
                break
        return matches

    def _authorized_metadata_profile_rows(
        self, user_id: str, *, knowledge_base_ids: Sequence[str] | None
    ) -> list[tuple[DocumentMetadataProfileModel, DocumentModel]]:
        statement = (
            select(DocumentMetadataProfileModel, DocumentModel)
            .join(DocumentModel, DocumentMetadataProfileModel.document_id == DocumentModel.id)
            .where(_authorized_document_filter(user_id, knowledge_base_ids=knowledge_base_ids))
            .order_by(desc(DocumentModel.created_at), desc(DocumentMetadataProfileModel.created_at))
        )
        return list(self._db.execute(statement).all())

    def _authorized_chunk_rows(
        self, user_id: str, *, knowledge_base_ids: Sequence[str] | None
    ) -> list[tuple[DocumentChunkModel, DocumentModel]]:
        include_published_personal_kbs = _schema_has_knowledge_base_publications(self._db)
        statement = (
            select(DocumentChunkModel, DocumentModel)
            .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
            .where(
                _authorized_document_filter(
                    user_id,
                    knowledge_base_ids=knowledge_base_ids,
                    include_published_personal_kbs=include_published_personal_kbs,
                )
            )
            .order_by(desc(DocumentModel.created_at), DocumentChunkModel.ordinal)
        )
        return list(self._db.execute(statement).all())


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
        .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
        .where(
            _authorized_document_filter(user_id),
            _knowledge_base_scope_filter(user_id, knowledge_base_ids),
            embedding_vector.is_not(None),
        )
        .order_by(vector_distance, desc(DocumentModel.created_at), DocumentChunkModel.ordinal)
        .limit(limit)
    )


def _authorized_document_filter(
    user_id: str,
    *,
    knowledge_base_ids: Sequence[str] | None = None,
    include_published_personal_kbs: bool = True,
):
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    explicit_doc_ids = select(DocumentPermissionModel.document_id).where(
        DocumentPermissionModel.user_id == user_id,
        DocumentPermissionModel.can_read.is_(True),
    )
    readable_document_predicates = [
        and_(DocumentModel.group_id.is_(None), DocumentModel.owner_user_id == user_id),
        DocumentModel.group_id.in_(group_ids),
        DocumentModel.id.in_(explicit_doc_ids),
    ]
    if include_published_personal_kbs:
        readable_document_predicates.append(
            DocumentModel.knowledge_base_id.in_(
                published_personal_knowledge_base_ids_for_user(user_id)
            )
        )
    return and_(
        _knowledge_base_scope_filter(
            user_id,
            knowledge_base_ids,
            include_published_personal_kbs=include_published_personal_kbs,
        ),
        or_(*readable_document_predicates),
    )


def _knowledge_base_scope_filter(
    user_id: str,
    knowledge_base_ids: Sequence[str] | None = None,
    *,
    include_published_personal_kbs: bool = True,
):
    from my_agents.knowledge.models import KnowledgeBaseModel

    if include_published_personal_kbs:
        authorized_filter = authorized_knowledge_base_filter(user_id)
    else:
        group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
        authorized_filter = or_(
            and_(
                KnowledgeBaseModel.scope == "personal",
                KnowledgeBaseModel.group_id.is_(None),
                KnowledgeBaseModel.owner_user_id == user_id,
            ),
            and_(
                KnowledgeBaseModel.scope == "group",
                KnowledgeBaseModel.group_id.in_(group_ids),
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


def _schema_has_knowledge_base_publications(db: Session) -> bool:
    return inspect(db.get_bind()).has_table("knowledge_base_publications")


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
    "portfolio",
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
