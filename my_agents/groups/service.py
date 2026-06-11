"""Group invitation service helpers.

The product boundary is invitation acceptance: public routes may create pending
invitations, but active memberships are created only when the invited user accepts
an opaque token while authenticated with the invited, verified email.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from my_agents.auth.email import AuthEmailLanguage, AuthEmailSender, get_auth_email_sender
from my_agents.auth.models import UserModel
from my_agents.diagnostics import deploy_log, safe_email_context
from my_agents.groups.models import (
    GroupInvitationModel,
    GroupInvitationStatus,
    GroupModel,
    MembershipModel,
    MembershipRole,
)

DEFAULT_GROUP_INVITATION_TTL = timedelta(days=7)
GROUP_INVITATION_TOKEN_BYTES = 32


class GroupInvitationError(RuntimeError):
    """Base invitation-service error."""


class PendingInvitationExistsError(GroupInvitationError):
    """Raised when a group/email already has a pending invitation."""


class InvitationNotFoundError(GroupInvitationError):
    """Raised when a requested invitation is not visible in the group scope."""


class InvitationNotPendingError(GroupInvitationError):
    """Raised when a pending-only operation targets a consumed invitation."""


class InvalidInvitationTokenError(GroupInvitationError):
    """Raised when an invitation token cannot be accepted safely."""


class GroupMembershipPermissionError(GroupInvitationError):
    """Raised when the actor is not allowed to manage group invitations."""


@dataclass(frozen=True)
class GroupInvitationDelivery:
    """Invitation plus one-time raw token for private delivery boundaries."""

    invitation: GroupInvitationModel
    token: str


class GroupInvitationService:
    """Own invitation lifecycle and active membership acceptance."""

    def __init__(
        self,
        db: Session,
        *,
        email_sender: AuthEmailSender | None = None,
        ttl: timedelta = DEFAULT_GROUP_INVITATION_TTL,
    ) -> None:
        self._db = db
        self._email_sender = email_sender or get_auth_email_sender()
        self._ttl = ttl

    def create_invitation(
        self,
        *,
        group_id: str,
        invited_email: str,
        role: MembershipRole,
        created_by_user_id: str,
        email_language: AuthEmailLanguage = "ko",
    ) -> GroupInvitationModel:
        """Create and deliver a pending invitation without exposing account existence."""
        self._require_group_manager(group_id=group_id, user_id=created_by_user_id)
        normalized_email = normalize_invitation_email(invited_email)
        self._assert_no_pending_invitation(
            group_id=group_id,
            invited_email_normalized=normalized_email,
        )
        delivery = self._new_pending_invitation(
            group_id=group_id,
            invited_email_normalized=normalized_email,
            role=role,
            created_by_user_id=created_by_user_id,
        )
        self._db.add(delivery.invitation)
        try:
            self._db.flush()
            self._send_invitation_email(
                recipient_email=normalized_email,
                token=delivery.token,
                expires_at=delivery.invitation.expires_at,
                email_language=email_language,
            )
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            raise PendingInvitationExistsError("pending_invitation_exists") from exc
        except Exception:
            self._db.rollback()
            raise
        self._db.refresh(delivery.invitation)
        return delivery.invitation

    def list_group_invitations(
        self,
        *,
        group_id: str,
        actor_user_id: str,
    ) -> list[GroupInvitationModel]:
        """List invitations for owner/admin management without user-profile enrichment."""
        self._require_group_manager(group_id=group_id, user_id=actor_user_id)
        return list(
            self._db.scalars(
                select(GroupInvitationModel)
                .where(GroupInvitationModel.group_id == group_id)
                .order_by(GroupInvitationModel.created_at.desc(), GroupInvitationModel.id.desc())
            ).all()
        )

    def update_pending_invitation_role(
        self,
        *,
        group_id: str,
        invitation_id: str,
        role: MembershipRole,
        actor_user_id: str,
    ) -> GroupInvitationModel:
        """Update the target role on a pending invitation."""
        self._require_group_manager(group_id=group_id, user_id=actor_user_id)
        invitation = self._get_invitation(group_id=group_id, invitation_id=invitation_id)
        self._require_pending(invitation)
        invitation.role = role.value
        self._db.add(invitation)
        self._db.commit()
        self._db.refresh(invitation)
        return invitation

    def resend_pending_invitation(
        self,
        *,
        group_id: str,
        invitation_id: str,
        actor_user_id: str,
        email_language: AuthEmailLanguage = "ko",
    ) -> GroupInvitationModel:
        """Rotate and deliver a pending invitation token.

        If delivery fails, the surrounding rollback preserves the previous token hash.
        """
        self._require_group_manager(group_id=group_id, user_id=actor_user_id)
        invitation = self._get_invitation(group_id=group_id, invitation_id=invitation_id)
        self._require_pending(invitation)
        token = _new_invitation_token()
        now = datetime.now(UTC)
        invitation.token_hash = digest_invitation_token(token)
        invitation.expires_at = now + self._ttl
        invitation.resent_at = now
        self._db.add(invitation)
        try:
            self._db.flush()
            self._send_invitation_email(
                recipient_email=invitation.invited_email_normalized,
                token=token,
                expires_at=invitation.expires_at,
                email_language=email_language,
            )
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise
        self._db.refresh(invitation)
        return invitation

    def cancel_pending_invitation(
        self,
        *,
        group_id: str,
        invitation_id: str,
        actor_user_id: str,
    ) -> GroupInvitationModel:
        """Cancel a pending invitation so its token cannot be accepted."""
        self._require_group_manager(group_id=group_id, user_id=actor_user_id)
        invitation = self._get_invitation(group_id=group_id, invitation_id=invitation_id)
        self._require_pending(invitation)
        invitation.status = GroupInvitationStatus.CANCELLED.value
        invitation.cancelled_at = datetime.now(UTC)
        self._db.add(invitation)
        self._db.commit()
        self._db.refresh(invitation)
        return invitation

    def accept_invitation(
        self,
        *,
        token: str,
        actor_user_id: str,
    ) -> MembershipModel:
        """Accept an invitation token as the authenticated invited user."""
        invitation = self._invitation_for_token(token)
        now = datetime.now(UTC)
        if (
            invitation is None
            or invitation.status != GroupInvitationStatus.PENDING.value
            or _as_utc(invitation.expires_at) <= now
        ):
            if invitation is not None and invitation.status == GroupInvitationStatus.PENDING.value:
                invitation.status = GroupInvitationStatus.EXPIRED.value
                self._db.add(invitation)
                self._db.commit()
            raise InvalidInvitationTokenError("invalid or expired invitation")

        user = self._db.get(UserModel, actor_user_id)
        if (
            user is None
            or user.email is None
            or user.email_verified_at is None
            or normalize_invitation_email(user.email) != invitation.invited_email_normalized
        ):
            raise InvalidInvitationTokenError("invalid or expired invitation")

        membership = self._db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == invitation.group_id,
                MembershipModel.user_id == actor_user_id,
            )
        )
        if membership is None:
            membership = MembershipModel(
                group_id=invitation.group_id,
                user_id=actor_user_id,
                role=invitation.role,
            )
            self._db.add(membership)
            self._db.flush()

        invitation.status = GroupInvitationStatus.ACCEPTED.value
        invitation.accepted_by_user_id = actor_user_id
        invitation.accepted_at = now
        self._db.add(invitation)
        try:
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            raise InvalidInvitationTokenError("invalid or expired invitation") from exc
        self._db.refresh(membership)
        return membership

    def list_group_members(
        self,
        *,
        group_id: str,
        actor_user_id: str,
    ) -> list[MembershipModel]:
        """List active member basics for owner/admin role maintenance only."""
        self._require_group_manager(group_id=group_id, user_id=actor_user_id)
        return list(
            self._db.scalars(
                select(MembershipModel)
                .where(MembershipModel.group_id == group_id)
                .order_by(MembershipModel.created_at.asc(), MembershipModel.id.asc())
            ).all()
        )

    def _new_pending_invitation(
        self,
        *,
        group_id: str,
        invited_email_normalized: str,
        role: MembershipRole,
        created_by_user_id: str,
    ) -> GroupInvitationDelivery:
        token = _new_invitation_token()
        invitation = GroupInvitationModel(
            group_id=group_id,
            invited_email_normalized=invited_email_normalized,
            role=role.value,
            status=GroupInvitationStatus.PENDING.value,
            token_hash=digest_invitation_token(token),
            created_by_user_id=created_by_user_id,
            expires_at=datetime.now(UTC) + self._ttl,
        )
        return GroupInvitationDelivery(invitation=invitation, token=token)

    def _assert_no_pending_invitation(
        self,
        *,
        group_id: str,
        invited_email_normalized: str,
    ) -> None:
        existing = self._db.scalar(
            select(GroupInvitationModel).where(
                GroupInvitationModel.group_id == group_id,
                GroupInvitationModel.invited_email_normalized == invited_email_normalized,
                GroupInvitationModel.status == GroupInvitationStatus.PENDING.value,
            )
        )
        if existing is not None:
            raise PendingInvitationExistsError("pending_invitation_exists")

    def _send_invitation_email(
        self,
        *,
        recipient_email: str,
        token: str,
        expires_at: datetime,
        email_language: AuthEmailLanguage,
    ) -> None:
        deploy_log("groups.invitation.email.send", **safe_email_context(recipient_email))
        self._email_sender.send_group_invitation(
            recipient_email=recipient_email,
            token=token,
            expires_at=expires_at,
            language=email_language,
        )

    def _require_group_manager(self, *, group_id: str, user_id: str) -> MembershipModel:
        _, membership = self._get_group_and_membership(group_id=group_id, user_id=user_id)
        if membership.role not in (MembershipRole.OWNER.value, MembershipRole.ADMIN.value):
            raise GroupMembershipPermissionError("not allowed")
        return membership

    def _get_group_and_membership(
        self, *, group_id: str, user_id: str
    ) -> tuple[GroupModel, MembershipModel]:
        row = self._db.execute(
            select(GroupModel, MembershipModel)
            .join(MembershipModel)
            .where(
                GroupModel.id == group_id,
                MembershipModel.user_id == user_id,
            )
        ).one_or_none()
        if row is None:
            raise InvitationNotFoundError("group not found")
        return row

    def _get_invitation(self, *, group_id: str, invitation_id: str) -> GroupInvitationModel:
        invitation = self._db.get(GroupInvitationModel, invitation_id)
        if invitation is None or invitation.group_id != group_id:
            raise InvitationNotFoundError("invitation not found")
        return invitation

    def _require_pending(self, invitation: GroupInvitationModel) -> None:
        if invitation.status != GroupInvitationStatus.PENDING.value:
            raise InvitationNotPendingError("invitation is not pending")

    def _invitation_for_token(self, token: str) -> GroupInvitationModel | None:
        stripped = token.strip()
        if not stripped:
            return None
        return self._db.scalar(
            select(GroupInvitationModel).where(
                GroupInvitationModel.token_hash == digest_invitation_token(stripped)
            )
        )


def normalize_invitation_email(email: str) -> str:
    """Normalize invitation email addresses without leaking account existence."""
    normalized = email.strip().casefold()
    if not normalized:
        raise ValueError("email must not be blank")
    return normalized


def digest_invitation_token(token: str) -> str:
    """Return the digest stored for opaque invitation tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_invitation_token() -> str:
    return secrets.token_urlsafe(GROUP_INVITATION_TOKEN_BYTES)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
