"""Group and membership API routes."""

from __future__ import annotations

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
from my_agents.persistence.database import get_database_session

groups_router = APIRouter(prefix="/groups", tags=["groups"])


@groups_router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    request: GroupCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> GroupResponse:
    group = GroupModel(name=request.name.strip(), created_by_user_id=principal.user_id)
    db.add(group)
    db.flush()
    membership = MembershipModel(
        group_id=group.id,
        user_id=principal.user_id,
        role=MembershipRole.OWNER.value,
    )
    db.add(membership)
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
