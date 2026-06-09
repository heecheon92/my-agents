"""Document metadata and permission API routes."""

from __future__ import annotations

import json
import logging
from threading import Thread
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_create_document
from my_agents.groups.models import MembershipModel
from my_agents.knowledge.auth import (
    get_authorized_knowledge_base_or_404,
    require_personal_knowledge_base_for_document_write,
)
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.ingestion_worker import execute_claimed_extraction_run
from my_agents.knowledge.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    DocumentParseArtifactModel,
    DocumentPermissionModel,
    EntityMentionModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    ExtractionStatus,
    KnowledgeBaseModel,
    KnowledgeBasePurpose,
    StructuredKnowledgeEntityModel,
)
from my_agents.knowledge.pdf_uploads import DoclingExtractionConfig, TesseractOcrConfig
from my_agents.knowledge.schemas import (
    DocumentCreateRequest,
    DocumentPermissionPatchRequest,
    DocumentPermissionResponse,
    DocumentResponse,
    ExtractionRunResponse,
)
from my_agents.knowledge.uploads import (
    DocumentUploadError,
    ParsedDocumentUpload,
    UnsupportedDocumentUploadError,
    parse_uploaded_document,
)
from my_agents.permissions.contracts import DocumentOperation
from my_agents.permissions.service import AuthorizationService
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

documents_router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


@documents_router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    request: DocumentCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentResponse:
    if request.knowledge_base_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="knowledge_base_id is required",
        )
    if request.group_id is not None:
        knowledge_base = get_authorized_knowledge_base_or_404(
            db, request.knowledge_base_id, principal.user_id
        )
        if request.group_id != knowledge_base.group_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="group_id must match knowledge base group_id",
            )
    return create_document_in_knowledge_base(
        knowledge_base_id=request.knowledge_base_id,
        request=request,
        principal=principal,
        db=db,
        settings=settings,
    )


def create_document_in_knowledge_base(
    *,
    knowledge_base_id: str,
    request: DocumentCreateRequest,
    principal: Principal,
    db: Session,
    settings: Settings,
) -> DocumentResponse:
    assert_guest_can_create_document(db, principal, settings)
    knowledge_base = require_personal_knowledge_base_for_document_write(
        db,
        knowledge_base_id=knowledge_base_id,
        user_id=principal.user_id,
    )
    document = DocumentModel(
        title=request.title.strip(),
        content=request.content,
        source_type="text",
        owner_user_id=principal.user_id,
        group_id=None,
        knowledge_base_id=knowledge_base.id,
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
        File(
            description=("Supported file: text-based PDF, Markdown, plain text, .xlsx, or .pptx.")
        ),
    ],
    knowledge_base_id: Annotated[str, Form(min_length=1)],
    group_id: Annotated[str | None, Form()] = None,
) -> DocumentResponse:
    """Create a document from a safe upload and persist parser metadata."""
    if group_id is not None:
        knowledge_base = get_authorized_knowledge_base_or_404(
            db, knowledge_base_id, principal.user_id
        )
        if group_id != knowledge_base.group_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="group_id must match knowledge base group_id",
            )
    return await upload_document_in_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        principal=principal,
        db=db,
        settings=settings,
        title=title,
        file=file,
    )


async def upload_document_in_knowledge_base(
    *,
    knowledge_base_id: str,
    principal: Principal,
    db: Session,
    settings: Settings,
    title: str,
    file: UploadFile,
) -> DocumentResponse:
    """Create an uploaded document inside an already path-selected KB."""
    assert_guest_can_create_document(db, principal, settings)
    knowledge_base = require_personal_knowledge_base_for_document_write(
        db, knowledge_base_id=knowledge_base_id, user_id=principal.user_id
    )
    content = await file.read()
    logger.info(
        "document_upload.received user_id=%s knowledge_base_id=%s title=%s filename=%s "
        "content_type=%s bytes=%d",
        principal.user_id,
        knowledge_base.id,
        title.strip(),
        file.filename,
        file.content_type,
        len(content),
    )
    try:
        parsed = parse_uploaded_document(
            filename=file.filename,
            content_type=file.content_type,
            content=content,
            docling_config=DoclingExtractionConfig(
                accelerator=settings.docling_accelerator,
                ocr_enabled=settings.docling_ocr_enabled,
                timeout_seconds=settings.docling_timeout_seconds,
                threads=settings.docling_threads,
            ),
            tesseract_config=TesseractOcrConfig(
                enabled=settings.tesseract_enabled,
                languages=settings.tesseract_languages,
                page_segmentation_mode=settings.tesseract_psm,
                render_scale=settings.tesseract_render_scale,
                timeout_seconds=settings.tesseract_timeout_seconds,
                max_pages=settings.tesseract_max_pages,
            ),
        )
    except DocumentUploadError as exc:
        logger.warning(
            "document_upload.rejected user_id=%s knowledge_base_id=%s title=%s filename=%s "
            "content_type=%s bytes=%d error=%s",
            principal.user_id,
            knowledge_base.id,
            title.strip(),
            file.filename,
            file.content_type,
            len(content),
            exc,
        )
        status_code = (
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            if isinstance(exc, UnsupportedDocumentUploadError)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    source_filename = file.filename.strip() if file.filename else None
    document = DocumentModel(
        title=title.strip(),
        content=parsed.content,
        source_type=parsed.source_type,
        source_filename=source_filename,
        source_content_type=parsed.source_content_type,
        source_byte_size=parsed.byte_size,
        source_sha256=parsed.sha256,
        source_page_count=parsed.page_count,
        parser_name=parsed.parser_name,
        owner_user_id=principal.user_id,
        group_id=None,
        knowledge_base_id=knowledge_base.id,
    )
    db.add(document)
    db.flush()
    _add_parse_artifact_for_upload(
        db,
        document=document,
        parsed=parsed,
        source_filename=source_filename,
    )
    db.commit()
    db.refresh(document)
    logger.info(
        "document_upload.persisted document_id=%s user_id=%s knowledge_base_id=%s "
        "source_type=%s parser=%s bytes=%s pages=%s chars=%d",
        document.id,
        principal.user_id,
        knowledge_base.id,
        document.source_type,
        document.parser_name,
        document.source_byte_size,
        document.source_page_count,
        len(document.content),
    )
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
        select(DocumentModel)
        .join(KnowledgeBaseModel, KnowledgeBaseModel.id == DocumentModel.knowledge_base_id)
        .where(
            KnowledgeBaseModel.purpose == KnowledgeBasePurpose.STANDARD.value,
            or_(
                DocumentModel.owner_user_id == principal.user_id,
                DocumentModel.group_id.in_(group_ids),
                DocumentModel.id.in_(explicit_ids),
            ),
        )
    ).all()
    auth = AuthorizationService(db)
    return [
        _document_response(document)
        for document in docs
        if auth.can(user_id=principal.user_id, document=document, operation=DocumentOperation.READ)
    ]


def list_documents_in_knowledge_base(
    *, db: Session, knowledge_base_id: str, principal: Principal
) -> list[DocumentResponse]:
    """Return documents in an authorized KB boundary."""
    assert_guest_access_active(db, principal)
    get_authorized_knowledge_base_or_404(db, knowledge_base_id, principal.user_id)
    documents = db.scalars(
        select(DocumentModel).where(DocumentModel.knowledge_base_id == knowledge_base_id)
    ).all()
    auth = AuthorizationService(db)
    return [
        _document_response(document)
        for document in documents
        if auth.can(user_id=principal.user_id, document=document, operation=DocumentOperation.READ)
    ]


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


def ingest_document_in_knowledge_base(
    *, knowledge_base_id: str, document_id: str, principal: Principal, db: Session
) -> ExtractionRunResponse:
    """Synchronously ingest a document after enforcing its KB path boundary."""
    assert_guest_access_active(db, principal)
    document = get_document_in_knowledge_base_or_404(
        db=db,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        user_id=principal.user_id,
    )
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
    _dispatch_extraction_run(settings=settings, run_id=run.id)
    return response


def ingest_document_async_in_knowledge_base(
    *,
    knowledge_base_id: str,
    document_id: str,
    principal: Principal,
    db: Session,
    settings: Settings,
) -> ExtractionRunResponse:
    """Queue ingestion after enforcing the document's KB path boundary."""
    assert_guest_access_active(db, principal)
    document = get_document_in_knowledge_base_or_404(
        db=db,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        user_id=principal.user_id,
    )
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.INGEST,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")

    run = KnowledgeExtractionService(db).create_extraction_run(document_id=document.id)
    response = _extraction_run_response(db, run)
    _dispatch_extraction_run(settings=settings, run_id=run.id)
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


def get_extraction_run_in_knowledge_base(
    *, knowledge_base_id: str, document_id: str, run_id: str, principal: Principal, db: Session
) -> ExtractionRunResponse:
    """Return one extraction run after enforcing its document KB path boundary."""
    assert_guest_access_active(db, principal)
    document = get_document_in_knowledge_base_or_404(
        db=db,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        user_id=principal.user_id,
    )
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


def list_extraction_runs_in_knowledge_base(
    *, knowledge_base_id: str, document_id: str, principal: Principal, db: Session
) -> list[ExtractionRunResponse]:
    """Return extraction runs after enforcing the document KB path boundary."""
    assert_guest_access_active(db, principal)
    document = get_document_in_knowledge_base_or_404(
        db=db,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        user_id=principal.user_id,
    )
    if not AuthorizationService(db).can(
        user_id=principal.user_id,
        document=document,
        operation=DocumentOperation.READ,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    runs = db.scalars(
        select(ExtractionRunModel).where(ExtractionRunModel.document_id == document_id)
    ).all()
    return [_extraction_run_response(db, run) for run in runs]


def _dispatch_extraction_run(*, settings: Settings, run_id: str) -> None:
    """Dispatch a queued extraction run according to the configured execution mode."""
    if settings.ingestion_execution_mode == "external_worker":
        logger.info(
            "document_ingestion.queued_for_external_worker run_id=%s execution_mode=%s",
            run_id,
            settings.ingestion_execution_mode,
        )
        return
    Thread(
        target=_execute_ingestion_run_in_background,
        args=(settings.database_url, run_id),
        daemon=True,
        name=f"document-ingest-{run_id}",
    ).start()


def _execute_ingestion_run_in_background(database_url: str, run_id: str) -> None:
    """Execute a queued run with a fresh session instead of the request session."""
    execute_claimed_extraction_run(database_url=database_url, run_id=run_id)


def _get_document_or_404(db: Session, document_id: str) -> DocumentModel:
    document = db.get(DocumentModel, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


def get_document_in_knowledge_base_or_404(
    *, db: Session, knowledge_base_id: str, document_id: str, user_id: str
) -> DocumentModel:
    """Return an authorized document only when it belongs to the path KB."""
    get_authorized_knowledge_base_or_404(db, knowledge_base_id, user_id)
    document = _get_document_or_404(db, document_id)
    if document.knowledge_base_id != knowledge_base_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


def _delete_document_dependencies(db: Session, document_id: str) -> None:
    """Remove rows that hold foreign keys to a document or its chunks/runs."""
    db.execute(delete(CitationModel).where(CitationModel.document_id == document_id))
    db.execute(
        delete(DocumentParseArtifactModel).where(
            DocumentParseArtifactModel.document_id == document_id
        )
    )
    db.execute(
        delete(StructuredKnowledgeEntityModel).where(
            StructuredKnowledgeEntityModel.document_id == document_id
        )
    )
    db.execute(
        delete(DocumentMetadataProfileModel).where(
            DocumentMetadataProfileModel.document_id == document_id
        )
    )
    db.execute(
        delete(EntityRelationshipModel).where(EntityRelationshipModel.document_id == document_id)
    )
    db.execute(delete(EntityMentionModel).where(EntityMentionModel.document_id == document_id))
    db.execute(delete(DocumentChunkModel).where(DocumentChunkModel.document_id == document_id))
    db.execute(delete(ExtractionRunModel).where(ExtractionRunModel.document_id == document_id))
    db.execute(
        delete(DocumentPermissionModel).where(DocumentPermissionModel.document_id == document_id)
    )


def _add_parse_artifact_for_upload(
    db: Session,
    *,
    document: DocumentModel,
    parsed: ParsedDocumentUpload,
    source_filename: str | None,
) -> None:
    artifact = parsed.parse_artifact
    if artifact is None:
        return
    db.add(
        DocumentParseArtifactModel(
            document_id=document.id,
            source_sha256=parsed.sha256,
            source_filename=source_filename,
            source_content_type=parsed.source_content_type,
            source_type=parsed.source_type,
            parser_provider=artifact.parser_provider,
            parser_name=artifact.parser_name,
            parser_version=artifact.parser_version,
            parser_mode=artifact.parser_mode,
            markdown_content=artifact.markdown_content,
            elements_json=json.dumps(artifact.elements, ensure_ascii=False, sort_keys=True),
            warnings_json=json.dumps(artifact.warnings, ensure_ascii=False, sort_keys=True),
        )
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
