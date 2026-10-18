"""Permission-checked private originals and short-lived direct upload admissions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from my_agents.api.documents import (
    _add_parse_artifact_for_upload,
    _can_ingest_document,
    _can_read_document,
    _dispatch_extraction_run,
    _document_response,
    _extraction_run_response,
    _get_document_or_404,
)
from my_agents.api.errors import APIErrorCode, APIHTTPException, document_upload_error_code
from my_agents.assistant_preferences import document_ingestion_mode
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_create_document
from my_agents.knowledge.auth import require_document_writable_knowledge_base
from my_agents.knowledge.file_schemas import (
    UPLOAD_MAX_BYTES,
    DocumentOriginalDownload,
    DocumentReprocessRequest,
    DocumentUploadCompleted,
    DocumentUploadInitiate,
    DocumentUploadReceipt,
    IngestionMode,
)
from my_agents.knowledge.file_upload_models import DocumentUploadSessionModel
from my_agents.knowledge.models import DocumentModel, ExtractionRunModel, KnowledgeBasePurpose
from my_agents.knowledge.pdf_uploads import DoclingExtractionConfig, TesseractOcrConfig
from my_agents.knowledge.schemas import ExtractionRunResponse
from my_agents.knowledge.uploads import (
    DocumentUploadError,
    ParsedDocumentUpload,
    UnsupportedDocumentUploadError,
    parse_uploaded_document,
)
from my_agents.persistence.database import get_database_session, supports_background_sessions
from my_agents.settings import Settings, get_settings
from my_agents.storage import (
    ObjectStore,
    StorageObjectNotFound,
    StorageObjectTooLarge,
    StorageUnavailable,
    get_object_store,
)
from my_agents.storage.lifecycle import (
    enqueue_object_cleanup,
    mark_file_ready,
    reserve_file,
    schedule_file_cleanup,
)
from my_agents.storage.models import StoredFileModel


def _private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


file_router = APIRouter(tags=["document-files"], dependencies=[Depends(_private_response)])
Db = Annotated[Session, Depends(get_database_session)]
User = Annotated[Principal, Depends(get_current_principal)]
Config = Annotated[Settings, Depends(get_settings)]


def object_store(settings: Config, principal: User, db: Db) -> ObjectStore:
    _registered(db, principal)
    if not supports_background_sessions(settings.database_url):
        raise StorageUnavailable("Private originals require a persistent application database.")
    return get_object_store(settings)


Store = Annotated[ObjectStore, Depends(object_store)]


def storage_configured(settings: Settings) -> bool:
    return bool(
        settings.storage_provider == "r2"
        and supports_background_sessions(settings.database_url)
        and settings.r2_endpoint_url
        and settings.r2_bucket_name
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
    )


def direct_uploads_available(settings: Settings) -> bool:
    return (
        storage_configured(settings)
        and settings.storage_direct_uploads_enabled
        and settings.deployment_environment != "preview"
    )


def ai_ingestion_available(settings: Settings) -> bool:
    return settings.response_mode == "deterministic" or bool(settings.openai_api_key)


def selected_ingestion_mode(
    db: Session, principal: Principal, settings: Settings, explicit: IngestionMode | None
) -> IngestionMode:
    mode = explicit or document_ingestion_mode(db, principal)
    if mode == "ai_assisted":
        if principal.is_guest:
            raise APIHTTPException(
                status_code=403,
                detail="Guests cannot use AI-assisted ingestion.",
                code=APIErrorCode.PERMISSION_DENIED,
            )
        if not ai_ingestion_available(settings):
            raise APIHTTPException(
                status_code=503,
                detail="AI-assisted ingestion is unavailable.",
                code=APIErrorCode.DOCUMENT_PROCESSING_UNAVAILABLE,
            )
    return mode


def _registered(db: Session, principal: Principal) -> None:
    assert_guest_access_active(db, principal)
    if principal.is_guest:
        raise APIHTTPException(
            status_code=403,
            detail="Private file storage requires an account.",
            code=APIErrorCode.PERMISSION_DENIED,
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _error(status: int, code: APIErrorCode, detail: str) -> APIHTTPException:
    return APIHTTPException(status_code=status, code=code, detail=detail)


def _session(
    db: Session, principal: Principal, kb_id: str, upload_id: str
) -> DocumentUploadSessionModel:
    _registered(db, principal)
    require_document_writable_knowledge_base(
        db, knowledge_base_id=kb_id, principal=principal, allow_system_management=True
    )
    admission = db.scalar(
        select(DocumentUploadSessionModel).where(
            DocumentUploadSessionModel.id == upload_id,
            DocumentUploadSessionModel.owner_user_id == principal.user_id,
            DocumentUploadSessionModel.knowledge_base_id == kb_id,
        )
    )
    if admission is None:
        raise HTTPException(404, "Upload not found.")
    return admission


def _pending(admission: DocumentUploadSessionModel) -> None:
    if admission.status == "aborted" or _utc(admission.expires_at) <= datetime.now(UTC):
        raise _error(410, APIErrorCode.UPLOAD_EXPIRED, "This upload has expired or was cancelled.")
    if admission.status != "pending":
        raise _error(
            409, APIErrorCode.UPLOAD_IN_PROGRESS, "This upload is already being finalized."
        )


def _receipt(
    db: Session, admission: DocumentUploadSessionModel, store: ObjectStore, settings: Settings
) -> DocumentUploadReceipt:
    now = datetime.now(UTC)
    locked = db.execute(
        update(DocumentUploadSessionModel)
        .where(
            DocumentUploadSessionModel.id == admission.id,
            DocumentUploadSessionModel.status == "pending",
            DocumentUploadSessionModel.expires_at > now,
        )
        .values(status="pending")
        .execution_options(synchronize_session=False)
    ).rowcount
    if not locked:
        raise _error(410, APIErrorCode.UPLOAD_EXPIRED, "This upload is no longer available.")
    original = db.get(StoredFileModel, admission.stored_file_id)
    assert original is not None
    if original.bucket != settings.r2_bucket_name:
        raise StorageUnavailable("The upload bucket is no longer configured.")
    if original.status != "reserved":
        raise _error(410, APIErrorCode.UPLOAD_EXPIRED, "This upload is no longer available.")
    ttl = int(min(600, (_utc(admission.expires_at) - now).total_seconds()))
    if ttl < 1:
        raise _error(410, APIErrorCode.UPLOAD_EXPIRED, "This upload has expired.")
    admission.signed_until = datetime.now(UTC) + timedelta(seconds=ttl)
    # Expire staging after the admission's absolute deadline, including refreshed URLs.
    enqueue_object_cleanup(
        db,
        bucket=original.bucket,
        object_key=admission.staging_key,
        stored_file_id=admission.staging_file_id,
        not_before=_utc(admission.expires_at) + timedelta(minutes=2),
    )
    url = store.presign_put(
        admission.staging_key,
        content_type=original.content_type,
        content_length=original.byte_size,
        expires_seconds=ttl,
    )
    db.commit()
    return DocumentUploadReceipt(
        upload_id=admission.id,
        url=url,
        headers={"Content-Type": original.content_type},
        expires_at=admission.signed_until,
    )


@file_router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/uploads",
    response_model=DocumentUploadReceipt,
    status_code=201,
)
def initiate_upload(
    knowledge_base_id: str,
    request: DocumentUploadInitiate,
    principal: User,
    db: Db,
    settings: Config,
    store: Store,
) -> DocumentUploadReceipt:
    _registered(db, principal)
    if not direct_uploads_available(settings):
        raise _error(503, APIErrorCode.STORAGE_UNAVAILABLE, "Direct uploads are unavailable.")
    assert_guest_can_create_document(db, principal, settings)
    require_document_writable_knowledge_base(
        db, knowledge_base_id=knowledge_base_id, principal=principal, allow_system_management=True
    )
    mode = selected_ingestion_mode(db, principal, settings, request.ingestion_mode)
    original = reserve_file(
        db,
        settings,
        owner_user_id=principal.user_id,
        byte_size=request.byte_size,
        sha256=request.sha256,
        filename=request.filename,
        content_type=request.content_type,
    )
    staging = reserve_file(
        db,
        settings,
        owner_user_id=principal.user_id,
        byte_size=request.byte_size,
        sha256=request.sha256,
        filename=request.filename,
        content_type=request.content_type,
    )
    staging.object_key = f"staging/{staging.id}"
    db.flush()
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    schedule_file_cleanup(db, staging, not_before=expires_at + timedelta(minutes=2))
    admission = DocumentUploadSessionModel(
        id=str(uuid.uuid4()),
        owner_user_id=principal.user_id,
        knowledge_base_id=knowledge_base_id,
        stored_file_id=original.id,
        staging_file_id=staging.id,
        title=request.title.strip(),
        ingestion_mode=mode,
        staging_key=staging.object_key,
        status="pending",
        expires_at=expires_at,
        signed_until=datetime.now(UTC),
    )
    db.add(admission)
    db.flush()
    return _receipt(db, admission, store, settings)


@file_router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/uploads/{upload_id}/refresh",
    response_model=DocumentUploadReceipt,
)
def refresh_upload(
    knowledge_base_id: str, upload_id: str, principal: User, db: Db, settings: Config, store: Store
) -> DocumentUploadReceipt:
    admission = _session(db, principal, knowledge_base_id, upload_id)
    _pending(admission)
    return _receipt(db, admission, store, settings)


def parse_original(
    original: StoredFileModel, content: bytes, settings: Settings
) -> ParsedDocumentUpload:
    try:
        return parse_uploaded_document(
            filename=original.filename,
            content_type=original.content_type,
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
        unsupported = isinstance(exc, UnsupportedDocumentUploadError)
        raise _error(
            415 if unsupported else 400,
            document_upload_error_code(str(exc), unsupported_media_type=unsupported),
            str(exc),
        ) from exc


def _candidate_run(
    db: Session, document: DocumentModel, parsed: ParsedDocumentUpload, mode: str
) -> ExtractionRunModel:
    run = ExtractionRunModel(
        document_id=document.id,
        status="pending",
        stage="queued",
        ingestion_mode=mode,
        candidate_content=parsed.content,
        candidate_elements_json=json.dumps(parsed.parse_artifact.elements)
        if parsed.parse_artifact
        else None,
        progress_percent=0,
        chunk_count=0,
        entity_count=0,
        relationship_count=0,
    )
    db.add(run)
    db.flush()
    return run


def _completed(db: Session, admission: DocumentUploadSessionModel) -> DocumentUploadCompleted:
    document = db.get(DocumentModel, admission.document_id)
    if document is None:
        raise HTTPException(404, "Document no longer exists.")
    run = (
        db.get(ExtractionRunModel, admission.extraction_run_id)
        if admission.extraction_run_id
        else None
    )
    return DocumentUploadCompleted(
        document=_document_response(document),
        extraction_run=_extraction_run_response(db, run) if run else None,
    )


@file_router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/uploads/{upload_id}/complete",
    response_model=DocumentUploadCompleted,
    status_code=201,
)
def complete_upload(
    knowledge_base_id: str, upload_id: str, principal: User, db: Db, settings: Config, store: Store
) -> DocumentUploadCompleted:
    admission = _session(db, principal, knowledge_base_id, upload_id)
    if admission.status == "completed":
        return _completed(db, admission)
    _pending(admission)
    claimed = db.execute(
        update(DocumentUploadSessionModel)
        .where(
            DocumentUploadSessionModel.id == admission.id,
            DocumentUploadSessionModel.status == "pending",
        )
        .values(status="verifying")
    ).rowcount
    if not claimed:
        db.rollback()
        raise _error(409, APIErrorCode.UPLOAD_IN_PROGRESS, "Upload finalization is in progress.")
    db.commit()
    original = db.get(StoredFileModel, admission.stored_file_id)
    assert original is not None
    try:
        if original.bucket != settings.r2_bucket_name:
            raise StorageUnavailable("The upload bucket is no longer configured.")
        try:
            content = store.read(admission.staging_key, max_bytes=UPLOAD_MAX_BYTES)
        except StorageObjectNotFound as exc:
            raise _error(
                409, APIErrorCode.UPLOAD_NOT_READY, "The uploaded file is not available yet."
            ) from exc
        except StorageObjectTooLarge as exc:
            raise _error(
                400, APIErrorCode.UPLOAD_INTEGRITY_MISMATCH, "Uploaded file size does not match."
            ) from exc
        if (
            len(content) != original.byte_size
            or hashlib.sha256(content).hexdigest() != original.sha256
        ):
            raise _error(
                400,
                APIErrorCode.UPLOAD_INTEGRITY_MISMATCH,
                "Uploaded file failed integrity checks.",
            )
        parsed = parse_original(original, content, settings)
        kb = require_document_writable_knowledge_base(
            db,
            knowledge_base_id=knowledge_base_id,
            principal=principal,
            allow_system_management=True,
        )
        from my_agents.storage.lifecycle import lock_reserved_file

        lock_reserved_file(db, original)
        # Seal the verified bytes under a server-only key; never trust staging after this read.
        store.put(original.object_key, content, content_type=parsed.source_content_type)
        document = DocumentModel(
            title=admission.title,
            content=parsed.content,
            source_type=parsed.source_type,
            source_filename=original.filename,
            source_content_type=parsed.source_content_type,
            source_byte_size=parsed.byte_size,
            source_sha256=parsed.sha256,
            source_page_count=parsed.page_count,
            parser_name=parsed.parser_name,
            owner_user_id=principal.user_id,
            group_id=kb.group_id,
            knowledge_base_id=kb.id,
            original_file_id=original.id,
            ingestion_mode=admission.ingestion_mode,
        )
        db.add(document)
        db.flush()
        _add_parse_artifact_for_upload(
            db, document=document, parsed=parsed, source_filename=original.filename
        )
        run = (
            None
            if kb.purpose == KnowledgeBasePurpose.TEAM_UPLOAD_STAGING.value
            else _candidate_run(db, document, parsed, admission.ingestion_mode)
        )
        mark_file_ready(db, original)
        admission.status = "completed"
        admission.document_id = document.id
        admission.extraction_run_id = run.id if run else None
        db.commit()
    except Exception:
        db.rollback()
        db.execute(
            update(DocumentUploadSessionModel)
            .where(
                DocumentUploadSessionModel.id == admission.id,
                DocumentUploadSessionModel.status == "verifying",
            )
            .values(status="pending")
        )
        db.commit()
        raise
    response = _completed(db, admission)
    if run is not None:
        _dispatch_extraction_run(settings=settings, run_id=run.id)
    return response


@file_router.delete(
    "/knowledge-bases/{knowledge_base_id}/documents/uploads/{upload_id}", status_code=204
)
def abort_upload(knowledge_base_id: str, upload_id: str, principal: User, db: Db) -> Response:
    admission = _session(db, principal, knowledge_base_id, upload_id)
    if admission.status in {"verifying", "completed"}:
        raise _error(409, APIErrorCode.UPLOAD_IN_PROGRESS, "Upload has already been finalized.")
    changed = db.execute(
        update(DocumentUploadSessionModel)
        .where(
            DocumentUploadSessionModel.id == admission.id,
            DocumentUploadSessionModel.status == "pending",
        )
        .values(status="aborted")
    ).rowcount
    if changed:
        original = db.get(StoredFileModel, admission.stored_file_id)
        assert original is not None
        schedule_file_cleanup(db, original)
        # Cleanup staging AFTER every URL has expired, so late uploads cannot resurrect it.
        enqueue_object_cleanup(
            db,
            bucket=original.bucket,
            object_key=admission.staging_key,
            not_before=_utc(admission.expires_at) + timedelta(minutes=2),
        )
    elif admission.status != "aborted":
        db.rollback()
        raise _error(409, APIErrorCode.UPLOAD_IN_PROGRESS, "Upload finalization is in progress.")
    db.commit()
    return Response(status_code=204)


@file_router.get("/documents/{document_id}/original", response_model=DocumentOriginalDownload)
def original_download(
    document_id: str, principal: User, db: Db, settings: Config, store: Store
) -> DocumentOriginalDownload:
    _registered(db, principal)
    document = _get_document_or_404(db, document_id)
    if not _can_read_document(db, principal, document):
        raise HTTPException(404, "Document not found.")
    original = (
        db.get(StoredFileModel, document.original_file_id) if document.original_file_id else None
    )
    if original is None or original.status != "ready":
        raise _error(
            404, APIErrorCode.ORIGINAL_UNAVAILABLE, "This document has no retained original."
        )
    if original.bucket != settings.r2_bucket_name:
        raise StorageUnavailable("Original storage is not configured for this bucket.")
    return DocumentOriginalDownload(
        url=store.presign_get(original.object_key, filename=original.filename, expires_seconds=120),
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
        filename=original.filename,
    )


@file_router.post(
    "/documents/{document_id}/reprocess", response_model=ExtractionRunResponse, status_code=202
)
def reprocess_document(
    document_id: str,
    request: DocumentReprocessRequest,
    principal: User,
    db: Db,
    settings: Config,
    store: Store,
) -> ExtractionRunResponse:
    _registered(db, principal)
    document = _get_document_or_404(db, document_id)
    if not _can_read_document(db, principal, document):
        raise HTTPException(404, "Document not found.")
    if not _can_ingest_document(db, principal, document):
        raise HTTPException(403, "Not allowed.")
    original = (
        db.get(StoredFileModel, document.original_file_id) if document.original_file_id else None
    )
    if original is None or original.status != "ready":
        raise _error(
            404, APIErrorCode.ORIGINAL_UNAVAILABLE, "This document has no retained original."
        )
    if original.bucket != settings.r2_bucket_name:
        raise StorageUnavailable("Original storage is not configured for this bucket.")
    mode = selected_ingestion_mode(db, principal, settings, request.ingestion_mode)
    content = store.read(original.object_key, max_bytes=UPLOAD_MAX_BYTES)
    if hashlib.sha256(content).hexdigest() != original.sha256:
        raise _error(
            400, APIErrorCode.UPLOAD_INTEGRITY_MISMATCH, "Original file failed integrity checks."
        )
    parsed = parse_original(original, content, settings)
    # Serialize admission with all other indexing entry points.
    locked = db.execute(
        update(DocumentModel)
        .where(DocumentModel.id == document_id)
        .values(title=DocumentModel.title)
    ).rowcount
    if not locked:
        raise HTTPException(404, "Document not found.")
    db.refresh(document)
    if not _can_ingest_document(db, principal, document):
        raise HTTPException(403, "Not allowed.")
    active = db.scalar(
        select(ExtractionRunModel.id).where(
            ExtractionRunModel.document_id == document_id,
            ExtractionRunModel.status.in_(["pending", "running"]),
        )
    )
    if active:
        raise _error(
            409, APIErrorCode.REPROCESS_IN_PROGRESS, "This document is already being processed."
        )
    run = _candidate_run(db, document, parsed, mode)
    db.commit()
    response = _extraction_run_response(db, run)
    _dispatch_extraction_run(settings=settings, run_id=run.id)
    return response
