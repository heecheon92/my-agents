"""Document metadata and permission API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.groups.models import MembershipModel, MembershipRole
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentModel,
    DocumentPermissionModel,
    EntityMentionModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
)
from my_agents.knowledge.schemas import (
    DocumentCreateRequest,
    DocumentPermissionPatchRequest,
    DocumentPermissionResponse,
    DocumentResponse,
    ExtractionRunResponse,
)
from my_agents.permissions.contracts import DocumentOperation
from my_agents.permissions.service import AuthorizationService
from my_agents.persistence.database import get_database_session

documents_router = APIRouter(prefix="/documents", tags=["documents"])


@documents_router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    request: DocumentCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> DocumentResponse:
    group_id = request.group_id
    if request.knowledge_base_id is not None:
        knowledge_base = _get_authorized_knowledge_base(
            db, request.knowledge_base_id, principal.user_id
        )
        group_id = knowledge_base.group_id
    if group_id is not None:
        _require_group_write_access(db, group_id, principal.user_id)
    document = DocumentModel(
        title=request.title.strip(),
        content=request.content,
        owner_user_id=principal.user_id,
        group_id=group_id,
        knowledge_base_id=request.knowledge_base_id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _document_response(document)


@documents_router.get("", response_model=list[DocumentResponse])
def list_documents(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[DocumentResponse]:
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == principal.user_id)
    explicit_ids = select(DocumentPermissionModel.document_id).where(
        DocumentPermissionModel.user_id == principal.user_id,
        DocumentPermissionModel.can_read.is_(True),
    )
    docs = db.scalars(
        select(DocumentModel).where(
            or_(
                DocumentModel.owner_user_id == principal.user_id,
                DocumentModel.group_id.in_(group_ids),
                DocumentModel.id.in_(explicit_ids),
            )
        )
    ).all()
    return [_document_response(document) for document in docs]


@documents_router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> DocumentResponse:
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.READ,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return _document_response(document)


@documents_router.patch("/{document_id}/permissions", response_model=DocumentPermissionResponse)
def patch_document_permission(
    document_id: str,
    request: DocumentPermissionPatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> DocumentPermissionResponse:
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.MANAGE_PERMISSIONS,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")
    permission = db.scalar(
        select(DocumentPermissionModel).where(
            DocumentPermissionModel.document_id == document_id,
            DocumentPermissionModel.user_id == request.user_id,
        )
    )
    if permission is None:
        permission = DocumentPermissionModel(document_id=document_id, user_id=request.user_id)
    permission.can_read = request.can_read
    permission.can_write = request.can_write
    permission.can_manage = request.can_manage
    permission.can_ingest = request.can_ingest
    db.add(permission)
    db.commit()
    db.refresh(permission)
    return DocumentPermissionResponse(
        document_id=permission.document_id,
        user_id=permission.user_id,
        can_read=permission.can_read,
        can_write=permission.can_write,
        can_manage=permission.can_manage,
        can_ingest=permission.can_ingest,
    )


@documents_router.post("/{document_id}/ingest", response_model=ExtractionRunResponse)
def ingest_document(
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ExtractionRunResponse:
    """Run deterministic thin extraction over an authorized document."""
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.INGEST,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")
    summary = KnowledgeExtractionService(db).ingest_document(document)
    return ExtractionRunResponse(
        id=summary.run.id,
        document_id=document.id,
        status=summary.run.status,
        chunk_count=summary.chunk_count,
        entity_count=summary.entity_count,
        relationship_count=summary.relationship_count,
    )


@documents_router.get("/{document_id}/extraction-runs", response_model=list[ExtractionRunResponse])
def list_extraction_runs(
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[ExtractionRunResponse]:
    """Return extraction runs for a readable document."""
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.READ,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    runs = db.scalars(
        select(ExtractionRunModel).where(ExtractionRunModel.document_id == document_id)
    ).all()
    responses: list[ExtractionRunResponse] = []
    for run in runs:
        responses.append(
            ExtractionRunResponse(
                id=run.id,
                document_id=run.document_id,
                status=run.status,
                chunk_count=_count_chunks(db, run.id),
                entity_count=_count_entities(db, run.id),
                relationship_count=_count_relationships(db, run.id),
            )
        )
    return responses


def _require_group_write_access(db: Session, group_id: str, user_id: str) -> None:
    membership = db.scalar(
        select(MembershipModel).where(
            MembershipModel.group_id == group_id,
            MembershipModel.user_id == user_id,
        )
    )
    if membership is None or membership.role not in {
        MembershipRole.OWNER.value,
        MembershipRole.ADMIN.value,
        MembershipRole.EDITOR.value,
    }:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")


def _get_authorized_knowledge_base(
    db: Session, knowledge_base_id: str, user_id: str
) -> KnowledgeBaseModel:
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if knowledge_base.owner_user_id == user_id:
        return knowledge_base
    if knowledge_base.group_id is not None:
        membership = db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == knowledge_base.group_id,
                MembershipModel.user_id == user_id,
            )
        )
        if membership is not None:
            return knowledge_base
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")


def _get_document_or_404(db: Session, document_id: str) -> DocumentModel:
    document = db.get(DocumentModel, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


def _document_response(document: DocumentModel) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        title=document.title,
        owner_user_id=document.owner_user_id,
        group_id=document.group_id,
        knowledge_base_id=document.knowledge_base_id,
    )


def _count_chunks(db: Session, extraction_run_id: str) -> int:
    return len(
        db.scalars(
            select(DocumentChunkModel).where(
                DocumentChunkModel.extraction_run_id == extraction_run_id
            )
        ).all()
    )


def _count_entities(db: Session, extraction_run_id: str) -> int:
    return len(
        {
            mention.entity_id
            for mention in db.scalars(
                select(EntityMentionModel).where(
                    EntityMentionModel.extraction_run_id == extraction_run_id
                )
            ).all()
        }
    )


def _count_relationships(db: Session, extraction_run_id: str) -> int:
    return len(
        db.scalars(
            select(EntityRelationshipModel).where(
                EntityRelationshipModel.extraction_run_id == extraction_run_id
            )
        ).all()
    )
