"""Email-bound guest code issuance and atomic trial creation or reauthentication."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from my_agents.auth.email import AuthEmailLanguage, AuthEmailSender
from my_agents.auth.guest_policy import (
    GuestIpRateLimited,
    GuestRequestSuppressed,
    eligible_user,
    fingerprint,
    initialize_guest_policy,
    lock_trial,
    take_budget,
    utc,
)
from my_agents.auth.models import (
    GuestAccessCodeModel,
    GuestAccessRequestModel,
    GuestTrialModel,
    SessionModel,
    UserModel,
)
from my_agents.auth.notifications import enqueue_registration
from my_agents.auth.service import (
    AuthenticatedSession,
    GuestAccessCodeResult,
    InvalidAuthTokenError,
)
from my_agents.settings import Settings


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class GuestAccessService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        sender: AuthEmailSender,
        notification_email: str | None,
    ):
        self.db, self.settings, self.sender = db, settings, sender
        self.notification_email = notification_email
        self.key = (
            settings.guest_cleanup_email_hmac_key.get_secret_value()
            if settings.guest_cleanup_email_hmac_key
            else ""
        )

    def prepare(self) -> None:
        try:
            initialize_guest_policy(self.db, self.key)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def owns_trial(self, user_id: str) -> bool:
        """Reject legacy duplicate/late-created sessions, not just fresh redemptions."""
        emails = set(
            self.db.scalars(
                select(GuestAccessRequestModel.email)
                .join(
                    GuestAccessCodeModel,
                    GuestAccessCodeModel.request_id == GuestAccessRequestModel.id,
                )
                .where(GuestAccessCodeModel.guest_user_id == user_id)
            )
        )
        normalized = {email.strip().casefold() for email in emails}
        if len(normalized) != 1:
            return False
        email = next(iter(normalized))
        trial = self.db.get(GuestTrialModel, fingerprint(email, self.key), populate_existing=True)
        if trial is None or trial.guest_user_id != user_id or trial.redeemed_at is None:
            return False
        try:
            eligible_user(self.db, trial, email)
        except GuestRequestSuppressed:
            return False
        return True

    def ip_budget(self, client: str | None, *, login: bool) -> None:
        if client is None:  # Trusted operator service calls have no network origin.
            return
        allowed = take_budget(
            self.db,
            key=self.key,
            action="login-ip" if login else "request-ip",
            subject=client,
            limit=self.settings.guest_login_ip_window_limit
            if login
            else self.settings.guest_request_ip_hourly_limit,
            window_seconds=900 if login else 3600,
        )
        self.db.commit()
        if not allowed:
            raise GuestIpRateLimited("too many guest access attempts")

    def request(self, email: str, *, client: str | None = None) -> GuestAccessRequestModel:
        self.prepare()
        self.ip_budget(client, login=False)
        email = email.strip().casefold()
        try:
            trial = lock_trial(self.db, fingerprint(email, self.key))
            eligible_user(self.db, trial, email)
            request = self._request_row(email)
            self.db.commit()
            return request
        except Exception:
            self.db.rollback()
            raise

    def _request_row(self, email: str, request_id: str | None = None) -> GuestAccessRequestModel:
        if request_id:
            request = self.db.get(GuestAccessRequestModel, request_id)
            if request is None or request.email.strip().casefold() != email or request.rejected_at:
                raise InvalidAuthTokenError("guest access request not found")
        else:
            request = self.db.scalar(
                select(GuestAccessRequestModel)
                .where(
                    GuestAccessRequestModel.email == email,
                    GuestAccessRequestModel.status.in_(["pending", "issued"]),
                    GuestAccessRequestModel.rejected_at.is_(None),
                )
                .order_by(
                    GuestAccessRequestModel.created_at.desc(), GuestAccessRequestModel.id.desc()
                )
            )
        if request is None:
            request = GuestAccessRequestModel(id=str(uuid.uuid4()), email=email, status="pending")
            self.db.add(request)
            self.db.flush()
        return request

    def issue(
        self,
        *,
        email: str | None,
        ttl: timedelta,
        send: bool = False,
        language: AuthEmailLanguage = "ko",
        request_id: str | None = None,
        client: str | None = None,
    ) -> GuestAccessCodeResult:
        if not email or not email.strip():
            raise GuestRequestSuppressed("guest codes require an email identity")
        self.prepare()
        self.ip_budget(client, login=False)
        email = email.strip().casefold()
        try:
            trial = lock_trial(self.db, fingerprint(email, self.key))
            active_user = eligible_user(self.db, trial, email)
            now = datetime.now(UTC)
            if trial.last_sent_at and now < utc(trial.last_sent_at) + timedelta(
                seconds=self.settings.guest_code_resend_cooldown_seconds
            ):
                raise GuestRequestSuppressed("guest code resend cooldown")
            if not take_budget(
                self.db,
                key=self.key,
                action="email-day",
                subject=email,
                limit=self.settings.guest_code_email_daily_limit,
                window_seconds=86400,
            ):
                raise GuestRequestSuppressed("guest email daily limit")
            request = self._request_row(email, request_id)
            raw_code = secrets.token_urlsafe(18)
            expires = now + ttl
            if active_user:
                expires = min(expires, utc(active_user.guest_expires_at))
            # All old codes are invalidated while holding the identity lock.
            self.db.execute(
                update(GuestAccessCodeModel)
                .where(
                    GuestAccessCodeModel.email_fingerprint == trial.email_fingerprint,
                    GuestAccessCodeModel.consumed_at.is_(None),
                )
                .values(expires_at=now)
                .execution_options(synchronize_session=False)
            )
            code = GuestAccessCodeModel(
                id=str(uuid.uuid4()),
                request_id=request.id,
                email_fingerprint=trial.email_fingerprint,
                generation=trial.generation,
                code_hash=digest(raw_code),
                expires_at=expires,
            )
            self.db.add(code)
            request.status = "issued"
            request.approved_at = request.approved_at or now
            request.sent_at = now
            trial.last_sent_at = now
            self.db.flush()
            if send:
                self.sender.send_guest_access_code(
                    recipient_email=email, code=raw_code, expires_at=expires, language=language
                )
            self.db.commit()
            return GuestAccessCodeResult(
                code=raw_code, expires_at=expires, request_id=request.id, email=email
            )
        except Exception:
            self.db.rollback()
            raise

    def redeem(
        self, code: str, access_ttl: timedelta, *, client: str | None = None
    ) -> AuthenticatedSession:
        self.prepare()
        self.ip_budget(client, login=True)
        try:
            stored = self.db.scalar(
                select(GuestAccessCodeModel).where(GuestAccessCodeModel.code_hash == digest(code))
            )
            if stored is None or stored.email_fingerprint is None or stored.request_id is None:
                raise InvalidAuthTokenError("invalid or expired code")
            trial = lock_trial(self.db, stored.email_fingerprint)
            self.db.refresh(stored)
            now = datetime.now(UTC)
            if (
                stored.consumed_at is not None
                or utc(stored.expires_at) <= now
                or stored.generation != trial.generation
            ):
                raise InvalidAuthTokenError("invalid or expired code")
            request = self.db.get(GuestAccessRequestModel, stored.request_id)
            if (
                request is None
                or request.rejected_at
                or fingerprint(request.email, self.key) != trial.email_fingerprint
            ):
                raise InvalidAuthTokenError("invalid or expired code")
            email = request.email.strip().casefold()
            user = eligible_user(self.db, trial, email)
            consumed = self.db.execute(
                update(GuestAccessCodeModel)
                .where(
                    GuestAccessCodeModel.id == stored.id,
                    GuestAccessCodeModel.consumed_at.is_(None),
                    GuestAccessCodeModel.expires_at > now,
                    GuestAccessCodeModel.generation == trial.generation,
                )
                .values(consumed_at=now)
                .execution_options(synchronize_session=False)
            )
            if consumed.rowcount != 1:
                raise InvalidAuthTokenError("invalid or expired code")
            if user is None:
                user = UserModel(
                    id=str(uuid.uuid4()),
                    email=f"guest-{uuid.uuid4().hex}@guest.example.com",
                    nickname="Guest",
                    password_hash="guest-login-disabled",
                    account_type="guest",
                    user_type="normal",
                    approval_status="approved",
                    guest_expires_at=now + access_ttl,
                )
                self.db.add(user)
                self.db.flush()
                trial.guest_user_id = user.id
                trial.redeemed_at = now
                enqueue_registration(
                    self.db,
                    user,
                    recipient=self.notification_email,
                    account_email=email,
                    kind="guest_redemption",
                )
            session_token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            session = SessionModel(
                id=str(uuid.uuid4()),
                user_id=user.id,
                token_hash=digest(session_token),
                csrf_token_hash=digest(csrf_token),
                expires_at=user.guest_expires_at,
            )
            self.db.add(session)
            stored.guest_user_id = user.id
            request.status = "consumed"
            self.db.commit()
            self.db.refresh(user)
            self.db.refresh(session)
            return AuthenticatedSession(
                user=user, session=session, session_token=session_token, csrf_token=csrf_token
            )
        except GuestRequestSuppressed as exc:
            self.db.rollback()
            raise InvalidAuthTokenError("invalid or expired code") from exc
        except Exception:
            self.db.rollback()
            raise
