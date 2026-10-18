"""Private originals retain independent ownership and durable, quota-aware erasure."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from my_agents.knowledge.models import (
    DocumentModel,
    KnowledgeBaseModel,
    KnowledgePublishRequestModel,
)
from my_agents.knowledge.publication_copies import (
    copy_personal_knowledge_base_to_group,
    delete_document_artifacts,
)
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models
from my_agents.storage import StorageUnavailable
from my_agents.storage.lifecycle import (
    StorageQuotaExceeded,
    cleanup_once,
    enqueue_object_cleanup,
    lock_reserved_file,
    mark_file_ready,
    prepare_document_file_copies,
    reserve_file,
    schedule_file_cleanup,
    schedule_user_file_cleanup,
)
from my_agents.storage.models import StorageCleanupModel, StorageQuotaModel, StoredFileModel


class FakeObjectStore:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.fail_delete = False
        self.fail_copy = False

    def copy(self, source_key, destination_key):
        self.objects[destination_key] = self.objects[source_key]
        if self.fail_copy:
            raise StorageUnavailable("private provider diagnostic must never persist")

    def delete(self, key):
        if self.fail_delete:
            raise RuntimeError("secret-provider-token-must-not-be-persisted")
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def storage_settings():
    return SimpleNamespace(
        storage_provider="r2", r2_bucket_name="private-test-bucket", storage_max_bytes=100
    )


@pytest.fixture
def db():
    import_all_models()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def reserve(db, settings, *, size=10, owner="owner"):
    return reserve_file(
        db,
        settings,
        owner_user_id=owner,
        byte_size=size,
        sha256="a" * 64,
        filename="original.md",
        content_type="text/markdown",
    )


def used_bytes(db):
    return db.scalar(select(StorageQuotaModel.used_bytes))


def seed_document(db, settings, store):
    file = reserve(db, settings)
    db.commit()
    store.objects[file.object_key] = b"original bytes"
    mark_file_ready(db, file)
    kb = KnowledgeBaseModel(id="source-kb", name="Private", scope="personal", owner_user_id="owner")
    db.add(kb)
    document = DocumentModel(
        id="source-doc",
        knowledge_base_id=kb.id,
        owner_user_id="owner",
        title="Original",
        content="cleaned and indexed text",
        original_file_id=file.id,
        ingestion_mode="ai_assisted",
        processing_provenance_json='{"version":1}',
        processed_at=datetime.now(UTC),
    )
    db.add(document)
    db.commit()
    return document, file, kb


def test_pending_and_ready_files_share_atomic_quota(db, storage_settings):
    first = reserve(db, storage_settings, size=70)
    db.commit()
    with pytest.raises(StorageQuotaExceeded):
        reserve(db, storage_settings, size=31)
    db.rollback()
    mark_file_ready(db, first)
    db.commit()
    reserve(db, storage_settings, size=30)
    db.commit()
    assert used_bytes(db) == 100
    assert db.scalar(select(func.count()).select_from(StoredFileModel)) == 2


def test_reservation_rollback_leaves_no_files_or_capacity(db, storage_settings):
    reserve(db, storage_settings, size=70)
    db.rollback()
    assert (used_bytes(db) or 0) == 0
    assert db.scalar(select(func.count()).select_from(StoredFileModel)) == 0


def test_delete_retry_keeps_quota_and_redacts_provider_errors(db, storage_settings):
    store = FakeObjectStore()
    file = reserve(db, storage_settings)
    db.commit()
    mark_file_ready(db, file)
    store.objects[file.object_key] = b"bytes"
    schedule_file_cleanup(db, file)
    schedule_file_cleanup(db, file)
    db.commit()
    assert db.scalar(select(func.count()).select_from(StorageCleanupModel)) == 1
    store.fail_delete = True
    now = datetime.now(UTC)
    assert cleanup_once(db, storage_settings, store=store, now=now) == {"retry": 1}
    job = db.scalar(select(StorageCleanupModel))
    assert job.error_code == "object_delete_failed"
    assert job.attempts == 1
    assert used_bytes(db) == 10
    store.fail_delete = False
    assert cleanup_once(db, storage_settings, store=store, now=now + timedelta(minutes=1)) == {
        "deleted": 1
    }
    db.refresh(file)
    assert file.status == "deleted"
    assert file.owner_user_id == file.filename == file.sha256 == ""
    assert used_bytes(db) == 0
    assert not store.objects
    assert cleanup_once(db, storage_settings, store=store) == {}


def test_expired_reservations_clean_up_but_ready_originals_survive(db, storage_settings):
    store = FakeObjectStore()
    abandoned = reserve(db, storage_settings)
    original = reserve(db, storage_settings)
    abandoned.reserved_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    mark_file_ready(db, original)
    db.commit()
    store.objects[abandoned.object_key] = b"partial write before process crash"
    store.objects[original.object_key] = b"retained original"
    assert cleanup_once(db, storage_settings, store=store) == {}
    assert cleanup_once(
        db, storage_settings, store=store, now=datetime.now(UTC) + timedelta(minutes=5)
    ) == {"deleted": 1}
    assert store.objects == {original.object_key: b"retained original"}
    assert used_bytes(db) == 10
    with pytest.raises(StorageUnavailable):
        mark_file_ready(db, abandoned)


def test_signed_staging_url_cannot_recreate_file_after_early_cleanup(db, storage_settings):
    store = FakeObjectStore()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=10)
    enqueue_object_cleanup(
        db, bucket=storage_settings.r2_bucket_name, object_key="staging/upload", not_before=expires
    )
    enqueue_object_cleanup(
        db, bucket=storage_settings.r2_bucket_name, object_key="staging/upload", not_before=now
    )
    db.commit()
    assert cleanup_once(db, storage_settings, store=store, now=now) == {}
    store.objects["staging/upload"] = b"late put before signed URL expires"
    assert cleanup_once(db, storage_settings, store=store, now=expires) == {"deleted": 1}
    assert not store.objects


def test_different_bucket_and_live_leases_are_not_deleted(db, storage_settings):
    store = FakeObjectStore()
    now = datetime.now(UTC)
    enqueue_object_cleanup(db, bucket="other-environment", object_key="other", not_before=now)
    enqueue_object_cleanup(
        db, bucket=storage_settings.r2_bucket_name, object_key="leased", not_before=now
    )
    job = db.scalar(select(StorageCleanupModel).where(StorageCleanupModel.object_key == "leased"))
    job.lease_until = now + timedelta(minutes=2)
    job.lease_token = "another-worker"
    db.commit()
    assert cleanup_once(db, storage_settings, store=store, now=now) == {}
    assert cleanup_once(db, storage_settings, store=store, now=now + timedelta(minutes=3)) == {
        "deleted": 1
    }
    assert store.deleted == ["leased"]


def test_old_worker_cannot_release_quota_after_lease_takeover(db, storage_settings):
    file = reserve(db, storage_settings)
    db.commit()
    schedule_file_cleanup(db, file)
    db.commit()

    class LeaseTakeoverStore(FakeObjectStore):
        def delete(self, key):
            super().delete(key)
            db.execute(update(StorageCleanupModel).values(lease_token="new-worker"))
            db.commit()

    store = LeaseTakeoverStore()
    assert (
        cleanup_once(
            db, storage_settings, store=store, now=datetime.now(UTC) + timedelta(minutes=6)
        )
        == {}
    )
    assert store.deleted == [file.object_key]
    assert used_bytes(db) == 10
    assert db.get(StoredFileModel, file.id, populate_existing=True).status == "delete_pending"


def test_concurrent_reservations_do_not_exceed_quota(tmp_path, storage_settings):
    import_all_models()
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'quota.sqlite'}")
    Base.metadata.create_all(engine)
    barrier = Barrier(2)

    def allocate():
        with Session(engine) as session:
            barrier.wait()
            try:
                reserve(session, storage_settings, size=70)
                session.commit()
                return True
            except StorageQuotaExceeded:
                session.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(allocate) for _ in range(2)]
        assert sorted(future.result() for future in futures) == [False, True]
    with Session(engine) as session:
        assert used_bytes(session) == 70
    engine.dispose()


def configure_publication_fakes(monkeypatch, settings, store):
    monkeypatch.setattr("my_agents.storage.lifecycle.get_settings", lambda: settings)
    monkeypatch.setattr("my_agents.storage.lifecycle.get_object_store", lambda settings: store)
    monkeypatch.setattr(
        "my_agents.knowledge.publication_copies.KnowledgeExtractionService.ingest_document",
        lambda self, document: self._db.commit(),
    )


def test_group_publication_owns_copy_after_source_deletion(db, storage_settings, monkeypatch):
    store = FakeObjectStore()
    document, original, kb = seed_document(db, storage_settings, store)
    configure_publication_fakes(monkeypatch, storage_settings, store)
    result = copy_personal_knowledge_base_to_group(
        db, source_knowledge_base=kb, target_group_id="group", group_owner_user_id="group-owner"
    )
    copied_document = db.get(DocumentModel, result.document_ids[0])
    copied_file = db.get(StoredFileModel, copied_document.original_file_id)
    assert copied_file.id != original.id
    assert copied_file.owner_user_id == "group-owner"
    assert copied_document.ingestion_mode == document.ingestion_mode
    assert copied_document.processing_provenance_json == document.processing_provenance_json
    assert copied_document.processed_at == document.processed_at
    delete_document_artifacts(db, document_id=document.id)
    db.delete(document)
    schedule_user_file_cleanup(db, "owner")
    db.commit()
    assert cleanup_once(db, storage_settings, store=store) == {"deleted": 1}
    assert store.objects == {copied_file.object_key: b"original bytes"}
    assert used_bytes(db) == 10


def test_failed_copy_preserves_source_and_queues_partial_object(db, storage_settings, monkeypatch):
    store = FakeObjectStore()
    document, original, kb = seed_document(db, storage_settings, store)
    configure_publication_fakes(monkeypatch, storage_settings, store)
    store.fail_copy = True
    with pytest.raises(StorageUnavailable):
        copy_personal_knowledge_base_to_group(
            db, source_knowledge_base=kb, target_group_id="group", group_owner_user_id="group-owner"
        )
    assert db.get(DocumentModel, document.id) is not None
    assert original.object_key in store.objects
    assert used_bytes(db) == 20
    assert cleanup_once(db, storage_settings, store=store) == {}
    assert cleanup_once(
        db, storage_settings, store=store, now=datetime.now(UTC) + timedelta(minutes=6)
    ) == {"deleted": 1}
    assert store.objects == {original.object_key: b"original bytes"}
    assert used_bytes(db) == 10
    assert db.scalar(select(func.count()).select_from(DocumentModel)) == 1


def test_publication_reservations_do_not_commit_unrelated_edits(db, storage_settings):
    document, _, _ = seed_document(db, storage_settings, FakeObjectStore())
    document.title = "uncommitted edit"
    with pytest.raises(RuntimeError, match="before changing business records"):
        prepare_document_file_copies(
            db, [document], owner_user_id="group-owner", settings=storage_settings
        )
    db.rollback()
    assert document.title == "Original"
    assert used_bytes(db) == 10


def test_single_document_approval_copies_original_under_group_owner(
    db, storage_settings, monkeypatch
):
    from my_agents.api.groups import approve_publish_request

    store = FakeObjectStore()
    document, original, _ = seed_document(db, storage_settings, store)
    configure_publication_fakes(monkeypatch, storage_settings, store)
    monkeypatch.setattr("my_agents.api.groups._require_group_manager", lambda *args: None)
    target = KnowledgeBaseModel(
        id="target-kb", name="Group", scope="group", group_id="group", owner_user_id="group-owner"
    )
    db.add(target)
    request = KnowledgePublishRequestModel(
        requester_user_id="owner",
        target_group_id="group",
        target_knowledge_base_id=target.id,
        source_document_id=document.id,
    )
    db.add(request)
    db.commit()
    result = approve_publish_request(
        "group", request.id, SimpleNamespace(user_id="group-owner"), db
    )
    assert result.status == "approved"
    published_document = db.get(DocumentModel, result.published_document_id)
    copied_file = db.get(StoredFileModel, published_document.original_file_id)
    assert copied_file.id != original.id
    assert copied_file.owner_user_id == "group-owner"
    assert store.objects[copied_file.object_key] == store.objects[original.object_key]
    schedule_user_file_cleanup(db, "owner")
    db.commit()
    cleanup_once(db, storage_settings, store=store)
    assert store.objects == {copied_file.object_key: b"original bytes"}


def test_expired_reservation_cannot_be_written_during_cleanup_grace(db, storage_settings):
    file = reserve(db, storage_settings)
    file.reserved_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert cleanup_once(db, storage_settings, store=FakeObjectStore()) == {}
    with pytest.raises(StorageUnavailable):
        lock_reserved_file(db, file)
    assert file.status == "reserved"
    assert used_bytes(db) == 10


def test_reserved_abort_waits_for_in_flight_writes_before_releasing_quota(db, storage_settings):
    store = FakeObjectStore()
    file = reserve(db, storage_settings)
    db.commit()
    schedule_file_cleanup(db, file)
    db.commit()
    assert cleanup_once(db, storage_settings, store=store) == {}
    assert used_bytes(db) == 10
    # A timed-out provider request may finish after the caller queued compensation.
    store.objects[file.object_key] = b"late provider write"
    assert cleanup_once(
        db, storage_settings, store=store, now=datetime.now(UTC) + timedelta(minutes=6)
    ) == {"deleted": 1}
    assert used_bytes(db) == 0
    assert not store.objects
