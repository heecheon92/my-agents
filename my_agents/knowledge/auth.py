"""Knowledge-base authorization helpers shared by API and retrieval boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from my_agents.groups.models import MembershipModel, MembershipRole
from my_agents.knowledge.models import KnowledgeBaseModel


@dataclass(frozen=True)
class KnowledgeBaseSelectionContext:
    """Resolved chat/retrieval knowledge-base boundary."""

    mode: str
    knowledge_base_ids: tuple[str, ...]
    resolved_count: int


def get_authorized_knowledge_base_or_404(
    db: Session, knowledge_base_id: str, user_id: str
) -> KnowledgeBaseModel:
    """Return an authorized KB or conceal missing/unauthorized KBs as 404."""
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if user_can_select_knowledge_base(db, knowledge_base=knowledge_base, user_id=user_id):
        return knowledge_base
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")


def user_can_select_knowledge_base(
    db: Session, *, knowledge_base: KnowledgeBaseModel, user_id: str
) -> bool:
    """Return whether a user can use a KB as a source boundary."""
    if knowledge_base.owner_user_id == user_id:
        return True
    if knowledge_base.group_id is None:
        return False
    return has_group_membership(db, knowledge_base.group_id, user_id)


def has_group_membership(db: Session, group_id: str, user_id: str) -> bool:
    """Return whether the user belongs to a group."""
    return (
        db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == group_id,
                MembershipModel.user_id == user_id,
            )
        )
        is not None
    )


def require_group_write_access(db: Session, group_id: str, user_id: str) -> None:
    """Require write-capable group membership for KB document writes."""
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


def resolve_kb_document_group_id(
    db: Session, *, knowledge_base_id: str, user_id: str
) -> str | None:
    """Resolve and authorize the group_id that must be stamped on a KB document write."""
    knowledge_base = get_authorized_knowledge_base_or_404(db, knowledge_base_id, user_id)
    if knowledge_base.group_id is not None:
        require_group_write_access(db, knowledge_base.group_id, user_id)
    return knowledge_base.group_id


def resolve_knowledge_base_selection(
    db: Session, *, user_id: str, mode: str, knowledge_base_ids: list[str]
) -> KnowledgeBaseSelectionContext:
    """Validate chat KB selection and return audit-friendly resolved metadata."""
    if mode == "selected":
        resolved_ids = tuple(dict.fromkeys(knowledge_base_ids))
        for knowledge_base_id in resolved_ids:
            get_authorized_knowledge_base_or_404(db, knowledge_base_id, user_id)
        return KnowledgeBaseSelectionContext(
            mode=mode,
            knowledge_base_ids=resolved_ids,
            resolved_count=len(resolved_ids),
        )

    count = authorized_knowledge_base_count(db, user_id=user_id)
    return KnowledgeBaseSelectionContext(mode="all", knowledge_base_ids=(), resolved_count=count)


def authorized_knowledge_base_count(db: Session, *, user_id: str) -> int:
    """Return how many KBs a user may select in all-KBs mode."""
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    return (
        db.scalar(
            select(func.count(KnowledgeBaseModel.id)).where(
                (KnowledgeBaseModel.owner_user_id == user_id)
                | (KnowledgeBaseModel.group_id.in_(group_ids))
            )
        )
        or 0
    )
