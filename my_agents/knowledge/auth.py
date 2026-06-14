"""Knowledge-base authorization helpers shared by API and retrieval boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.groups.models import MembershipModel, MembershipRole
from my_agents.knowledge.models import (
    KnowledgeBaseModel,
    KnowledgeBasePublicationModel,
    KnowledgeBasePurpose,
    KnowledgeBaseScope,
)
from my_agents.knowledge.schemas import KnowledgeBaseSelection


@dataclass(frozen=True)
class KnowledgeBaseSelectionContext:
    """Resolved chat/retrieval knowledge-base boundary."""

    mode: str
    knowledge_base_ids: tuple[str, ...]
    resolved_count: int
    resolved_knowledge_base_ids: tuple[str, ...] = ()
    ambient_system_knowledge_base_ids: tuple[str, ...] = ()
    ambient_system_knowledge_base_count: int = 0

    @property
    def retrieval_knowledge_base_ids(self) -> tuple[str, ...] | None:
        """Return an explicit retrieval scope, or None for personal-chat all mode."""
        if self.mode == "selected":
            return tuple(
                dict.fromkeys(
                    (
                        *(self.resolved_knowledge_base_ids or self.knowledge_base_ids),
                        *self.ambient_system_knowledge_base_ids,
                    )
                )
            )
        return None


def is_system_knowledge_manager(principal: Principal) -> bool:
    """Return whether the current principal can manage system knowledge sources.

    Lane A owns the Principal capability field. Until that branch is integrated,
    fall closed instead of assuming privilege.
    """
    return bool(getattr(principal, "can_manage_system_knowledge", False))


def system_knowledge_base_filter():
    """Return the SQL predicate for standard public system KBs."""
    return and_(
        KnowledgeBaseModel.scope == KnowledgeBaseScope.SYSTEM.value,
        KnowledgeBaseModel.group_id.is_(None),
        KnowledgeBaseModel.purpose == KnowledgeBasePurpose.STANDARD.value,
    )


def get_authorized_knowledge_base_or_404(
    db: Session, knowledge_base_id: str, user_id: str
) -> KnowledgeBaseModel:
    """Return an authorized KB or conceal missing/unauthorized KBs as 404.

    This management-level check includes hidden staging KBs so upload/create
    endpoints can write to them by direct ID. Chat/retrieval selection must use
    `get_retrievable_knowledge_base_or_404` instead.
    """
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if user_can_select_knowledge_base(db, knowledge_base=knowledge_base, user_id=user_id):
        return knowledge_base
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")


def get_manageable_knowledge_base_or_404(
    db: Session, knowledge_base_id: str, principal: Principal
) -> KnowledgeBaseModel:
    """Return a KB visible to management routes, concealing unauthorized IDs."""
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if knowledge_base.scope == KnowledgeBaseScope.SYSTEM.value:
        if is_system_knowledge_manager(principal) and knowledge_base.purpose == (
            KnowledgeBasePurpose.STANDARD.value
        ):
            return knowledge_base
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if user_can_select_knowledge_base(db, knowledge_base=knowledge_base, user_id=principal.user_id):
        return knowledge_base
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")


def get_retrievable_knowledge_base_or_404(
    db: Session, knowledge_base_id: str, principal: Principal
) -> KnowledgeBaseModel:
    """Return a user-selectable KB only when it participates in retrieval."""
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if knowledge_base.scope == KnowledgeBaseScope.SYSTEM.value:
        if is_system_knowledge_manager(principal):
            return knowledge_base
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if not user_can_select_knowledge_base(
        db, knowledge_base=knowledge_base, user_id=principal.user_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")
    if knowledge_base.purpose != KnowledgeBasePurpose.STANDARD.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="knowledge base is not selectable for chat retrieval",
        )
    return knowledge_base


def user_can_select_knowledge_base(
    db: Session, *, knowledge_base: KnowledgeBaseModel, user_id: str
) -> bool:
    """Return whether a user can use a KB as a source boundary."""
    if knowledge_base.scope == KnowledgeBaseScope.PERSONAL.value:
        if knowledge_base.group_id is not None:
            return False
        if knowledge_base.owner_user_id == user_id:
            return True
        return (
            db.scalar(
                select(KnowledgeBasePublicationModel.id)
                .join(
                    MembershipModel,
                    MembershipModel.group_id == KnowledgeBasePublicationModel.group_id,
                )
                .where(
                    KnowledgeBasePublicationModel.knowledge_base_id == knowledge_base.id,
                    MembershipModel.user_id == user_id,
                )
            )
            is not None
        )
    if knowledge_base.scope == KnowledgeBaseScope.GROUP.value:
        return knowledge_base.group_id is not None and has_group_membership(
            db, knowledge_base.group_id, user_id
        )
    return False


def authorized_knowledge_base_filter(user_id: str):
    """Return the SQL predicate for management-visible KBs authorized to a user.

    Personal KBs remain owner-scoped. Group KBs are group-authority scoped:
    the original creator does not retain access after membership is removed.
    This predicate intentionally includes hidden staging KBs; use
    `retrievable_knowledge_base_filter` for chat/list/retrieval surfaces.
    """
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    published_personal_kb_ids = published_personal_knowledge_base_ids_for_user(user_id)
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
        and_(
            KnowledgeBaseModel.scope == KnowledgeBaseScope.PERSONAL.value,
            KnowledgeBaseModel.id.in_(published_personal_kb_ids),
        ),
    )


def selectable_knowledge_base_filter(user_id: str):
    """Return the SQL predicate for KBs visible/selectable by the authenticated user."""
    return and_(
        authorized_knowledge_base_filter(user_id),
        KnowledgeBaseModel.purpose == KnowledgeBasePurpose.STANDARD.value,
    )


def management_visible_knowledge_base_filter(principal: Principal):
    """Return KBs listed in management surfaces for this principal."""
    visible = [selectable_knowledge_base_filter(principal.user_id)]
    if is_system_knowledge_manager(principal):
        visible.append(system_knowledge_base_filter())
    return or_(*visible)


def retrievable_knowledge_base_filter(user_id: str):
    """Return the SQL predicate for KBs that can appear in chat/RAG retrieval."""
    return or_(selectable_knowledge_base_filter(user_id), system_knowledge_base_filter())


def published_personal_knowledge_base_ids_for_user(user_id: str):
    """Return KB IDs published into any group where the user is currently a member."""
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == user_id)
    return select(KnowledgeBasePublicationModel.knowledge_base_id).where(
        KnowledgeBasePublicationModel.group_id.in_(group_ids)
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


def require_personal_knowledge_base_for_document_write(
    db: Session, *, knowledge_base_id: str, user_id: str
) -> KnowledgeBaseModel:
    """Return an authorized personal KB for direct document create/upload paths.

    Group KBs are populated through the publish-review workflow so personal
    documents cannot be directly wired into group-owned retrieval scope.
    """
    knowledge_base = get_authorized_knowledge_base_or_404(db, knowledge_base_id, user_id)
    if (
        knowledge_base.scope != KnowledgeBaseScope.PERSONAL.value
        or knowledge_base.group_id is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="group knowledge bases accept documents through publish approval",
        )
    if knowledge_base.owner_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="direct document writes require an owned personal knowledge base",
        )
    return knowledge_base


def require_document_writable_knowledge_base(
    db: Session,
    *,
    knowledge_base_id: str,
    principal: Principal,
    allow_system_management: bool = False,
) -> KnowledgeBaseModel:
    """Return a document-writable KB without broadening legacy direct write routes."""
    knowledge_base = db.get(KnowledgeBaseModel, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    if knowledge_base.scope == KnowledgeBaseScope.SYSTEM.value:
        if allow_system_management and is_system_knowledge_manager(principal):
            return knowledge_base
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="knowledge base not found",
        )
    return require_personal_knowledge_base_for_document_write(
        db,
        knowledge_base_id=knowledge_base_id,
        user_id=principal.user_id,
    )


def resolve_knowledge_base_selection(
    db: Session, *, principal: Principal, mode: str, knowledge_base_ids: list[str]
) -> KnowledgeBaseSelectionContext:
    """Validate chat KB selection and return audit-friendly resolved metadata."""
    ambient_system_ids = resolve_system_ambient_knowledge_base_ids(db)
    if mode == "selected":
        resolved_ids = tuple(dict.fromkeys(knowledge_base_ids))
        for knowledge_base_id in resolved_ids:
            get_retrievable_knowledge_base_or_404(db, knowledge_base_id, principal)
        return KnowledgeBaseSelectionContext(
            mode=mode,
            knowledge_base_ids=resolved_ids,
            resolved_count=len(resolved_ids),
            resolved_knowledge_base_ids=resolved_ids,
            ambient_system_knowledge_base_ids=ambient_system_ids,
            ambient_system_knowledge_base_count=len(ambient_system_ids),
        )

    count = authorized_knowledge_base_count(db, user_id=principal.user_id)
    return KnowledgeBaseSelectionContext(
        mode="all",
        knowledge_base_ids=(),
        resolved_count=count,
        ambient_system_knowledge_base_ids=ambient_system_ids,
        ambient_system_knowledge_base_count=len(ambient_system_ids),
    )


def resolve_conversation_knowledge_context(
    db: Session,
    *,
    principal: Principal,
    requested_selection: KnowledgeBaseSelection,
) -> KnowledgeBaseSelectionContext:
    """Resolve unified chat source boundaries for authorized standard KBs."""
    return resolve_knowledge_base_selection(
        db,
        principal=principal,
        mode=requested_selection.mode,
        knowledge_base_ids=requested_selection.knowledge_base_ids,
    )


def authorized_knowledge_base_count(db: Session, *, user_id: str) -> int:
    """Return how many KBs a user may select in all-KBs mode."""
    return (
        db.scalar(
            select(func.count(KnowledgeBaseModel.id)).where(
                selectable_knowledge_base_filter(user_id)
            )
        )
        or 0
    )


def resolve_system_ambient_knowledge_base_ids(db: Session) -> tuple[str, ...]:
    """Return standard system KB IDs used internally as ambient chat context."""
    return tuple(
        db.scalars(
            select(KnowledgeBaseModel.id)
            .where(system_knowledge_base_filter())
            .order_by(KnowledgeBaseModel.created_at, KnowledgeBaseModel.id)
        ).all()
    )
