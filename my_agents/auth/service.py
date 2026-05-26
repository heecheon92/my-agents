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

from my_agents.auth.contracts import Principal
from my_agents.auth.email import AuthEmailSender, get_auth_email_sender
from my_agents.auth.models import (
    AuthTokenModel,
    GuestAccessCodeModel,
    GuestAccessRequestModel,
    SessionModel,
    UserModel,
)
from my_agents.diagnostics import deploy_log, safe_email_context

AuthTokenPurpose = Literal["email_verification", "password_reset"]
EMAIL_VERIFICATION_TOKEN_TTL = timedelta(hours=24)
PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)


class AuthError(RuntimeError):
    """Base auth-service error."""


class DuplicateEmailError(AuthError):
    """Raised when signup attempts to reuse an email."""


class InvalidCredentialsError(AuthError):
    """Raised when login credentials are invalid."""


class UnverifiedEmailError(AuthError):
    """Raised when a user must verify their email before logging in."""


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


class AuthService:
    """Own first-party users, password hashes, sessions, and account lifecycle tokens."""

    def __init__(
        self,
        db: Session,
        password_hasher: PasswordHasher | None = None,
        email_sender: AuthEmailSender | None = None,
    ) -> None:
        self._db = db
        self._password_hasher = password_hasher or PasswordHasher()
        self._email_sender = email_sender or get_auth_email_sender()

    def signup(self, *, email: str, password: str) -> SignupResult:
        normalized_email = _normalize_email(email)
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
            password_hash=password_hash,
            account_type="registered",
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
        token = self._create_token(
            user_id=user.id,
            purpose="email_verification",
            ttl=EMAIL_VERIFICATION_TOKEN_TTL,
        )
        deploy_log("auth.service.signup.token_created", user_id=user.id, **email_context)
        self._db.commit()
        deploy_log("auth.service.signup.db_committed", user_id=user.id, **email_context)
        self._db.refresh(user)
        deploy_log("auth.service.signup.email_send.start", user_id=user.id, **email_context)
        self._email_sender.send_email_verification(recipient_email=user.email, token=token)
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

    def request_password_reset(self, *, email: str) -> None:
        """Create a reset token for known users without revealing account existence."""
        normalized_email = _normalize_email(email)
        user = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if user is None or user.account_type != "registered":
            return
        token = self._create_token(
            user_id=user.id,
            purpose="password_reset",
            ttl=PASSWORD_RESET_TOKEN_TTL,
        )
        self._db.commit()
        self._email_sender.send_password_reset(recipient_email=user.email, token=token)

    def confirm_password_reset(self, *, token: str, new_password: str) -> None:
        auth_token = self._consume_token(token=token, purpose="password_reset")
        user = self._db.get(UserModel, auth_token.user_id)
        if user is None:
            raise InvalidAuthTokenError("invalid token")
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
        if is_guest and (
            user.guest_expires_at is None or _as_utc(user.guest_expires_at) <= datetime.now(UTC)
        ):
            raise InvalidSessionError("guest access expired")
        return Principal(user_id=session.user_id, session_id=session.id, is_guest=is_guest)

    def logout(self, *, session_token: str | None, csrf_token: str | None) -> None:
        session = self._active_session(session_token)
        if not csrf_token or _digest(csrf_token) != session.csrf_token_hash:
            raise InvalidCsrfTokenError("invalid CSRF token")
        session.revoked_at = datetime.now(UTC)
        self._db.add(session)
        self._db.commit()

    def request_guest_access(self, *, email: str) -> GuestAccessRequestModel:
        """Record a manually reviewed guest access request without returning a code."""
        normalized_email = _normalize_email(email)
        if not normalized_email:
            raise ValueError("email must not be blank")
        request = GuestAccessRequestModel(
            id=str(uuid.uuid4()),
            email=normalized_email,
            status="pending",
        )
        self._db.add(request)
        self._db.commit()
        self._db.refresh(request)
        return request

    def create_guest_access_code(self, *, ttl: timedelta) -> GuestAccessCodeResult:
        """Create a short-lived one-time guest access code without an email request."""
        return self.issue_guest_access_code(email=None, ttl=ttl)

    def issue_guest_access_code(
        self,
        *,
        email: str | None,
        ttl: timedelta,
        request_id: str | None = None,
    ) -> GuestAccessCodeResult:
        """Issue a one-time guest code for manual delivery to a requested email."""
        request: GuestAccessRequestModel | None = None
        if email is not None:
            normalized_email = _normalize_email(email)
            if not normalized_email:
                raise ValueError("email must not be blank")
            if request_id is not None:
                request = self._db.get(GuestAccessRequestModel, request_id)
                if request is None or request.email != normalized_email:
                    raise InvalidAuthTokenError("guest access request not found")
            else:
                request = self._db.scalar(
                    select(GuestAccessRequestModel)
                    .where(
                        GuestAccessRequestModel.email == normalized_email,
                        GuestAccessRequestModel.status.in_(["pending", "issued"]),
                        GuestAccessRequestModel.rejected_at.is_(None),
                    )
                    .order_by(
                        GuestAccessRequestModel.created_at.desc(),
                        GuestAccessRequestModel.id.desc(),
                    )
                )
            if request is None:
                request = GuestAccessRequestModel(
                    id=str(uuid.uuid4()),
                    email=normalized_email,
                    status="pending",
                )
                self._db.add(request)
                self._db.flush()

        code = secrets.token_urlsafe(18)
        expires_at = datetime.now(UTC) + ttl
        guest_code = None
        if request is not None:
            guest_code = self._db.scalar(
                select(GuestAccessCodeModel)
                .where(
                    GuestAccessCodeModel.request_id == request.id,
                    GuestAccessCodeModel.consumed_at.is_(None),
                )
                .order_by(GuestAccessCodeModel.created_at.desc(), GuestAccessCodeModel.id.desc())
            )
        if guest_code is None:
            guest_code = GuestAccessCodeModel(
                id=str(uuid.uuid4()),
                request_id=request.id if request is not None else None,
                code_hash=_digest(code),
                expires_at=expires_at,
            )
        else:
            guest_code.code_hash = _digest(code)
            guest_code.expires_at = expires_at
        self._db.add(guest_code)
        if request is not None:
            request.status = "issued"
            request.approved_at = request.approved_at or datetime.now(UTC)
            request.sent_at = datetime.now(UTC)
            self._db.add(request)
        self._db.commit()
        return GuestAccessCodeResult(
            code=code,
            expires_at=expires_at,
            request_id=request.id if request is not None else None,
            email=normalized_email if email is not None else None,
        )

    def redeem_guest_access_code(
        self,
        *,
        code: str,
        access_ttl: timedelta,
    ) -> AuthenticatedSession:
        """Redeem a one-time guest code and issue a normal app session cookie."""
        if not code.strip():
            raise InvalidAuthTokenError("invalid code")
        guest_code = self._db.scalar(
            select(GuestAccessCodeModel).where(GuestAccessCodeModel.code_hash == _digest(code))
        )
        if (
            guest_code is None
            or guest_code.consumed_at is not None
            or _as_utc(guest_code.expires_at) <= datetime.now(UTC)
        ):
            raise InvalidAuthTokenError("invalid or expired code")

        access_expires_at = datetime.now(UTC) + access_ttl
        user = UserModel(
            id=str(uuid.uuid4()),
            email=f"guest-{uuid.uuid4().hex}@guest.example.com",
            password_hash="guest-login-disabled",
            email_verified_at=None,
            account_type="guest",
            guest_expires_at=access_expires_at,
        )
        self._db.add(user)
        self._db.flush()
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = SessionModel(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=_digest(session_token),
            csrf_token_hash=_digest(csrf_token),
            expires_at=access_expires_at,
        )
        guest_code.consumed_at = datetime.now(UTC)
        guest_code.guest_user_id = user.id
        self._db.add_all([session, guest_code])
        if guest_code.request_id is not None:
            request = self._db.get(GuestAccessRequestModel, guest_code.request_id)
            if request is not None:
                request.status = "consumed"
                self._db.add(request)
        self._db.commit()
        self._db.refresh(user)
        self._db.refresh(session)
        return AuthenticatedSession(
            user=user,
            session=session,
            session_token=session_token,
            csrf_token=csrf_token,
        )

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
