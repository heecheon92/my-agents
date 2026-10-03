"""First-party authentication and owned-session service."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal, UserType
from my_agents.auth.email import AuthEmailLanguage, AuthEmailSender, get_auth_email_sender
from my_agents.auth.models import (
    AuthTokenModel,
    GuestAccessRequestModel,
    SessionModel,
    UserModel,
)
from my_agents.diagnostics import deploy_log, safe_email_context
from my_agents.settings import Settings, get_settings

AuthTokenPurpose = Literal["email_verification", "password_reset"]
EMAIL_VERIFICATION_TOKEN_TTL = timedelta(hours=24)
PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)
DEFAULT_AUTH_PASSWORD_HASH_TIME_COST = 2
DEFAULT_AUTH_PASSWORD_HASH_MEMORY_COST_KIB = 19_456
DEFAULT_AUTH_PASSWORD_HASH_PARALLELISM = 1


def build_password_hasher(
    *,
    time_cost: int = DEFAULT_AUTH_PASSWORD_HASH_TIME_COST,
    memory_cost: int = DEFAULT_AUTH_PASSWORD_HASH_MEMORY_COST_KIB,
    parallelism: int = DEFAULT_AUTH_PASSWORD_HASH_PARALLELISM,
) -> PasswordHasher:
    """Build a deployable Argon2id password hasher.

    The defaults intentionally follow a lower-memory profile than argon2-cffi's
    generic defaults so signup can run predictably on small demo containers.
    """
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
    )


class AuthError(RuntimeError):
    """Base auth-service error."""


class DuplicateEmailError(AuthError):
    """Raised when signup attempts to reuse an email."""


class InvalidCredentialsError(AuthError):
    """Raised when login credentials are invalid."""


class UnverifiedEmailError(AuthError):
    """Raised when a user must verify their email before logging in."""


class AccountApprovalRequiredError(AuthError):
    """Raised when a registered user is waiting for operator approval."""


class AccountRejectedError(AuthError):
    """Raised when a registered user's signup request was rejected."""


class AccountMutationNotAllowedError(AuthError):
    """Raised when a principal cannot use password-backed account mutations."""


class InvalidSessionError(AuthError):
    """Raised when a session token is absent, unknown, or revoked."""


class InvalidCsrfTokenError(AuthError):
    """Raised when a mutating cookie-auth request lacks valid CSRF proof."""


class InvalidAuthTokenError(AuthError):
    """Raised when an auth lifecycle token is unknown, expired, or consumed."""


@dataclass(frozen=True)
class AuthenticatedSession:
    """Session material returned once when a user logs in."""

    user: UserModel
    session: SessionModel
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class SignupResult:
    """Signup result with user plus local delivery metadata."""

    user: UserModel
    verification_email_sent: bool


@dataclass(frozen=True)
class GuestAccessCodeResult:
    """One-time guest access code result."""

    code: str
    expires_at: datetime
    request_id: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class AccountApprovalResult:
    """Manual account approval result with printable verification metadata."""

    user: UserModel
    verification_token: str | None
    email_marked_verified: bool = False
    was_email_already_verified: bool = False


class AuthService:
    """Own first-party users, password hashes, sessions, and account lifecycle tokens."""

    def __init__(
        self,
        db: Session,
        password_hasher: PasswordHasher | None = None,
        email_sender: AuthEmailSender | None = None,
        notification_email: str | None = None,
        guest_settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._guest_settings = guest_settings
        self._notification_email = notification_email
        self._password_hasher = password_hasher or build_password_hasher()
        self._email_sender = email_sender or get_auth_email_sender()

    def signup(
        self,
        *,
        email: str,
        nickname: str,
        password: str,
        email_language: AuthEmailLanguage = "ko",
        auto_approve: bool = False,
    ) -> SignupResult:
        normalized_email = _normalize_email(email)
        from my_agents.auth.guest_policy import lock_signup_identity

        lock_signup_identity(self._db, normalized_email, self._guest_settings or get_settings())
        email_context = safe_email_context(normalized_email)
        deploy_log("auth.service.signup.start", **email_context)
        existing = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if existing is not None:
            deploy_log("auth.service.signup.duplicate_email", **email_context)
            raise DuplicateEmailError("email is already registered")
        deploy_log("auth.service.signup.email_available", **email_context)
        deploy_log("auth.service.signup.password_hash.start", **email_context)
        hash_started_at = perf_counter()
        password_hash = self._password_hasher.hash(password)
        deploy_log(
            "auth.service.signup.password_hash.completed",
            elapsed_ms=round((perf_counter() - hash_started_at) * 1000, 2),
            **email_context,
        )
        user = UserModel(
            id=str(uuid.uuid4()),
            email=normalized_email,
            nickname=nickname,
            password_hash=password_hash,
            account_type="registered",
            user_type=UserType.NORMAL.value,
            approval_status="approved" if auto_approve else "pending",
            approved_at=datetime.now(UTC) if auto_approve else None,
        )
        self._db.add(user)
        deploy_log("auth.service.signup.user_add.completed", user_id=user.id, **email_context)
        deploy_log("auth.service.signup.user_flush.start", user_id=user.id, **email_context)
        flush_started_at = perf_counter()
        self._db.flush()
        deploy_log(
            "auth.service.signup.user_flushed",
            user_id=user.id,
            elapsed_ms=round((perf_counter() - flush_started_at) * 1000, 2),
            **email_context,
        )
        from my_agents.auth.notifications import enqueue_registration

        enqueue_registration(
            self._db,
            user,
            recipient=self._notification_email,
            account_email=normalized_email,
            kind="registered_signup",
        )
        token = None
        if auto_approve:
            token = self._create_token(
                user_id=user.id,
                purpose="email_verification",
                ttl=EMAIL_VERIFICATION_TOKEN_TTL,
            )
            deploy_log("auth.service.signup.token_created", user_id=user.id, **email_context)
        self._db.commit()
        deploy_log("auth.service.signup.db_committed", user_id=user.id, **email_context)
        self._db.refresh(user)
        if token is None:
            deploy_log("auth.service.signup.pending_approval", user_id=user.id, **email_context)
            return SignupResult(user=user, verification_email_sent=False)
        deploy_log("auth.service.signup.email_send.start", user_id=user.id, **email_context)
        self._email_sender.send_email_verification(
            recipient_email=user.email,
            token=token,
            language=email_language,
        )
        deploy_log("auth.service.signup.email_send.completed", user_id=user.id, **email_context)
        return SignupResult(user=user, verification_email_sent=True)

    def login(self, *, email: str, password: str) -> AuthenticatedSession:
        normalized_email = _normalize_email(email)
        user = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if user is None:
            raise InvalidCredentialsError("invalid email or password")
        if user.account_type != "registered":
            raise InvalidCredentialsError("invalid email or password")
        try:
            is_valid = self._password_hasher.verify(user.password_hash, password)
        except VerifyMismatchError as exc:
            raise InvalidCredentialsError("invalid email or password") from exc
        if not is_valid:
            raise InvalidCredentialsError("invalid email or password")
        if user.approval_status == "pending":
            raise AccountApprovalRequiredError("account approval pending")
        if user.approval_status == "rejected":
            raise AccountRejectedError("account approval rejected")
        if user.approval_status != "approved":
            raise InvalidCredentialsError("invalid email or password")
        if user.email_verified_at is None:
            raise UnverifiedEmailError("email verification required")

        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = SessionModel(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=_digest(session_token),
            csrf_token_hash=_digest(csrf_token),
        )
        self._db.add(session)
        self._db.commit()
        self._db.refresh(session)
        return AuthenticatedSession(
            user=user,
            session=session,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    def verify_email(self, *, token: str) -> UserModel:
        auth_token = self._consume_token(token=token, purpose="email_verification")
        user = self._db.get(UserModel, auth_token.user_id)
        if user is None:
            raise InvalidAuthTokenError("invalid token")
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        return user

    def request_password_reset(
        self,
        *,
        email: str,
        email_language: AuthEmailLanguage = "ko",
    ) -> None:
        """Create a reset token for known users without revealing account existence."""
        normalized_email = _normalize_email(email)
        user = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if user is None or user.account_type != "registered" or user.approval_status != "approved":
            return
        token = self._create_token(
            user_id=user.id,
            purpose="password_reset",
            ttl=PASSWORD_RESET_TOKEN_TTL,
        )
        self._db.commit()
        self._email_sender.send_password_reset(
            recipient_email=user.email,
            token=token,
            language=email_language,
        )

    def confirm_password_reset(self, *, token: str, new_password: str) -> None:
        auth_token = self._consume_token(token=token, purpose="password_reset")
        user = self._db.get(UserModel, auth_token.user_id)
        if user is None:
            raise InvalidAuthTokenError("invalid token")
        user.password_hash = self._password_hasher.hash(new_password)
        self._revoke_sessions_for_user(user.id)
        self._db.add(user)
        self._db.commit()

    def update_nickname(
        self,
        *,
        user_id: str,
        current_password: str,
        nickname: str,
    ) -> UserModel:
        """Update registered-user display metadata after current-password proof."""
        user = self._registered_account_for_update(user_id)
        self._verify_current_password(user, current_password)
        user.nickname = nickname
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        return user

    def update_password(
        self,
        *,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None:
        """Replace a registered-user password and revoke all existing sessions."""
        user = self._registered_account_for_update(user_id)
        self._verify_current_password(user, current_password)
        user.password_hash = self._password_hasher.hash(new_password)
        self._revoke_sessions_for_user(user.id)
        self._db.add(user)
        self._db.commit()

    def authenticate_session(self, session_token: str | None) -> Principal:
        session = self._active_session(session_token)
        user = self._db.get(UserModel, session.user_id)
        if user is None:
            raise InvalidSessionError("invalid session")
        is_guest = user.account_type == "guest"
        if is_guest:
            guest_service = self._guest_service()
            guest_service.prepare()
            if not guest_service.owns_trial(user.id):
                raise InvalidSessionError("guest access unavailable")
            self._db.refresh(user)
        if is_guest and (
            user.guest_expires_at is None or _as_utc(user.guest_expires_at) <= datetime.now(UTC)
        ):
            raise InvalidSessionError("guest access expired")
        return Principal(
            user_id=session.user_id,
            session_id=session.id,
            is_guest=is_guest,
            user_type=user.user_type or UserType.NORMAL.value,
        )

    def logout(self, *, session_token: str | None, csrf_token: str | None) -> None:
        session = self._active_session(session_token)
        if not csrf_token or _digest(csrf_token) != session.csrf_token_hash:
            raise InvalidCsrfTokenError("invalid CSRF token")
        session.revoked_at = datetime.now(UTC)
        self._db.add(session)
        self._db.commit()

    def _guest_service(self):
        from my_agents.auth.guest_access import GuestAccessService

        return GuestAccessService(
            self._db,
            self._guest_settings or get_settings(),
            self._email_sender,
            self._notification_email,
        )

    def request_guest_access(
        self, *, email: str, client_identifier: str | None = None
    ) -> GuestAccessRequestModel:
        return self._guest_service().request(email, client=client_identifier)

    def approve_account_signup(
        self,
        *,
        email: str,
        mark_email_verified: bool = False,
    ) -> AccountApprovalResult:
        """Approve a registered account and either issue or bypass email verification."""
        normalized_email = _normalize_email(email)
        user = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if user is None or user.account_type != "registered":
            raise InvalidAuthTokenError("account not found")
        if user.approval_status == "rejected":
            raise InvalidAuthTokenError("account signup was rejected")
        if user.approval_status != "approved":
            user.approval_status = "approved"
            user.approved_at = datetime.now(UTC)
            user.rejected_at = None
        was_email_already_verified = user.email_verified_at is not None
        token = None
        if mark_email_verified:
            if user.email_verified_at is None:
                user.email_verified_at = datetime.now(UTC)
        else:
            token = self._create_token(
                user_id=user.id,
                purpose="email_verification",
                ttl=EMAIL_VERIFICATION_TOKEN_TTL,
            )
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        return AccountApprovalResult(
            user=user,
            verification_token=token,
            email_marked_verified=mark_email_verified,
            was_email_already_verified=was_email_already_verified,
        )

    def resend_account_verification(self, *, email: str) -> AccountApprovalResult:
        """Create a fresh email verification token for an approved, unverified account."""
        normalized_email = _normalize_email(email)
        user = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if user is None or user.account_type != "registered":
            raise InvalidAuthTokenError("account not found")
        if user.approval_status == "pending":
            raise AccountApprovalRequiredError("account approval pending")
        if user.approval_status == "rejected":
            raise AccountRejectedError("account approval rejected")
        if user.approval_status != "approved" or user.email_verified_at is not None:
            raise InvalidAuthTokenError("account is not eligible for verification resend")
        token = self._create_token(
            user_id=user.id,
            purpose="email_verification",
            ttl=EMAIL_VERIFICATION_TOKEN_TTL,
        )
        self._db.commit()
        self._db.refresh(user)
        return AccountApprovalResult(user=user, verification_token=token)

    def reject_account_signup(self, *, email: str) -> UserModel:
        """Reject a pending account signup without deleting the audit row."""
        normalized_email = _normalize_email(email)
        user = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if user is None or user.account_type != "registered":
            raise InvalidAuthTokenError("account not found")
        user.approval_status = "rejected"
        user.rejected_at = datetime.now(UTC)
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        return user

    def create_guest_access_code(self, *, ttl: timedelta) -> GuestAccessCodeResult:
        return self.issue_guest_access_code(email=None, ttl=ttl)

    def issue_guest_access_code(
        self, *, email: str | None, ttl: timedelta, request_id: str | None = None
    ) -> GuestAccessCodeResult:
        return self._guest_service().issue(email=email, ttl=ttl, request_id=request_id)

    def issue_and_send_guest_access_code(
        self,
        *,
        email: str,
        ttl: timedelta,
        email_language: AuthEmailLanguage = "ko",
        client_identifier: str | None = None,
    ) -> GuestAccessCodeResult:
        return self._guest_service().issue(
            email=email, ttl=ttl, send=True, language=email_language, client=client_identifier
        )

    def redeem_guest_access_code(
        self, *, code: str, access_ttl: timedelta, client_identifier: str | None = None
    ) -> AuthenticatedSession:
        return self._guest_service().redeem(code, access_ttl, client=client_identifier)

    def _active_session(self, session_token: str | None) -> SessionModel:
        if not session_token:
            raise InvalidSessionError("missing session")
        session = self._db.scalar(
            select(SessionModel).where(SessionModel.token_hash == _digest(session_token))
        )
        if (
            session is None
            or session.revoked_at is not None
            or (session.expires_at is not None and _as_utc(session.expires_at) <= datetime.now(UTC))
        ):
            raise InvalidSessionError("invalid session")
        return session

    def _registered_account_for_update(self, user_id: str) -> UserModel:
        user = self._db.get(UserModel, user_id)
        if user is None:
            raise InvalidSessionError("invalid session")
        if user.account_type != "registered":
            raise AccountMutationNotAllowedError("registered account required")
        return user

    def _verify_current_password(self, user: UserModel, current_password: str) -> None:
        try:
            is_valid = self._password_hasher.verify(user.password_hash, current_password)
        except VerifyMismatchError as exc:
            raise InvalidCredentialsError("invalid current password") from exc
        if not is_valid:
            raise InvalidCredentialsError("invalid current password")

    def _create_token(self, *, user_id: str, purpose: AuthTokenPurpose, ttl: timedelta) -> str:
        token = secrets.token_urlsafe(32)
        self._db.add(
            AuthTokenModel(
                id=str(uuid.uuid4()),
                user_id=user_id,
                purpose=purpose,
                token_hash=_digest(token),
                expires_at=datetime.now(UTC) + ttl,
            )
        )
        return token

    def _consume_token(self, *, token: str, purpose: AuthTokenPurpose) -> AuthTokenModel:
        if not token.strip():
            raise InvalidAuthTokenError("invalid token")
        auth_token = self._db.scalar(
            select(AuthTokenModel).where(AuthTokenModel.token_hash == _digest(token))
        )
        if (
            auth_token is None
            or auth_token.purpose != purpose
            or auth_token.consumed_at is not None
            or _as_utc(auth_token.expires_at) <= datetime.now(UTC)
        ):
            raise InvalidAuthTokenError("invalid or expired token")
        auth_token.consumed_at = datetime.now(UTC)
        self._db.add(auth_token)
        self._db.flush()
        return auth_token

    def _revoke_sessions_for_user(self, user_id: str) -> None:
        now = datetime.now(UTC)
        sessions = self._db.scalars(
            select(SessionModel).where(
                SessionModel.user_id == user_id,
                SessionModel.revoked_at.is_(None),
            )
        ).all()
        for session in sessions:
            session.revoked_at = now
            self._db.add(session)


def _normalize_email(email: str) -> str:
    return email.strip().casefold()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
