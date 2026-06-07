"""Group and membership API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.groups.models import GroupModel, MembershipModel, MembershipRole
from my_agents.groups.schemas import (
    GroupCreateRequest,
    GroupResponse,
    MemberPatchRequest,
    MemberUpsertRequest,
)
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    DocumentModel,
    KnowledgeBaseModel,
    KnowledgeBasePublicationModel,
    KnowledgeBasePurpose,
    KnowledgeBaseScope,
    KnowledgePublishRequestModel,
    KnowledgePublishRequestStatus,
)
from my_agents.knowledge.schemas import (
    KnowledgePublishRequestCreateRequest,
    KnowledgePublishRequestResponse,
)
from my_agents.persistence.database import get_database_session

groups_router = APIRouter(prefix="/groups", tags=["groups"])


@groups_router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    request: GroupCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> GroupResponse:
    group_name = request.name.strip()
    group = GroupModel(name=group_name, created_by_user_id=principal.user_id)
    db.add(group)
    db.flush()
    membership = MembershipModel(
        group_id=group.id,
        user_id=principal.user_id,
        role=MembershipRole.OWNER.value,
    )
    db.add(membership)
    db.add(
        KnowledgeBaseModel(
            name=_default_group_knowledge_base_name(group_name),
            scope=KnowledgeBaseScope.GROUP.value,
            owner_user_id=principal.user_id,
            group_id=group.id,
        )
    )
    db.commit()
    db.refresh(group)
    return GroupResponse(id=group.id, name=group.name, role=MembershipRole.OWNER)


@groups_router.get("", response_model=list[GroupResponse])
def list_groups(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[GroupResponse]:
    rows = db.execute(
        select(GroupModel, MembershipModel)
        .join(MembershipModel)
        .where(MembershipModel.user_id == principal.user_id)
    ).all()
    return [
        GroupResponse(id=group.id, name=group.name, role=MembershipRole(membership.role))
        for group, membership in rows
    ]


@groups_router.get("/{group_id}", response_model=GroupResponse)
def get_group(
    group_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> GroupResponse:
    group, membership = _get_group_and_membership(db, group_id, principal.user_id)
    return GroupResponse(id=group.id, name=group.name, role=MembershipRole(membership.role))


@groups_router.post("/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_member(
    group_id: str,
    request: MemberUpsertRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> None:
    _require_group_manager(db, group_id, principal.user_id)
    existing = db.scalar(
        select(MembershipModel).where(
            MembershipModel.group_id == group_id,
            MembershipModel.user_id == request.user_id,
        )
    )
    if existing is None:
        db.add(
            MembershipModel(
                group_id=group_id,
                user_id=request.user_id,
                role=request.role.value,
            )
        )
    else:
        existing.role = request.role.value
        db.add(existing)
    db.commit()


@groups_router.patch("/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_member(
    group_id: str,
    user_id: str,
    request: MemberPatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> None:
    _require_group_manager(db, group_id, principal.user_id)
    membership = db.scalar(
        select(MembershipModel).where(
            MembershipModel.group_id == group_id,
            MembershipModel.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")
    membership.role = request.role.value
    db.add(membership)
    db.commit()


@groups_router.post(
    "/{group_id}/publish-requests",
    response_model=KnowledgePublishRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_publish_request(
    group_id: str,
    request: KnowledgePublishRequestCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> KnowledgePublishRequestResponse:
    """Request owner/admin review before sharing personal knowledge with a group."""
    _get_group_and_membership(db, group_id, principal.user_id)
    source_document_id: str | None = None
    target_knowledge_base_id: str | None = None
    source_knowledge_base_id: str | None = None
    if request.source_document_id is not None:
        source_document = _get_owned_personal_source_document(
            db,
            document_id=request.source_document_id,
            requester_user_id=principal.user_id,
        )
        target_knowledge_base = _get_target_group_knowledge_base(
            db,
            knowledge_base_id=request.target_knowledge_base_id or "",
            group_id=group_id,
        )
        source_document_id = source_document.id
        target_knowledge_base_id = target_knowledge_base.id
    else:
        source_knowledge_base = _get_owned_personal_source_knowledge_base(
            db,
            knowledge_base_id=request.source_knowledge_base_id or "",
            requester_user_id=principal.user_id,
        )
        _require_publishable_personal_knowledge_base_request(
            db,
            group_id=group_id,
            knowledge_base_id=source_knowledge_base.id,
        )
        source_knowledge_base_id = source_knowledge_base.id

    publish_request = KnowledgePublishRequestModel(
        requester_user_id=principal.user_id,
        target_group_id=group_id,
        target_knowledge_base_id=target_knowledge_base_id,
        source_document_id=source_document_id,
        source_knowledge_base_id=source_knowledge_base_id,
        status=KnowledgePublishRequestStatus.PENDING.value,
    )
    db.add(publish_request)
    db.commit()
    db.refresh(publish_request)
    return _publish_request_response(publish_request)


@groups_router.get(
    "/{group_id}/publish-requests",
    response_model=list[KnowledgePublishRequestResponse],
)
def list_publish_requests(
    group_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[KnowledgePublishRequestResponse]:
    """List publish requests, scoped to manager visibility or the requester's own rows."""
    _, membership = _get_group_and_membership(db, group_id, principal.user_id)
    statement = select(KnowledgePublishRequestModel).where(
        KnowledgePublishRequestModel.target_group_id == group_id
    )
    if membership.role not in (MembershipRole.OWNER.value, MembershipRole.ADMIN.value):
        statement = statement.where(
            KnowledgePublishRequestModel.requester_user_id == principal.user_id
        )
    publish_requests = db.scalars(statement.order_by(KnowledgePublishRequestModel.created_at)).all()
    return [_publish_request_response(publish_request) for publish_request in publish_requests]


@groups_router.post(
    "/{group_id}/publish-requests/{request_id}/approve",
    response_model=KnowledgePublishRequestResponse,
)
def approve_publish_request(
    group_id: str,
    request_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> KnowledgePublishRequestResponse:
    """Approve a document copy or personal-KB publication for group retrieval."""
    _require_group_manager(db, group_id, principal.user_id)
    publish_request = _get_publish_request_or_404(db, group_id=group_id, request_id=request_id)
    _require_pending_publish_request(publish_request)
    if publish_request.source_knowledge_base_id is not None:
        return _approve_knowledge_base_publish_request(
            db,
            publish_request=publish_request,
            reviewer_user_id=principal.user_id,
        )

    source_document = db.get(DocumentModel, publish_request.source_document_id)
    if source_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="source document not found",
        )
    target_knowledge_base = _get_target_group_knowledge_base(
        db,
        knowledge_base_id=publish_request.target_knowledge_base_id or "",
        group_id=group_id,
    )
    published_document = _copy_document_for_group_knowledge_base(
        source_document=source_document,
        target_knowledge_base=target_knowledge_base,
        requester_user_id=publish_request.requester_user_id,
    )
    db.add(published_document)
    db.flush()
    publish_request.status = KnowledgePublishRequestStatus.APPROVED.value
    publish_request.reviewer_user_id = principal.user_id
    publish_request.published_document_id = published_document.id
    publish_request.reviewed_at = datetime.now(UTC)
    db.add(publish_request)
    db.commit()
    db.refresh(publish_request)
    KnowledgeExtractionService(db).ingest_document(published_document)
    db.refresh(publish_request)
    return _publish_request_response(publish_request)


@groups_router.post(
    "/{group_id}/publish-requests/{request_id}/reject",
    response_model=KnowledgePublishRequestResponse,
)
def reject_publish_request(
    group_id: str,
    request_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> KnowledgePublishRequestResponse:
    """Reject without copying content, leaving retrieval scope unchanged."""
    _require_group_manager(db, group_id, principal.user_id)
    publish_request = _get_publish_request_or_404(db, group_id=group_id, request_id=request_id)
    _require_pending_publish_request(publish_request)
    publish_request.status = KnowledgePublishRequestStatus.REJECTED.value
    publish_request.reviewer_user_id = principal.user_id
    publish_request.reviewed_at = datetime.now(UTC)
    db.add(publish_request)
    db.commit()
    db.refresh(publish_request)
    return _publish_request_response(publish_request)


def _get_group_and_membership(
    db: Session, group_id: str, user_id: str
) -> tuple[GroupModel, MembershipModel]:
    row = db.execute(
        select(GroupModel, MembershipModel)
        .join(MembershipModel)
        .where(
            GroupModel.id == group_id,
            MembershipModel.user_id == user_id,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    return row


def _require_group_manager(db: Session, group_id: str, user_id: str) -> MembershipModel:
    _, membership = _get_group_and_membership(db, group_id, user_id)
    if membership.role not in (MembershipRole.OWNER.value, MembershipRole.ADMIN.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")
    return membership


def _get_owned_personal_source_document(
    db: Session, *, document_id: str, requester_user_id: str
) -> DocumentModel:
    source_document = db.get(DocumentModel, document_id)
    if source_document is None or source_document.owner_user_id != requester_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    if source_document.group_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source document must be personal",
        )
    return source_document


def _get_owned_personal_source_knowledge_base(
    db: Session, *, knowledge_base_id: str, requester_user_id: str
) -> KnowledgeBaseModel:
    source_knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if source_knowledge_base is None or source_knowledge_base.owner_user_id != requester_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if (
        source_knowledge_base.scope != KnowledgeBaseScope.PERSONAL.value
        or source_knowledge_base.group_id is not None
        or source_knowledge_base.purpose != KnowledgeBasePurpose.STANDARD.value
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source knowledge base must be personal",
        )
    return source_knowledge_base


def _require_publishable_personal_knowledge_base_request(
    db: Session, *, group_id: str, knowledge_base_id: str, exclude_request_id: str | None = None
) -> None:
    existing_publication = db.scalar(
        select(KnowledgeBasePublicationModel).where(
            KnowledgeBasePublicationModel.group_id == group_id,
            KnowledgeBasePublicationModel.knowledge_base_id == knowledge_base_id,
        )
    )
    if existing_publication is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="knowledge base already published to group",
        )
    pending_statement = select(KnowledgePublishRequestModel).where(
        KnowledgePublishRequestModel.target_group_id == group_id,
        KnowledgePublishRequestModel.source_knowledge_base_id == knowledge_base_id,
        KnowledgePublishRequestModel.status == KnowledgePublishRequestStatus.PENDING.value,
    )
    if exclude_request_id is not None:
        pending_statement = pending_statement.where(
            KnowledgePublishRequestModel.id != exclude_request_id
        )
    existing_pending_request = db.scalar(pending_statement)
    if existing_pending_request is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="knowledge base publish request already pending",
        )


def _get_target_group_knowledge_base(
    db: Session, *, knowledge_base_id: str, group_id: str
) -> KnowledgeBaseModel:
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if (
        knowledge_base is None
        or knowledge_base.scope != KnowledgeBaseScope.GROUP.value
        or knowledge_base.group_id != group_id
        or knowledge_base.purpose != KnowledgeBasePurpose.STANDARD.value
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="target knowledge base must belong to group",
        )
    return knowledge_base


def _get_publish_request_or_404(
    db: Session, *, group_id: str, request_id: str
) -> KnowledgePublishRequestModel:
    publish_request = db.get(KnowledgePublishRequestModel, request_id)
    if publish_request is None or publish_request.target_group_id != group_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="publish request not found",
        )
    return publish_request


def _require_pending_publish_request(publish_request: KnowledgePublishRequestModel) -> None:
    if publish_request.status != KnowledgePublishRequestStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="publish request already reviewed",
        )


def _copy_document_for_group_knowledge_base(
    *,
    source_document: DocumentModel,
    target_knowledge_base: KnowledgeBaseModel,
    requester_user_id: str,
) -> DocumentModel:
    return DocumentModel(
        title=source_document.title,
        content=source_document.content,
        source_type=source_document.source_type,
        source_filename=source_document.source_filename,
        source_content_type=source_document.source_content_type,
        source_byte_size=source_document.source_byte_size,
        source_sha256=source_document.source_sha256,
        source_page_count=source_document.source_page_count,
        parser_name=source_document.parser_name,
        owner_user_id=requester_user_id,
        group_id=target_knowledge_base.group_id,
        knowledge_base_id=target_knowledge_base.id,
    )


def _approve_knowledge_base_publish_request(
    db: Session,
    *,
    publish_request: KnowledgePublishRequestModel,
    reviewer_user_id: str,
) -> KnowledgePublishRequestResponse:
    source_knowledge_base = db.get(KnowledgeBaseModel, publish_request.source_knowledge_base_id)
    if source_knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="source knowledge base not found",
        )
    if (
        source_knowledge_base.scope != KnowledgeBaseScope.PERSONAL.value
        or source_knowledge_base.group_id is not None
        or source_knowledge_base.purpose != KnowledgeBasePurpose.STANDARD.value
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source knowledge base must be personal",
        )
    _require_publishable_personal_knowledge_base_request(
        db,
        group_id=publish_request.target_group_id,
        knowledge_base_id=source_knowledge_base.id,
        exclude_request_id=publish_request.id,
    )
    publication = KnowledgeBasePublicationModel(
        group_id=publish_request.target_group_id,
        knowledge_base_id=source_knowledge_base.id,
        requester_user_id=publish_request.requester_user_id,
        approved_by_user_id=reviewer_user_id,
        publish_request_id=publish_request.id,
    )
    db.add(publication)
    db.flush()
    publish_request.status = KnowledgePublishRequestStatus.APPROVED.value
    publish_request.reviewer_user_id = reviewer_user_id
    publish_request.published_knowledge_base_id = source_knowledge_base.id
    publish_request.reviewed_at = datetime.now(UTC)
    db.add(publish_request)
    db.commit()
    db.refresh(publish_request)
    return _publish_request_response(publish_request)


def _default_group_knowledge_base_name(group_name: str) -> str:
    return f"{group_name} Knowledge"


def _publish_request_response(
    publish_request: KnowledgePublishRequestModel,
) -> KnowledgePublishRequestResponse:
    return KnowledgePublishRequestResponse(
        id=publish_request.id,
        requester_user_id=publish_request.requester_user_id,
        target_group_id=publish_request.target_group_id,
        target_knowledge_base_id=publish_request.target_knowledge_base_id,
        source_document_id=publish_request.source_document_id,
        source_knowledge_base_id=publish_request.source_knowledge_base_id,
        status=KnowledgePublishRequestStatus(publish_request.status),
        reviewer_user_id=publish_request.reviewer_user_id,
        published_document_id=publish_request.published_document_id,
        published_knowledge_base_id=publish_request.published_knowledge_base_id,
        created_at=publish_request.created_at,
        reviewed_at=publish_request.reviewed_at,
    )
