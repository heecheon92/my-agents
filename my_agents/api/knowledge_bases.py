"""Knowledge-base API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.api.documents import (
    create_document_in_knowledge_base,
    get_extraction_run_in_knowledge_base,
    ingest_document_async_in_knowledge_base,
    ingest_document_in_knowledge_base,
    list_documents_in_knowledge_base,
    list_extraction_runs_in_knowledge_base,
    upload_document_in_knowledge_base,
)
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.groups.models import MembershipModel, MembershipRole
from my_agents.knowledge.auth import (
    get_authorized_knowledge_base_or_404,
    retrievable_knowledge_base_filter,
)
from my_agents.knowledge.models import (
    KnowledgeBaseModel,
    KnowledgeBasePublicationModel,
    KnowledgeBasePurpose,
    KnowledgeBaseScope,
)
from my_agents.knowledge.schemas import (
    DocumentCreateRequest,
    DocumentResponse,
    ExtractionRunResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
)
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

knowledge_bases_router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@knowledge_bases_router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base(
    request: KnowledgeBaseCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> KnowledgeBaseResponse:
    if request.scope == KnowledgeBaseScope.GROUP:
        if request.group_id is None or not _has_group_manager_access(
            db, request.group_id, principal.user_id
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")
        group_id = request.group_id
    else:
        group_id = None
    knowledge_base = KnowledgeBaseModel(
        name=request.name.strip(),
        scope=request.scope.value,
        owner_user_id=principal.user_id,
        group_id=group_id,
    )
    db.add(knowledge_base)
    db.commit()
    db.refresh(knowledge_base)
    return _knowledge_base_response(db, knowledge_base, user_id=principal.user_id)


@knowledge_bases_router.get("", response_model=list[KnowledgeBaseResponse])
def list_knowledge_bases(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[KnowledgeBaseResponse]:
    knowledge_bases = db.scalars(
        select(KnowledgeBaseModel).where(retrievable_knowledge_base_filter(principal.user_id))
    ).all()
    return [_knowledge_base_response(db, kb, user_id=principal.user_id) for kb in knowledge_bases]


@knowledge_bases_router.post(
    "/team-upload-staging",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Ensure Group Upload Staging Knowledge Base",
)
def ensure_team_upload_staging_knowledge_base(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> KnowledgeBaseResponse:
    """Return the user's hidden personal staging KB for group document publication.

    Staging KBs are writable by direct ID, but excluded from ordinary KB lists,
    chat source selection, and retrieval. Approved publish requests copy their
    source document into a group KB, and only that group copy is ingested for
    normal group retrieval.
    """
    knowledge_base = db.scalar(
        select(KnowledgeBaseModel).where(
            KnowledgeBaseModel.owner_user_id == principal.user_id,
            KnowledgeBaseModel.scope == KnowledgeBaseScope.PERSONAL.value,
            KnowledgeBaseModel.group_id.is_(None),
            KnowledgeBaseModel.purpose == KnowledgeBasePurpose.TEAM_UPLOAD_STAGING.value,
        )
    )
    if knowledge_base is None:
        knowledge_base = KnowledgeBaseModel(
            name="Group upload staging",
            scope=KnowledgeBaseScope.PERSONAL.value,
            owner_user_id=principal.user_id,
            group_id=None,
            purpose=KnowledgeBasePurpose.TEAM_UPLOAD_STAGING.value,
        )
        db.add(knowledge_base)
        db.commit()
        db.refresh(knowledge_base)
    return _knowledge_base_response(db, knowledge_base, user_id=principal.user_id)


@knowledge_bases_router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
def get_knowledge_base(
    knowledge_base_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> KnowledgeBaseResponse:
    knowledge_base = get_authorized_knowledge_base_or_404(db, knowledge_base_id, principal.user_id)
    return _knowledge_base_response(db, knowledge_base, user_id=principal.user_id)


@knowledge_bases_router.get("/{knowledge_base_id}/documents", response_model=list[DocumentResponse])
def list_knowledge_base_documents(
    knowledge_base_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[DocumentResponse]:
    return list_documents_in_knowledge_base(
        db=db,
        knowledge_base_id=knowledge_base_id,
        principal=principal,
    )


@knowledge_bases_router.post(
    "/{knowledge_base_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base_document(
    knowledge_base_id: str,
    request: DocumentCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentResponse:
    return create_document_in_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        request=request,
        principal=principal,
        db=db,
        settings=settings,
    )


@knowledge_bases_router.post(
    "/{knowledge_base_id}/documents/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_knowledge_base_document(
    knowledge_base_id: str,
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
) -> DocumentResponse:
    return await upload_document_in_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        principal=principal,
        db=db,
        settings=settings,
        title=title,
        file=file,
    )


@knowledge_bases_router.post(
    "/{knowledge_base_id}/documents/{document_id}/ingest",
    response_model=ExtractionRunResponse,
)
def ingest_knowledge_base_document(
    knowledge_base_id: str,
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ExtractionRunResponse:
    return ingest_document_in_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        principal=principal,
        db=db,
    )


@knowledge_bases_router.post(
    "/{knowledge_base_id}/documents/{document_id}/ingest/async",
    response_model=ExtractionRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_knowledge_base_document_async(
    knowledge_base_id: str,
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExtractionRunResponse:
    return ingest_document_async_in_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        principal=principal,
        db=db,
        settings=settings,
    )


@knowledge_bases_router.get(
    "/{knowledge_base_id}/documents/{document_id}/extraction-runs",
    response_model=list[ExtractionRunResponse],
)
def list_knowledge_base_document_extraction_runs(
    knowledge_base_id: str,
    document_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[ExtractionRunResponse]:
    return list_extraction_runs_in_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        principal=principal,
        db=db,
    )


@knowledge_bases_router.get(
    "/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}",
    response_model=ExtractionRunResponse,
)
def get_knowledge_base_document_extraction_run(
    knowledge_base_id: str,
    document_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ExtractionRunResponse:
    return get_extraction_run_in_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        run_id=run_id,
        principal=principal,
        db=db,
    )


def _has_group_manager_access(db: Session, group_id: str, user_id: str) -> bool:
    membership = db.scalar(
        select(MembershipModel).where(
            MembershipModel.group_id == group_id,
            MembershipModel.user_id == user_id,
        )
    )
    return membership is not None and membership.role in {
        MembershipRole.OWNER.value,
        MembershipRole.ADMIN.value,
    }


def _knowledge_base_response(
    db: Session, knowledge_base: KnowledgeBaseModel, *, user_id: str
) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=knowledge_base.id,
        name=knowledge_base.name,
        scope=KnowledgeBaseScope(knowledge_base.scope),
        owner_user_id=knowledge_base.owner_user_id,
        group_id=knowledge_base.group_id,
        purpose=KnowledgeBasePurpose(knowledge_base.purpose),
        published_group_ids=_published_group_ids_for_user(
            db,
            knowledge_base_id=knowledge_base.id,
            user_id=user_id,
        ),
    )


def _published_group_ids_for_user(
    db: Session, *, knowledge_base_id: str, user_id: str
) -> list[str]:
    return list(
        db.scalars(
            select(KnowledgeBasePublicationModel.group_id)
            .join(
                MembershipModel, MembershipModel.group_id == KnowledgeBasePublicationModel.group_id
            )
            .where(
                KnowledgeBasePublicationModel.knowledge_base_id == knowledge_base_id,
                MembershipModel.user_id == user_id,
            )
            .order_by(KnowledgeBasePublicationModel.created_at, KnowledgeBasePublicationModel.id)
        ).all()
    )
