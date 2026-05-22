"""Permission-aware retrieval with JSON-backed semantic vector ranking."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from collections.abc import Sequence

from sqlalchemy import and_, desc, false, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from my_agents.groups.models import MembershipModel
from my_agents.knowledge.embeddings import EmbeddingProvider, get_embedding_provider
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentModel,
    DocumentPermissionModel,
    EntityMentionModel,
)


@dataclass(frozen=True)
class RetrievedChunk:
    """Authorized retrieved context chunk."""

    chunk: DocumentChunkModel
    document: DocumentModel
    score: float
    source: str


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
        direct = self._direct_authorized_matches(
            user_id=user_id,
            query=query,
            terms=terms,
            knowledge_base_ids=knowledge_base_ids,
        )
        expanded = self._expand_authorized_related(
            user_id=user_id,
            direct=direct,
            knowledge_base_ids=knowledge_base_ids,
        )
        combined: dict[str, RetrievedChunk] = {item.chunk.id: item for item in direct}
        for item in expanded:
            combined.setdefault(item.chunk.id, item)
        if not combined and _needs_personal_document_fallback(query):
            return self._recent_authorized_chunks(
                user_id=user_id, limit=limit, knowledge_base_ids=knowledge_base_ids
            )
        return sorted(combined.values(), key=lambda item: (-item.score, item.chunk.ordinal))[:limit]

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

    def _authorized_chunk_rows(
        self, user_id: str, *, knowledge_base_ids: Sequence[str] | None
    ) -> list[tuple[DocumentChunkModel, DocumentModel]]:
        statement = (
            select(DocumentChunkModel, DocumentModel)
            .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
            .where(_authorized_document_filter(user_id, knowledge_base_ids=knowledge_base_ids))
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


def _authorized_document_filter(user_id: str, *, knowledge_base_ids: Sequence[str] | None = None):
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    explicit_doc_ids = select(DocumentPermissionModel.document_id).where(
        DocumentPermissionModel.user_id == user_id,
        DocumentPermissionModel.can_read.is_(True),
    )
    return and_(
        _knowledge_base_scope_filter(user_id, knowledge_base_ids),
        or_(
            DocumentModel.owner_user_id == user_id,
            DocumentModel.group_id.in_(group_ids),
            DocumentModel.id.in_(explicit_doc_ids),
        ),
    )


def _knowledge_base_scope_filter(user_id: str, knowledge_base_ids: Sequence[str] | None = None):
    from my_agents.knowledge.models import KnowledgeBaseModel

    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    authorized_kb_ids = select(KnowledgeBaseModel.id).where(
        or_(
            KnowledgeBaseModel.owner_user_id == user_id,
            KnowledgeBaseModel.group_id.in_(group_ids),
        )
    )
    if knowledge_base_ids is None:
        return DocumentModel.knowledge_base_id.in_(authorized_kb_ids)
    unique_ids = tuple(dict.fromkeys(knowledge_base_ids))
    if not unique_ids:
        return false()
    return and_(
        DocumentModel.knowledge_base_id.in_(unique_ids),
        DocumentModel.knowledge_base_id.in_(authorized_kb_ids),
    )


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


def _query_terms(query: str) -> set[str]:
    return {term.casefold() for term in re.findall(r"[A-Za-z0-9가-힣]+", query) if len(term) > 1}


def _needs_personal_document_fallback(query: str) -> bool:
    normalized = query.casefold()
    return any(hint in normalized for hint in _PERSONAL_DOCUMENT_FALLBACK_HINTS)


def _keyword_score(content: str, terms: set[str]) -> int:
    lowered = content.casefold()
    return sum(1 for term in terms if term in lowered)


def _normalized_keyword_score(score: int, terms: set[str]) -> float:
    if not terms:
        return 0.0
    return min(score / len(terms), 1.0)


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
