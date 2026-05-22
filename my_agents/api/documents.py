"""Document metadata and permission API routes."""

from __future__ import annotations

from threading import Thread
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_create_document
from my_agents.groups.models import MembershipModel, MembershipRole
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentModel,
    DocumentPermissionModel,
    EntityMentionModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    ExtractionStatus,
    KnowledgeBaseModel,
)
from my_agents.knowledge.schemas import (
    DocumentCreateRequest,
    DocumentPermissionPatchRequest,
    DocumentPermissionResponse,
    DocumentResponse,
    ExtractionRunResponse,
)
from my_agents.knowledge.uploads import (
    DocumentUploadError,
    UnsupportedDocumentUploadError,
    parse_uploaded_document,
)
from my_agents.permissions.contracts import DocumentOperation
from my_agents.permissions.service import AuthorizationService
from my_agents.persistence.database import _sessionmaker_for_url, get_database_session
from my_agents.settings import Settings, get_settings

documents_router = APIRouter(prefix="/documents", tags=["documents"])


@documents_router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    request: DocumentCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentResponse:
    assert_guest_can_create_document(db, principal, settings)
    group_id = _resolve_document_group_id(
        db=db,
        requested_group_id=request.group_id,
        knowledge_base_id=request.knowledge_base_id,
        user_id=principal.user_id,
    )
    document = DocumentModel(
        title=request.title.strip(),
        content=request.content,
        source_type="text",
        owner_user_id=principal.user_id,
        group_id=group_id,
        knowledge_base_id=request.knowledge_base_id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _document_response(document)


@documents_router.post(
    "/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    file: Annotated[
        UploadFile,
        File(description="Supported file: text-based PDF, Markdown, or plain text."),
    ],
    knowledge_base_id: Annotated[str, Form(min_length=1)],
    group_id: Annotated[str | None, Form()] = None,
) -> DocumentResponse:
    """Create a document from a safe upload and persist parser metadata."""
    assert_guest_can_create_document(db, principal, settings)
    resolved_group_id = _resolve_document_group_id(
        db=db,
        requested_group_id=group_id,
        knowledge_base_id=knowledge_base_id,
        user_id=principal.user_id,
    )
    content = await file.read()
    try:
        parsed = parse_uploaded_document(
            filename=file.filename,
            content_type=file.content_type,
            content=content,
        )
    except DocumentUploadError as exc:
        status_code = (
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            if isinstance(exc, UnsupportedDocumentUploadError)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    document = DocumentModel(
        title=title.strip(),
        content=parsed.content,
        source_type=parsed.source_type,
        source_filename=file.filename.strip() if file.filename else None,
        source_content_type=parsed.source_content_type,
        source_byte_size=parsed.byte_size,
        source_sha256=parsed.sha256,
        source_page_count=parsed.page_count,
        parser_name=parsed.parser_name,
        owner_user_id=principal.user_id,
        group_id=resolved_group_id,
        knowledge_base_id=knowledge_base_id,
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
    assert_guest_access_active(db, principal)
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
    assert_guest_access_active(db, principal)
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.READ,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return _document_response(document)


@documents_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> Response:
    """Delete an authorized document and dependent extraction/retrieval artifacts."""
    assert_guest_access_active(db, principal)
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.DELETE,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    _delete_document_dependencies(db, document_id)
    db.delete(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@documents_router.patch("/{document_id}/permissions", response_model=DocumentPermissionResponse)
def patch_document_permission(
    document_id: str,
    request: DocumentPermissionPatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> DocumentPermissionResponse:
    assert_guest_access_active(db, principal)
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
    assert_guest_access_active(db, principal)
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.INGEST,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")
    summary = KnowledgeExtractionService(db).ingest_document(document)
    return _extraction_run_response(
        db,
        summary.run,
        chunk_count=summary.chunk_count,
        entity_count=summary.entity_count,
        relationship_count=summary.relationship_count,
    )


@documents_router.post(
    "/{document_id}/ingest/async",
    response_model=ExtractionRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_document_async(
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExtractionRunResponse:
    """Queue an in-process background extraction run for an authorized document."""
    assert_guest_access_active(db, principal)
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.INGEST,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")

    run = KnowledgeExtractionService(db).create_extraction_run(document_id=document.id)
    response = _extraction_run_response(db, run)
    Thread(
        target=_execute_ingestion_run_in_background,
        args=(settings.database_url, run.id),
        daemon=True,
        name=f"document-ingest-{run.id}",
    ).start()
    return response


@documents_router.get(
    "/{document_id}/extraction-runs/{run_id}",
    response_model=ExtractionRunResponse,
)
def get_extraction_run(
    document_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ExtractionRunResponse:
    """Return one extraction run for a readable document."""
    assert_guest_access_active(db, principal)
    document = _get_document_or_404(db, document_id)
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.READ,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    run = db.get(ExtractionRunModel, run_id)
    if run is None or run.document_id != document_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="extraction run not found",
        )
    return _extraction_run_response(db, run)


@documents_router.get("/{document_id}/extraction-runs", response_model=list[ExtractionRunResponse])
def list_extraction_runs(
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[ExtractionRunResponse]:
    """Return extraction runs for a readable document."""
    assert_guest_access_active(db, principal)
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
        responses.append(_extraction_run_response(db, run))
    return responses


def _execute_ingestion_run_in_background(database_url: str, run_id: str) -> None:
    """Execute a queued run with a fresh session instead of the request session."""
    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        run = db.get(ExtractionRunModel, run_id)
        if run is None:
            return
        document = db.get(DocumentModel, run.document_id)
        if document is None:
            run.status = ExtractionStatus.FAILED.value
            run.stage = "failed"
            run.error = "DocumentNotFound: document not found"
            db.commit()
            return
        try:
            KnowledgeExtractionService(db).ingest_document(document, run=run)
        except Exception:
            # KnowledgeExtractionService persists a bounded failed status before re-raising.
            return


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


def _resolve_document_group_id(
    *,
    db: Session,
    requested_group_id: str | None,
    knowledge_base_id: str | None,
    user_id: str,
) -> str | None:
    group_id = requested_group_id
    if knowledge_base_id is not None:
        knowledge_base = _get_authorized_knowledge_base(db, knowledge_base_id, user_id)
        group_id = knowledge_base.group_id
    if group_id is not None:
        _require_group_write_access(db, group_id, user_id)
    return group_id


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


def _delete_document_dependencies(db: Session, document_id: str) -> None:
    """Remove rows that hold foreign keys to a document or its chunks/runs."""
    db.execute(delete(CitationModel).where(CitationModel.document_id == document_id))
    db.execute(
        delete(EntityRelationshipModel).where(EntityRelationshipModel.document_id == document_id)
    )
    db.execute(delete(EntityMentionModel).where(EntityMentionModel.document_id == document_id))
    db.execute(delete(DocumentChunkModel).where(DocumentChunkModel.document_id == document_id))
    db.execute(delete(ExtractionRunModel).where(ExtractionRunModel.document_id == document_id))
    db.execute(
        delete(DocumentPermissionModel).where(DocumentPermissionModel.document_id == document_id)
    )


def _document_response(document: DocumentModel) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        title=document.title,
        owner_user_id=document.owner_user_id,
        group_id=document.group_id,
        knowledge_base_id=document.knowledge_base_id,
        source_type=document.source_type,
        source_filename=document.source_filename,
        source_content_type=document.source_content_type,
        source_byte_size=document.source_byte_size,
        source_sha256=document.source_sha256,
        source_page_count=document.source_page_count,
        parser_name=document.parser_name,
    )


def _extraction_run_response(
    db: Session,
    run: ExtractionRunModel,
    *,
    chunk_count: int | None = None,
    entity_count: int | None = None,
    relationship_count: int | None = None,
) -> ExtractionRunResponse:
    resolved_chunk_count = _resolve_run_count(
        db,
        run,
        explicit_count=chunk_count,
        stored_count=run.chunk_count,
        counter=_count_chunks,
    )
    resolved_entity_count = _resolve_run_count(
        db,
        run,
        explicit_count=entity_count,
        stored_count=run.entity_count,
        counter=_count_entities,
    )
    resolved_relationship_count = _resolve_run_count(
        db,
        run,
        explicit_count=relationship_count,
        stored_count=run.relationship_count,
        counter=_count_relationships,
    )
    return ExtractionRunResponse(
        id=run.id,
        document_id=run.document_id,
        status=run.status,
        stage=run.stage,
        progress_percent=run.progress_percent,
        chunk_count=resolved_chunk_count,
        entity_count=resolved_entity_count,
        relationship_count=resolved_relationship_count,
        error=run.error,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _resolve_run_count(
    db: Session,
    run: ExtractionRunModel,
    *,
    explicit_count: int | None,
    stored_count: int,
    counter,
) -> int:
    if explicit_count is not None:
        return explicit_count
    if stored_count > 0 or run.status != ExtractionStatus.COMPLETED.value:
        return stored_count
    return counter(db, run.id)


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
