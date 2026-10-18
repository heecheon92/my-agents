"""Transactional file reservations and retryable private-object cleanup.

Reservation and scheduling helpers never commit. A reservation must be committed
before writing its object; otherwise a process crash could leave untracked bytes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from threading import Event

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from my_agents.knowledge.models import DocumentModel
from my_agents.settings import Settings, get_settings
from my_agents.storage import ObjectStore, StorageUnavailable, get_object_store
from my_agents.storage.models import StorageCleanupModel, StorageQuotaModel, StoredFileModel

logger = logging.getLogger(__name__)
WRITE_SETTLE_GRACE = timedelta(minutes=5)


class StorageQuotaExceeded(ValueError):
    """The configured storage budget cannot accommodate another reservation."""


def _insert(db: Session, model):  # noqa: ANN001, ANN202
    if db.get_bind().dialect.name == "postgresql":
        return pg_insert(model)
    return sqlite_insert(model)


def reserve_file(
    db: Session,
    settings: Settings,
    *,
    owner_user_id: str,
    byte_size: int,
    sha256: str,
    filename: str,
    content_type: str,
) -> StoredFileModel:
    """Reserve capacity atomically; caller commits before any external write."""
    if settings.storage_provider != "r2" or not settings.r2_bucket_name:
        raise StorageUnavailable("Private file storage is unavailable.")
    if byte_size < 0:
        raise ValueError("file size must be nonnegative")
    db.execute(
        _insert(db, StorageQuotaModel)
        .values(id=1, used_bytes=0)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    reserved = db.execute(
        update(StorageQuotaModel)
        .where(
            StorageQuotaModel.id == 1,
            StorageQuotaModel.used_bytes + byte_size <= settings.storage_max_bytes,
        )
        .values(used_bytes=StorageQuotaModel.used_bytes + byte_size)
        .execution_options(synchronize_session=False)
    )
    if reserved.rowcount != 1:
        raise StorageQuotaExceeded("Private file storage capacity has been reached.")
    file_id = str(uuid.uuid4())
    result = StoredFileModel(
        id=file_id,
        owner_user_id=owner_user_id,
        bucket=settings.r2_bucket_name,
        object_key=f"originals/{file_id}",
        byte_size=byte_size,
        sha256=sha256,
        filename=filename,
        content_type=content_type,
        status="reserved",
        reserved_until=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(result)
    db.flush()
    return result


def mark_file_ready(db: Session, stored_file: StoredFileModel) -> None:
    """Publish a verified original in the same transaction that attaches it."""
    changed = db.execute(
        update(StoredFileModel)
        .where(StoredFileModel.id == stored_file.id, StoredFileModel.status == "reserved")
        .values(status="ready", reserved_until=None)
        .execution_options(synchronize_session=False)
    )
    db.refresh(stored_file)
    if changed.rowcount != 1 and stored_file.status != "ready":
        raise StorageUnavailable("The private file reservation is no longer available.")


def lock_reserved_file(db: Session, stored_file: StoredFileModel) -> None:
    """Serialize final writes with erasure and reject expired/revoked reservations.

    Hold this transaction through the external write and document attachment.
    The cleanup grace also protects a bounded in-flight write if this process dies
    and releases its DB lock before the storage request finishes.
    """
    locked = db.execute(
        update(StoredFileModel)
        .where(
            StoredFileModel.id == stored_file.id,
            StoredFileModel.status == "reserved",
            StoredFileModel.reserved_until > datetime.now(UTC),
        )
        .values(status=StoredFileModel.status)
        .execution_options(synchronize_session=False)
    )
    if locked.rowcount != 1:
        raise StorageUnavailable("The private file reservation is no longer available.")
    db.refresh(stored_file)


def enqueue_object_cleanup(
    db: Session,
    *,
    bucket: str,
    object_key: str,
    not_before: datetime | None = None,
    stored_file_id: str | None = None,
) -> None:
    """Queue erasure once; signed staging uploads must outlive their PUT URL."""
    available_at = not_before or datetime.now(UTC)
    db.execute(
        _insert(db, StorageCleanupModel)
        .values(
            id=str(uuid.uuid4()),
            bucket=bucket,
            object_key=object_key,
            stored_file_id=stored_file_id,
            not_before=available_at,
            attempts=0,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["bucket", "object_key"])
    )
    # Never shorten a staging object's signed-upload lifetime on a repeated request.
    db.execute(
        update(StorageCleanupModel)
        .where(
            StorageCleanupModel.bucket == bucket,
            StorageCleanupModel.object_key == object_key,
            StorageCleanupModel.not_before < available_at,
        )
        .values(not_before=available_at)
    )


def schedule_file_cleanup(
    db: Session, stored_file: StoredFileModel, *, not_before: datetime | None = None
) -> None:
    """Revoke file readiness now, releasing capacity only after confirmed erasure."""
    db.refresh(stored_file)
    if stored_file.status == "reserved":
        available_at = not_before or datetime.now(UTC)
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        not_before = max(available_at, datetime.now(UTC) + WRITE_SETTLE_GRACE)
    changed = db.execute(
        update(StoredFileModel)
        .where(
            StoredFileModel.id == stored_file.id,
            StoredFileModel.status.in_(("reserved", "ready")),
        )
        .values(status="delete_pending", reserved_until=None)
        .execution_options(synchronize_session=False)
    )
    db.refresh(stored_file)
    if changed.rowcount == 1 or stored_file.status == "delete_pending":
        enqueue_object_cleanup(
            db,
            bucket=stored_file.bucket,
            object_key=stored_file.object_key,
            stored_file_id=stored_file.id,
            not_before=not_before,
        )


def schedule_document_file_cleanup(db: Session, document_id: str) -> None:
    """Called in the document deletion transaction, before its original link disappears."""
    from my_agents.knowledge.models import DocumentModel

    document = db.get(DocumentModel, document_id)
    if document is not None and document.original_file_id:
        stored_file = db.get(StoredFileModel, document.original_file_id)
        if stored_file is not None:
            schedule_file_cleanup(db, stored_file)


def schedule_user_file_cleanup(db: Session, user_id: str) -> None:
    """Also revoke abandoned reservations when the owning account is erased."""
    files = db.scalars(
        select(StoredFileModel).where(
            StoredFileModel.owner_user_id == user_id,
            StoredFileModel.status.in_(("reserved", "ready", "delete_pending")),
        )
    ).all()
    for stored_file in files:
        schedule_file_cleanup(db, stored_file)


def reserve_file_copy(
    db: Session, settings: Settings, source: StoredFileModel, *, owner_user_id: str
) -> StoredFileModel:
    """Allocate independent capacity and ownership before a publication copy."""
    if source.status != "ready" or source.bucket != settings.r2_bucket_name:
        raise StorageUnavailable("The original private file is unavailable.")
    return reserve_file(
        db,
        settings,
        owner_user_id=owner_user_id,
        byte_size=source.byte_size,
        sha256=source.sha256,
        filename=source.filename,
        content_type=source.content_type,
    )


def copy_reserved_file(
    db: Session,
    settings: Settings,
    source: StoredFileModel,
    destination: StoredFileModel,
    *,
    store: ObjectStore | None = None,
) -> None:
    """Copy into a previously committed reservation; attach before committing ready."""
    if (
        source.status != "ready"
        or destination.status != "reserved"
        or source.bucket != settings.r2_bucket_name
        or destination.bucket != settings.r2_bucket_name
    ):
        raise StorageUnavailable("The original private file is unavailable.")
    lock_reserved_file(db, destination)
    (store or get_object_store(settings)).copy(source.object_key, destination.object_key)
    mark_file_ready(db, destination)


def prepare_document_file_copies(
    db: Session,
    documents: Sequence[DocumentModel],
    *,
    owner_user_id: str,
    settings: Settings | None = None,
) -> dict[str, StoredFileModel]:
    """Commit only copy reservations at the publication flow's clean entry boundary.

    Call this before creating the group KB/document or changing review status.
    Keeping reservations durable first makes a crash during object copy recoverable.
    """
    with db.no_autoflush:
        originals = [document for document in documents if document.original_file_id]
        if not originals:
            return {}
        if db.new or db.deleted or any(db.is_modified(row) for row in db.dirty):
            raise RuntimeError("reserve publication files before changing business records")
    settings = settings or get_settings()
    copies = {}
    try:
        for document in originals:
            source = db.get(StoredFileModel, document.original_file_id)
            if source is None:
                raise StorageUnavailable("The original private file is unavailable.")
            copies[document.id] = reserve_file_copy(
                db, settings, source, owner_user_id=owner_user_id
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return copies


def attach_document_file_copy(
    db: Session,
    *,
    source_document: DocumentModel,
    copied_document: DocumentModel,
    reserved_file: StoredFileModel | None,
    settings: Settings | None = None,
) -> None:
    """Copy an original to independent storage and link it to the group document."""
    if reserved_file is None:
        return
    source = db.get(StoredFileModel, source_document.original_file_id)
    if source is None:
        raise StorageUnavailable("The original private file is unavailable.")
    copy_reserved_file(db, settings or get_settings(), source, reserved_file)
    copied_document.original_file_id = reserved_file.id


def discard_file_copies(db: Session, copies: dict[str, StoredFileModel]) -> None:
    """After business rollback, preserve durable cleanup of every reserved target."""
    for reserved_file in copies.values():
        current = db.get(StoredFileModel, reserved_file.id, populate_existing=True)
        if current is not None:
            schedule_file_cleanup(db, current)


def _expire_reservations(db: Session, *, now: datetime) -> None:
    expired_before = now - WRITE_SETTLE_GRACE
    ids = db.scalars(
        select(StoredFileModel.id)
        .where(
            StoredFileModel.status == "reserved", StoredFileModel.reserved_until <= expired_before
        )
        .order_by(StoredFileModel.reserved_until, StoredFileModel.id)
        .limit(25)
    ).all()
    for file_id in ids:
        changed = db.execute(
            update(StoredFileModel)
            .where(
                StoredFileModel.id == file_id,
                StoredFileModel.status == "reserved",
                StoredFileModel.reserved_until <= expired_before,
            )
            .values(status="delete_pending", reserved_until=None)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount == 1:
            stored_file = db.get(StoredFileModel, file_id, populate_existing=True)
            enqueue_object_cleanup(
                db,
                bucket=stored_file.bucket,
                object_key=stored_file.object_key,
                stored_file_id=stored_file.id,
                not_before=now,
            )


def cleanup_once(
    db: Session,
    settings: Settings,
    *,
    store: ObjectStore | None = None,
    stop: Event | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Run one bounded sweep with committed leases and idempotent provider deletes."""
    now = now or datetime.now(UTC)
    _expire_reservations(db, now=now)
    db.commit()
    jobs = db.scalars(
        select(StorageCleanupModel)
        .where(
            StorageCleanupModel.bucket == settings.r2_bucket_name,
            StorageCleanupModel.not_before <= now,
            or_(StorageCleanupModel.lease_until.is_(None), StorageCleanupModel.lease_until <= now),
        )
        .order_by(StorageCleanupModel.not_before, StorageCleanupModel.id)
        .limit(25)
    ).all()
    counts: Counter[str] = Counter()
    for job in jobs:
        if stop is not None and stop.is_set():
            break
        token = str(uuid.uuid4())
        claimed = db.execute(
            update(StorageCleanupModel)
            .where(
                StorageCleanupModel.id == job.id,
                StorageCleanupModel.not_before <= now,
                or_(
                    StorageCleanupModel.lease_until.is_(None),
                    StorageCleanupModel.lease_until <= now,
                ),
            )
            .values(
                lease_token=token,
                lease_until=now + timedelta(minutes=2),
                attempts=StorageCleanupModel.attempts + 1,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if claimed.rowcount != 1:
            continue
        db.refresh(job)
        try:
            (store or get_object_store(settings)).delete(job.object_key)
        except Exception as exc:
            db.execute(
                update(StorageCleanupModel)
                .where(StorageCleanupModel.id == job.id, StorageCleanupModel.lease_token == token)
                .values(
                    not_before=now + timedelta(seconds=min(3600, 5 * 2 ** min(job.attempts, 10))),
                    lease_token=None,
                    lease_until=None,
                    error_code="object_delete_failed",
                )
            )
            db.commit()
            counts["retry"] += 1
            logger.warning("storage_cleanup.retry error_class=%s", type(exc).__name__)
            continue
        still_owned = db.execute(
            update(StorageCleanupModel)
            .where(StorageCleanupModel.id == job.id, StorageCleanupModel.lease_token == token)
            .values(lease_until=now + timedelta(minutes=2))
        )
        if still_owned.rowcount != 1:
            db.rollback()
            continue
        if job.stored_file_id:
            stored_file = db.get(StoredFileModel, job.stored_file_id, populate_existing=True)
            if stored_file is not None:
                changed = db.execute(
                    update(StoredFileModel)
                    .where(
                        StoredFileModel.id == stored_file.id,
                        StoredFileModel.status == "delete_pending",
                    )
                    .values(
                        status="deleted", owner_user_id="", filename="", sha256="", content_type=""
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount == 1:
                    released = db.execute(
                        update(StorageQuotaModel)
                        .where(
                            StorageQuotaModel.id == 1,
                            StorageQuotaModel.used_bytes >= stored_file.byte_size,
                        )
                        .values(used_bytes=StorageQuotaModel.used_bytes - stored_file.byte_size)
                        .execution_options(synchronize_session=False)
                    )
                    if released.rowcount != 1:
                        db.rollback()
                        raise RuntimeError("private file quota accounting is inconsistent")
        db.execute(
            delete(StorageCleanupModel).where(
                StorageCleanupModel.id == job.id, StorageCleanupModel.lease_token == token
            )
        )
        db.commit()
        counts["deleted"] += 1
    return dict(counts)


async def storage_cleanup_loop(settings: Settings) -> None:
    """Use the existing API process; no separate worker service is required."""
    from my_agents.persistence.database import _sessionmaker_for_url, supports_background_sessions

    if settings.storage_provider != "r2" or not supports_background_sessions(settings.database_url):
        return
    stop = Event()
    while True:

        def sweep() -> None:
            with _sessionmaker_for_url(settings.database_url)() as db:
                cleanup_once(db, settings, stop=stop)

        task = asyncio.create_task(asyncio.to_thread(sweep))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            stop.set()
            try:
                await task
            except Exception as exc:
                logger.warning("storage_cleanup.shutdown_failed error_class=%s", type(exc).__name__)
            raise
        except Exception as exc:
            logger.warning("storage_cleanup.failed error_class=%s", type(exc).__name__)
        await asyncio.sleep(60)
