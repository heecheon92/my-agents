"""Operator registration alerts queued atomically with account creation."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from my_agents.auth.email import AuthEmailSender, build_auth_email_sender
from my_agents.auth.models import RegistrationNotificationModel, UserModel
from my_agents.settings import Settings

logger = logging.getLogger(__name__)
RegistrationKind = Literal["registered_signup", "guest_redemption", "invitation_signup"]


def enqueue_registration(
    db: Session,
    user: UserModel,
    *,
    recipient: str | None,
    account_email: str | None,
    kind: RegistrationKind,
) -> None:
    """Caller commits the event with the user; disabled means no event or backfill."""
    if not recipient:
        return
    now = datetime.now(UTC)
    db.add(
        RegistrationNotificationModel(
            id=str(uuid.uuid4()),
            user_id=user.id,
            kind=kind,
            recipient_email=recipient,
            account_email=account_email,
            approval_status=user.approval_status or "approved",
            created_at=now,
            guest_expires_at=user.guest_expires_at,
            available_at=now,
        )
    )


def deliver_pending(
    db: Session, sender: AuthEmailSender, *, lease_seconds: float = 120, stop: Event | None = None
) -> int:
    """Lease a bounded batch; failures retry and delivery success clears email addresses.

    Delivery is at least once: a crash after provider acceptance but before committing
    success may cause a duplicate alert. A unique user_id prevents duplicate queue events.
    """
    now = datetime.now(UTC)
    eligible = (
        RegistrationNotificationModel.sent_at.is_(None),
        RegistrationNotificationModel.available_at <= now,
        or_(
            RegistrationNotificationModel.lease_until.is_(None),
            RegistrationNotificationModel.lease_until <= now,
        ),
    )
    ids = db.scalars(
        select(RegistrationNotificationModel.id)
        .where(*eligible)
        .order_by(RegistrationNotificationModel.created_at, RegistrationNotificationModel.id)
        .limit(25)
    ).all()
    delivered = 0
    for job_id in ids:
        if stop is not None and stop.is_set():
            break
        token = str(uuid.uuid4())
        claim = db.execute(
            update(RegistrationNotificationModel)
            .execution_options(synchronize_session=False)
            .where(RegistrationNotificationModel.id == job_id, *eligible)
            .values(
                lease_token=token,
                lease_until=datetime.now(UTC) + timedelta(seconds=lease_seconds),
                attempts=RegistrationNotificationModel.attempts + 1,
            )
        )
        db.commit()
        if claim.rowcount != 1:
            continue
        job = db.get(RegistrationNotificationModel, job_id, populate_existing=True)
        assert job is not None
        owned = (
            RegistrationNotificationModel.id == job_id,
            RegistrationNotificationModel.lease_token == token,
        )
        if stop is not None and stop.is_set():
            db.execute(
                update(RegistrationNotificationModel)
                .where(*owned)
                .values(lease_token=None, lease_until=None)
            )
            db.commit()
            break
        try:
            if not job.recipient_email:
                raise ValueError("notification recipient missing")
            sender.send_registration_notification(
                recipient_email=job.recipient_email,
                subject=f"[my-agents] Account created: {job.kind}",
                body=(
                    f"Event: {job.kind}\n"
                    f"Account ID: {job.user_id}\n"
                    f"Email: {job.account_email or 'Not provided (operator-issued guest code)'}\n"
                    f"Created (UTC): {_utc_iso(job.created_at)}\n"
                    f"Approval status at creation: {job.approval_status}\n"
                    f"Guest expires (UTC): {_utc_iso(job.guest_expires_at)}\n"
                ),
            )
        except Exception as exc:
            db.execute(
                update(RegistrationNotificationModel)
                .execution_options(synchronize_session=False)
                .where(*owned)
                .values(
                    lease_token=None,
                    lease_until=None,
                    available_at=datetime.now(UTC)
                    + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7))),
                )
            )
            db.commit()
            logger.warning(
                "registration_notification.retry event_id=%s error_class=%s",
                job_id,
                type(exc).__name__,
            )
            continue
        db.execute(
            update(RegistrationNotificationModel)
            .execution_options(synchronize_session=False)
            .where(*owned)
            .values(
                sent_at=datetime.now(UTC),
                lease_token=None,
                lease_until=None,
                recipient_email=None,
                account_email=None,
            )
        )
        db.commit()
        delivered += 1
    return delivered


def _utc_iso(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    return (
        value.replace(tzinfo=UTC).isoformat()
        if value.tzinfo is None
        else value.astimezone(UTC).isoformat()
    )


async def notification_loop(settings: Settings) -> None:
    """Drain every 30 seconds while configured, with network work off the event loop."""
    from my_agents.persistence.database import (
        _sessionmaker_for_url,
        initialize_database,
        supports_background_sessions,
    )

    if not settings.registration_notification_email:
        return
    if not supports_background_sessions(settings.database_url):
        logger.warning("registration_notification.disabled requires_file_sqlite_or_postgresql")
        return
    stop = Event()
    sender = build_auth_email_sender(settings)
    while True:

        def sweep() -> None:
            initialize_database(settings)
            with _sessionmaker_for_url(settings.database_url)() as db:
                deliver_pending(
                    db,
                    sender,
                    lease_seconds=max(120, settings.auth_smtp_timeout_seconds * 4 + 60),
                    stop=stop,
                )

        task = asyncio.create_task(asyncio.to_thread(sweep))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            stop.set()
            try:
                await task
            except Exception as exc:
                logger.warning(
                    "registration_notification.shutdown_failed error_class=%s", type(exc).__name__
                )
            raise
        except Exception as exc:
            logger.warning(
                "registration_notification.sweep_failed error_class=%s", type(exc).__name__
            )
        await asyncio.sleep(30)
