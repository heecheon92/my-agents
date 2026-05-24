"""Knowledge-base authorization helpers shared by API and retrieval boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from my_agents.conversations.models import ConversationModel
from my_agents.groups.models import MembershipModel, MembershipRole
from my_agents.knowledge.models import KnowledgeBaseModel, KnowledgeBaseScope
from my_agents.knowledge.schemas import KnowledgeBaseSelection


@dataclass(frozen=True)
class KnowledgeBaseSelectionContext:
    """Resolved chat/retrieval knowledge-base boundary."""

    mode: str
    knowledge_base_ids: tuple[str, ...]
    resolved_count: int
    source_context_group_id: str | None = None
    mandatory_group_knowledge_base_ids: tuple[str, ...] = ()
    optional_personal_knowledge_base_ids: tuple[str, ...] = ()
    resolved_knowledge_base_ids: tuple[str, ...] = ()

    @property
    def retrieval_knowledge_base_ids(self) -> tuple[str, ...] | None:
        """Return an explicit retrieval scope, or None for personal-chat all mode."""
        if self.source_context_group_id is not None:
            return self.resolved_knowledge_base_ids
        if self.mode == "selected":
            return self.resolved_knowledge_base_ids or self.knowledge_base_ids
        return None


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
    if knowledge_base.scope == KnowledgeBaseScope.PERSONAL.value:
        return knowledge_base.group_id is None and knowledge_base.owner_user_id == user_id
    if knowledge_base.scope == KnowledgeBaseScope.GROUP.value:
        return knowledge_base.group_id is not None and has_group_membership(
            db, knowledge_base.group_id, user_id
        )
    return False


def authorized_knowledge_base_filter(user_id: str):
    """Return the SQL predicate for KBs selectable by a user.

    Personal KBs remain owner-scoped. Group KBs are group-authority scoped:
    the original creator does not retain access after membership is removed.
    """
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    return or_(
        and_(
            KnowledgeBaseModel.scope == KnowledgeBaseScope.PERSONAL.value,
            KnowledgeBaseModel.group_id.is_(None),
            KnowledgeBaseModel.owner_user_id == user_id,
        ),
        and_(
            KnowledgeBaseModel.scope == KnowledgeBaseScope.GROUP.value,
            KnowledgeBaseModel.group_id.in_(group_ids),
        ),
    )


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
            resolved_knowledge_base_ids=resolved_ids,
        )

    count = authorized_knowledge_base_count(db, user_id=user_id)
    return KnowledgeBaseSelectionContext(mode="all", knowledge_base_ids=(), resolved_count=count)


def resolve_conversation_knowledge_context(
    db: Session,
    *,
    user_id: str,
    conversation: ConversationModel,
    requested_selection: KnowledgeBaseSelection,
    optional_personal_knowledge_base_ids: list[str],
) -> KnowledgeBaseSelectionContext:
    """Resolve source boundaries for a personal or group-context conversation.

    Personal chat keeps the existing all/selected behavior and rejects the group-chat
    optional personal attachment field to avoid two competing source semantics.
    Group-context chat treats `all` as mandatory group KBs only, with an explicit
    per-run optional personal attachment list.
    """
    if conversation.group_id is None:
        if optional_personal_knowledge_base_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="optional personal knowledge bases are only valid in group chat",
            )
        return resolve_knowledge_base_selection(
            db,
            user_id=user_id,
            mode=requested_selection.mode,
            knowledge_base_ids=requested_selection.knowledge_base_ids,
        )

    if requested_selection.mode != "all":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="group chat uses mandatory group knowledge bases only",
        )

    mandatory_group_ids = _group_knowledge_base_ids(db, conversation.group_id)
    optional_personal_ids = _validate_optional_personal_knowledge_base_ids(
        db,
        user_id=user_id,
        knowledge_base_ids=optional_personal_knowledge_base_ids,
    )
    resolved_ids = tuple(dict.fromkeys((*mandatory_group_ids, *optional_personal_ids)))
    return KnowledgeBaseSelectionContext(
        mode="all",
        knowledge_base_ids=(),
        source_context_group_id=conversation.group_id,
        mandatory_group_knowledge_base_ids=mandatory_group_ids,
        optional_personal_knowledge_base_ids=optional_personal_ids,
        resolved_knowledge_base_ids=resolved_ids,
        resolved_count=len(resolved_ids),
    )


def authorized_knowledge_base_count(db: Session, *, user_id: str) -> int:
    """Return how many KBs a user may select in all-KBs mode."""
    return (
        db.scalar(
            select(func.count(KnowledgeBaseModel.id)).where(
                authorized_knowledge_base_filter(user_id)
            )
        )
        or 0
    )


def _group_knowledge_base_ids(db: Session, group_id: str) -> tuple[str, ...]:
    rows = db.scalars(
        select(KnowledgeBaseModel.id)
        .where(
            KnowledgeBaseModel.group_id == group_id,
            KnowledgeBaseModel.scope == KnowledgeBaseScope.GROUP.value,
        )
        .order_by(KnowledgeBaseModel.created_at, KnowledgeBaseModel.id)
    ).all()
    return tuple(rows)


def _validate_optional_personal_knowledge_base_ids(
    db: Session, *, user_id: str, knowledge_base_ids: list[str]
) -> tuple[str, ...]:
    resolved_ids = tuple(dict.fromkeys(knowledge_base_ids))
    for knowledge_base_id in resolved_ids:
        knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
        if (
            knowledge_base is None
            or knowledge_base.scope != KnowledgeBaseScope.PERSONAL.value
            or knowledge_base.owner_user_id != user_id
            or knowledge_base.group_id is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="optional personal knowledge bases must be owned personal sources",
            )
    return resolved_ids
