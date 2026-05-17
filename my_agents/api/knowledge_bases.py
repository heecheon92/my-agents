"""Knowledge-base API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.groups.models import MembershipModel
from my_agents.knowledge.models import KnowledgeBaseModel, KnowledgeBaseScope
from my_agents.knowledge.schemas import KnowledgeBaseCreateRequest, KnowledgeBaseResponse
from my_agents.persistence.database import get_database_session

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
        if request.group_id is None or not _has_group_membership(
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
    return _knowledge_base_response(knowledge_base)


@knowledge_bases_router.get("", response_model=list[KnowledgeBaseResponse])
def list_knowledge_bases(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[KnowledgeBaseResponse]:
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == principal.user_id)
    knowledge_bases = db.scalars(
        select(KnowledgeBaseModel).where(
            or_(
                KnowledgeBaseModel.owner_user_id == principal.user_id,
                KnowledgeBaseModel.group_id.in_(group_ids),
            )
        )
    ).all()
    return [_knowledge_base_response(kb) for kb in knowledge_bases]


def _has_group_membership(db: Session, group_id: str, user_id: str) -> bool:
    return (
        db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == group_id,
                MembershipModel.user_id == user_id,
            )
        )
        is not None
    )


def _knowledge_base_response(knowledge_base: KnowledgeBaseModel) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=knowledge_base.id,
        name=knowledge_base.name,
        scope=KnowledgeBaseScope(knowledge_base.scope),
        owner_user_id=knowledge_base.owner_user_id,
        group_id=knowledge_base.group_id,
    )
