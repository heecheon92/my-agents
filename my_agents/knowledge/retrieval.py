"""Permission-aware deterministic retrieval with graph-shaped expansion."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from my_agents.groups.models import MembershipModel
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
    score: int
    source: str


class RetrievalService:
    """Retrieve only authorized chunks, then expand through authorized entity links."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def retrieve(self, *, user_id: str, query: str, limit: int = 5) -> list[RetrievedChunk]:
        terms = _query_terms(query)
        direct = self._direct_authorized_matches(user_id=user_id, terms=terms)
        expanded = self._expand_authorized_related(user_id=user_id, direct=direct)
        combined: dict[str, RetrievedChunk] = {item.chunk.id: item for item in direct}
        for item in expanded:
            combined.setdefault(item.chunk.id, item)
        if not combined and _needs_personal_document_fallback(query):
            return self._recent_authorized_chunks(user_id=user_id, limit=limit)
        return sorted(combined.values(), key=lambda item: (-item.score, item.chunk.ordinal))[:limit]

    def _direct_authorized_matches(self, *, user_id: str, terms: set[str]) -> list[RetrievedChunk]:
        rows = self._authorized_chunk_rows(user_id)
        matches: list[RetrievedChunk] = []
        for chunk, document in rows:
            score = _score(chunk.content, terms)
            if score > 0:
                matches.append(
                    RetrievedChunk(
                        chunk=chunk,
                        document=document,
                        score=score,
                        source="vector_fixture",
                    )
                )
        return sorted(matches, key=lambda item: (-item.score, item.chunk.ordinal))

    def _expand_authorized_related(
        self, *, user_id: str, direct: list[RetrievedChunk]
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
        authorized_rows = self._authorized_chunk_rows(user_id)
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
                        score=1,
                        source="graph_expansion",
                    )
                )
        return expanded

    def _recent_authorized_chunks(self, *, user_id: str, limit: int) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk=chunk,
                document=document,
                score=1,
                source="document_fallback",
            )
            for chunk, document in self._authorized_chunk_rows(user_id)[:limit]
        ]

    def _authorized_chunk_rows(
        self, user_id: str
    ) -> list[tuple[DocumentChunkModel, DocumentModel]]:
        group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
        explicit_doc_ids = select(DocumentPermissionModel.document_id).where(
            DocumentPermissionModel.user_id == user_id,
            DocumentPermissionModel.can_read.is_(True),
        )
        statement = (
            select(DocumentChunkModel, DocumentModel)
            .join(DocumentModel, DocumentChunkModel.document_id == DocumentModel.id)
            .where(
                or_(
                    DocumentModel.owner_user_id == user_id,
                    DocumentModel.group_id.in_(group_ids),
                    DocumentModel.id.in_(explicit_doc_ids),
                )
            )
            .order_by(desc(DocumentModel.created_at), DocumentChunkModel.ordinal)
        )
        return list(self._db.execute(statement).all())


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


def _score(content: str, terms: set[str]) -> int:
    lowered = content.casefold()
    return sum(1 for term in terms if term in lowered)
