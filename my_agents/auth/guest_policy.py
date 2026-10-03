"""Durable guest identity, rollout reconciliation, and cross-worker request budgets."""

from __future__ import annotations

import hashlib
import hmac
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from my_agents.auth.models import (
    GuestAccessCodeModel,
    GuestAccessRequestModel,
    GuestDeletionAuditModel,
    GuestPolicyStateModel,
    GuestRateBucketModel,
    GuestTrialModel,
    SessionModel,
    UserModel,
)


class GuestRequestSuppressed(RuntimeError):
    """Ineligible email or email cooldown: public requests remain generically accepted."""


class GuestIpRateLimited(RuntimeError):
    """Shared request-origin budget exhausted."""


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def fingerprint(email: str, key: str) -> str:
    if len(key.encode()) < 32:
        raise ValueError("Guest identity requires a stable HMAC key of at least 32 bytes")
    # Same normalization and HMAC as the existing deletion audits.
    return hmac.new(key.encode(), email.strip().casefold().encode(), hashlib.sha256).hexdigest()


def insert_for(db: Session, model: type):
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return pg_insert(model)
    if dialect == "sqlite":
        return sqlite_insert(model)
    raise RuntimeError("Guest policy requires PostgreSQL or SQLite")


def validate_policy_key(db: Session, key: str) -> None:
    state = db.get(GuestPolicyStateModel, 1, populate_existing=True)
    if state is not None and not hmac.compare_digest(
        state.key_verifier, fingerprint("guest-policy-key-verifier-v1", key)
    ):
        raise RuntimeError("Guest identity HMAC key changed; restore the original key")


def initialize_guest_policy(db: Session, key: str) -> None:
    """Bootstrap once, inside the caller's transaction; competing starters wait on the PK."""
    verifier = fingerprint("guest-policy-key-verifier-v1", key)
    state = db.get(GuestPolicyStateModel, 1)
    if state is not None:
        validate_policy_key(db, key)
        return
    created = db.scalar(
        insert_for(db, GuestPolicyStateModel)
        .values(id=1, key_verifier=verifier, initialized_at=datetime.now(UTC))
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(GuestPolicyStateModel.id)
    )
    if created is None:
        validate_policy_key(db, key)
        return
    _backfill(db, key)
    db.flush()


def _backfill(db: Session, key: str) -> None:
    now = datetime.now(UTC)
    requests = db.scalars(select(GuestAccessRequestModel)).all()
    by_email: dict[str, list[GuestAccessRequestModel]] = defaultdict(list)
    for request in requests:
        by_email[request.email.strip().casefold()].append(request)
    audits: dict[str, datetime] = {}
    for audit in db.scalars(
        select(GuestDeletionAuditModel).where(
            GuestDeletionAuditModel.email_fingerprint.is_not(None)
        )
    ):
        fp = audit.email_fingerprint
        previous = audits.get(fp)
        audits[fp] = (
            min(utc(audit.guest_created_at), previous) if previous else utc(audit.guest_created_at)
        )
    for email, rows in by_email.items():
        fp = fingerprint(email, key)
        request_ids = [row.id for row in rows]
        codes = db.scalars(
            select(GuestAccessCodeModel).where(GuestAccessCodeModel.request_id.in_(request_ids))
        ).all()
        ids = {code.guest_user_id for code in codes if code.guest_user_id}
        users = db.scalars(
            select(UserModel).where(UserModel.id.in_(ids), UserModel.account_type == "guest")
        ).all()
        registered = db.scalar(
            select(UserModel.id).where(
                UserModel.email == email, UserModel.account_type == "registered"
            )
        )
        active = sorted(
            (user for user in users if user.guest_expires_at and utc(user.guest_expires_at) > now),
            key=lambda user: (utc(user.created_at), user.id),
        )
        keeper = active[0] if active and not registered else None
        for user in active:
            if keeper is None or user.id != keeper.id:
                user.guest_expires_at = now
                db.execute(
                    update(SessionModel)
                    .where(SessionModel.user_id == user.id, SessionModel.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
        history = [utc(code.consumed_at) for code in codes if code.consumed_at] + [
            utc(user.created_at) for user in users
        ]
        history += [utc(row.created_at) for row in rows if row.status == "consumed"]
        if fp in audits:
            history.append(audits.pop(fp))
        last_sent = max((utc(row.sent_at) for row in rows if row.sent_at), default=None)
        db.add(
            GuestTrialModel(
                email_fingerprint=fp,
                guest_user_id=keeper.id if keeper else None,
                redeemed_at=min(history) if history else None,
                generation=0,
                last_sent_at=last_sent,
                created_at=min(utc(row.created_at) for row in rows),
            )
        )
        # Legacy duplicate pending requests must not leave multiple usable codes.
        usable = sorted(
            (code for code in codes if code.consumed_at is None and utc(code.expires_at) > now),
            key=lambda code: (utc(code.created_at), code.id),
            reverse=True,
        )
        for code in codes:
            code.email_fingerprint = fp
            code.generation = 0
        for code in usable[1:]:
            code.expires_at = now
        if registered or (history and keeper is None):
            for code in usable:
                code.expires_at = now
    for fp, redeemed_at in audits.items():
        db.add(
            GuestTrialModel(
                email_fingerprint=fp,
                guest_user_id=None,
                redeemed_at=redeemed_at,
                generation=0,
                created_at=redeemed_at,
            )
        )


def lock_trial(db: Session, fp: str) -> GuestTrialModel:
    db.execute(
        insert_for(db, GuestTrialModel)
        .values(email_fingerprint=fp, generation=0, created_at=datetime.now(UTC))
        .on_conflict_do_nothing(index_elements=["email_fingerprint"])
    )
    # An UPDATE takes a write lock on SQLite and a row lock on PostgreSQL.
    db.execute(
        update(GuestTrialModel)
        .where(GuestTrialModel.email_fingerprint == fp)
        .values(generation=GuestTrialModel.generation)
        .execution_options(synchronize_session=False)
    )
    return db.scalar(
        select(GuestTrialModel)
        .where(GuestTrialModel.email_fingerprint == fp)
        .execution_options(populate_existing=True)
    )


def eligible_user(db: Session, trial: GuestTrialModel, email: str) -> UserModel | None:
    if db.scalar(
        select(UserModel.id).where(UserModel.email == email, UserModel.account_type == "registered")
    ):
        raise GuestRequestSuppressed("registered account exists")
    if trial.redeemed_at is None:
        return None
    user = (
        db.get(UserModel, trial.guest_user_id, populate_existing=True)
        if trial.guest_user_id
        else None
    )
    if (
        user is None
        or user.account_type != "guest"
        or user.guest_expires_at is None
        or utc(user.guest_expires_at) <= datetime.now(UTC)
    ):
        raise GuestRequestSuppressed("guest trial already used")
    return user


def take_budget(
    db: Session, *, key: str, action: str, subject: str, limit: int, window_seconds: int
) -> bool:
    """Consume one fixed-window attempt atomically; caller owns the commit."""
    now = datetime.now(UTC)
    slot = int(now.timestamp()) // window_seconds
    bucket = fingerprint(f"guest-rate:{action}:{slot}:{subject}", key)
    expires = datetime.fromtimestamp((slot + 1) * window_seconds, tz=UTC)
    db.execute(
        delete(GuestRateBucketModel)
        .where(GuestRateBucketModel.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    count = db.scalar(
        insert_for(db, GuestRateBucketModel)
        .values(id=bucket, count=1, expires_at=expires)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"count": GuestRateBucketModel.count + 1},
            where=GuestRateBucketModel.count < limit,
        )
        .returning(GuestRateBucketModel.count)
    )
    return count is not None


def reset_trial(db: Session, *, email: str, key: str) -> None:
    """Operator-only action. End any active trial and invalidate all previous-generation codes."""
    initialize_guest_policy(db, key)
    trial = lock_trial(db, fingerprint(email, key))
    now = datetime.now(UTC)
    if trial.guest_user_id:
        db.execute(
            update(UserModel)
            .where(
                UserModel.id == trial.guest_user_id,
                UserModel.account_type == "guest",
                UserModel.guest_expires_at > now,
            )
            .values(guest_expires_at=now)
            .execution_options(synchronize_session=False)
        )
        db.execute(
            update(SessionModel)
            .where(SessionModel.user_id == trial.guest_user_id)
            .values(revoked_at=now)
        )
    db.execute(
        update(GuestAccessCodeModel)
        .where(
            GuestAccessCodeModel.email_fingerprint == trial.email_fingerprint,
            GuestAccessCodeModel.consumed_at.is_(None),
        )
        .values(expires_at=now)
        .execution_options(synchronize_session=False)
    )
    trial.generation += 1
    trial.guest_user_id = None
    trial.redeemed_at = None
    trial.last_sent_at = None
    trial.last_reset_at = now
    db.commit()


def lock_signup_identity(db: Session, email: str, settings) -> None:
    """Serialize registered signup against guest issuance for the same email."""
    if not settings.guest_access_enabled:
        return
    key = settings.guest_cleanup_email_hmac_key.get_secret_value()
    initialize_guest_policy(db, key)
    lock_trial(db, fingerprint(email, key))
