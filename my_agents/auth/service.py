"""First-party authentication and owned-session service."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.models import SessionModel, UserModel


class AuthError(RuntimeError):
    """Base auth-service error."""


class DuplicateEmailError(AuthError):
    """Raised when signup attempts to reuse an email."""


class InvalidCredentialsError(AuthError):
    """Raised when login credentials are invalid."""


class InvalidSessionError(AuthError):
    """Raised when a session token is absent, unknown, or revoked."""


class InvalidCsrfTokenError(AuthError):
    """Raised when a mutating cookie-auth request lacks valid CSRF proof."""


@dataclass(frozen=True)
class AuthenticatedSession:
    """Session material returned once when a user logs in."""

    user: UserModel
    session: SessionModel
    session_token: str
    csrf_token: str


class AuthService:
    """Own first-party users, password hashes, and revocable sessions."""

    def __init__(self, db: Session, password_hasher: PasswordHasher | None = None) -> None:
        self._db = db
        self._password_hasher = password_hasher or PasswordHasher()

    def signup(self, *, email: str, password: str) -> UserModel:
        normalized_email = _normalize_email(email)
        existing = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if existing is not None:
            raise DuplicateEmailError("email is already registered")
        user = UserModel(
            id=str(uuid.uuid4()),
            email=normalized_email,
            password_hash=self._password_hasher.hash(password),
        )
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        return user

    def login(self, *, email: str, password: str) -> AuthenticatedSession:
        normalized_email = _normalize_email(email)
        user = self._db.scalar(select(UserModel).where(UserModel.email == normalized_email))
        if user is None:
            raise InvalidCredentialsError("invalid email or password")
        try:
            is_valid = self._password_hasher.verify(user.password_hash, password)
        except VerifyMismatchError as exc:
            raise InvalidCredentialsError("invalid email or password") from exc
        if not is_valid:
            raise InvalidCredentialsError("invalid email or password")

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

    def authenticate_session(self, session_token: str | None) -> Principal:
        session = self._active_session(session_token)
        return Principal(user_id=session.user_id, session_id=session.id)

    def logout(self, *, session_token: str | None, csrf_token: str | None) -> None:
        session = self._active_session(session_token)
        if not csrf_token or _digest(csrf_token) != session.csrf_token_hash:
            raise InvalidCsrfTokenError("invalid CSRF token")
        session.revoked_at = datetime.now(UTC)
        self._db.add(session)
        self._db.commit()

    def _active_session(self, session_token: str | None) -> SessionModel:
        if not session_token:
            raise InvalidSessionError("missing session")
        session = self._db.scalar(
            select(SessionModel).where(SessionModel.token_hash == _digest(session_token))
        )
        if session is None or session.revoked_at is not None:
            raise InvalidSessionError("invalid session")
        return session


def _normalize_email(email: str) -> str:
    return email.strip().casefold()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
