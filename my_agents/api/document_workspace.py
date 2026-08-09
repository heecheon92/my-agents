"""Authenticated API surface for temporary OpenAI-hosted document workspaces."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.errors import APIErrorCode, APIHTTPException
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.document_workspace.models import (
    ArtifactStatus,
    ConversationArtifactModel,
    ConversationAttachmentModel,
    DocumentWorkspaceModel,
    WorkspaceStatus,
)
from my_agents.document_workspace.provider import (
    DocumentWorkspaceProvider,
    DocumentWorkspaceProviderError,
    OpenAIDocumentWorkspaceProvider,
)
from my_agents.document_workspace.schemas import (
    ConversationArtifactResponse,
    ConversationAttachmentResponse,
    DocumentWorkspaceCapabilityResponse,
)
from my_agents.document_workspace.service import (
    artifact_response,
    artifacts_for_conversation,
    assert_document_workspace_access,
    attachment_response,
    attachments_for_conversation,
    capability_response,
    delete_attachment,
    upload_attachment,
)
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

document_workspace_router = APIRouter(tags=["document-workspace"])


def get_document_workspace_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentWorkspaceProvider | None:
    if not settings.document_workspace_enabled:
        return None
    return OpenAIDocumentWorkspaceProvider(settings)


@document_workspace_router.get(
    "/capabilities/document-workspace",
    response_model=DocumentWorkspaceCapabilityResponse,
)
def get_document_workspace_capability(
    principal: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentWorkspaceCapabilityResponse:
    return capability_response(settings=settings, principal=principal)


@document_workspace_router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=ConversationAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation_attachment(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    provider: Annotated[DocumentWorkspaceProvider | None, Depends(get_document_workspace_provider)],
    file: Annotated[UploadFile, File()],
    provider_consent: Annotated[bool, Form()],
) -> ConversationAttachmentResponse:
    get_authorized_conversation(db, conversation_id, principal.user_id)
    assert_document_workspace_access(settings=settings, principal=principal)
    if provider is None:
        raise RuntimeError("document workspace provider is unavailable")
    attachment = upload_attachment(
        db=db,
        provider=provider,
        settings=settings,
        principal=principal,
        conversation_id=conversation_id,
        upload=file,
        provider_consent=provider_consent,
    )
    return attachment_response(attachment)


@document_workspace_router.get(
    "/conversations/{conversation_id}/attachments",
    response_model=list[ConversationAttachmentResponse],
)
def list_conversation_attachments(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ConversationAttachmentResponse]:
    assert_document_workspace_access(settings=settings, principal=principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    return [
        attachment_response(item)
        for item in attachments_for_conversation(
            db,
            conversation_id=conversation_id,
            owner_user_id=principal.user_id,
        )
    ]


@document_workspace_router.delete(
    "/conversations/{conversation_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_conversation_attachment(
    conversation_id: str,
    attachment_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    provider: Annotated[DocumentWorkspaceProvider | None, Depends(get_document_workspace_provider)],
) -> Response:
    assert_document_workspace_access(settings=settings, principal=principal)
    if provider is None:
        raise RuntimeError("document workspace provider is unavailable")
    get_authorized_conversation(db, conversation_id, principal.user_id)
    attachment = db.get(ConversationAttachmentModel, attachment_id)
    if (
        attachment is None
        or attachment.conversation_id != conversation_id
        or attachment.owner_user_id != principal.user_id
    ):
        raise APIHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="attachment not found",
            code=APIErrorCode.ATTACHMENT_NOT_FOUND,
        )
    delete_attachment(db=db, provider=provider, attachment=attachment)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@document_workspace_router.get(
    "/conversations/{conversation_id}/artifacts",
    response_model=list[ConversationArtifactResponse],
)
def list_conversation_artifacts(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ConversationArtifactResponse]:
    assert_document_workspace_access(settings=settings, principal=principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    return [
        artifact_response(item)
        for item in artifacts_for_conversation(
            db,
            conversation_id=conversation_id,
            owner_user_id=principal.user_id,
        )
    ]


@document_workspace_router.get(
    "/conversations/{conversation_id}/artifacts/{artifact_id}/download",
)
def download_conversation_artifact(
    conversation_id: str,
    artifact_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    provider: Annotated[DocumentWorkspaceProvider | None, Depends(get_document_workspace_provider)],
) -> StreamingResponse:
    assert_document_workspace_access(settings=settings, principal=principal)
    if provider is None:
        raise RuntimeError("document workspace provider is unavailable")
    get_authorized_conversation(db, conversation_id, principal.user_id)
    artifact = db.get(ConversationArtifactModel, artifact_id)
    if (
        artifact is None
        or artifact.conversation_id != conversation_id
        or artifact.owner_user_id != principal.user_id
    ):
        raise APIHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="artifact not found",
            code=APIErrorCode.ARTIFACT_NOT_FOUND,
        )
    serialized = artifact_response(artifact)
    if serialized.status != ArtifactStatus.AVAILABLE.value:
        db.commit()
        raise APIHTTPException(
            status_code=status.HTTP_410_GONE,
            detail="artifact has expired",
            code=APIErrorCode.ARTIFACT_EXPIRED,
        )
    workspace = db.get(DocumentWorkspaceModel, artifact.workspace_id)
    if (
        workspace is None
        or workspace.status != WorkspaceStatus.ACTIVE.value
        or not workspace.provider_container_id
    ):
        raise APIHTTPException(
            status_code=status.HTTP_410_GONE,
            detail="artifact workspace has expired",
            code=APIErrorCode.ARTIFACT_EXPIRED,
        )
    try:
        chunks = provider.download_container_file(
            container_id=workspace.provider_container_id,
            provider_file_id=artifact.provider_file_id,
        )
    except DocumentWorkspaceProviderError as exc:
        raise APIHTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="artifact download failed",
            code=APIErrorCode.ARTIFACT_DOWNLOAD_FAILED,
        ) from exc
    encoded_filename = quote(artifact.filename, safe="")
    return StreamingResponse(
        chunks,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )
