"""Authorization service for document-level permission checks."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.groups.models import MembershipModel, MembershipRole
from my_agents.knowledge.models import DocumentModel, DocumentPermissionModel
from my_agents.permissions.contracts import DocumentOperation


class AuthorizationService:
    """Evaluate deny-by-default document authorization rules."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def can(self, *, user_id: str, document: DocumentModel, operation: DocumentOperation) -> bool:
        """Return whether a user can perform a document operation."""
        if document.owner_user_id == user_id:
            return True

        explicit = self._db.scalar(
            select(DocumentPermissionModel).where(
                DocumentPermissionModel.document_id == document.id,
                DocumentPermissionModel.user_id == user_id,
            )
        )
        if explicit is not None and _explicit_allows(explicit, operation):
            return True

        if document.group_id is None:
            return False

        membership = self._db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == document.group_id,
                MembershipModel.user_id == user_id,
            )
        )
        if membership is None:
            return False
        return _role_allows(membership.role, operation)


def _explicit_allows(permission: DocumentPermissionModel, operation: DocumentOperation) -> bool:
    if operation in (DocumentOperation.READ, DocumentOperation.RETRIEVE, DocumentOperation.CITE):
        return permission.can_read
    if operation == DocumentOperation.WRITE:
        return permission.can_write
    if operation in (DocumentOperation.MANAGE_PERMISSIONS, DocumentOperation.DELETE):
        return permission.can_manage
    if operation == DocumentOperation.INGEST:
        return permission.can_ingest
    return False


def _role_allows(role: str, operation: DocumentOperation) -> bool:
    if role in (MembershipRole.OWNER, MembershipRole.ADMIN):
        return True
    if role == MembershipRole.EDITOR:
        return operation in {
            DocumentOperation.READ,
            DocumentOperation.WRITE,
            DocumentOperation.INGEST,
            DocumentOperation.RETRIEVE,
            DocumentOperation.CITE,
        }
    if role == MembershipRole.VIEWER:
        return operation in {
            DocumentOperation.READ,
            DocumentOperation.RETRIEVE,
            DocumentOperation.CITE,
        }
    return False
