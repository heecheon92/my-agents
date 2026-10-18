"""Private-original HTTP scenarios with in-memory storage and no provider calls."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.auth.models import UserModel
from my_agents.knowledge.file_upload_models import DocumentUploadSessionModel
from my_agents.knowledge.models import DocumentModel, ExtractionRunModel, KnowledgeBaseModel
from my_agents.persistence.database import get_database_session
from my_agents.settings import get_settings
from my_agents.storage import (
    StorageObjectNotFound,
    StorageObjectTooLarge,
    StorageUnavailable,
)
from my_agents.storage.lifecycle import cleanup_once
from my_agents.storage.models import StorageCleanupModel, StorageQuotaModel, StoredFileModel

from .test_guest_access_api import _guest_login
from .test_kb_nested_document_routes import _create_kb, _signup_login


class MemoryObjectStore:
    """Keep object bytes independent from database rows and signed staging URLs."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_signatures: list[tuple[str, str, int | None]] = []
        self.get_signatures: list[tuple[str, str]] = []
        self.reads: list[str] = []
        self.puts: list[str] = []
        self.fail_read = False
        self.fail_put = False

    def presign_put(
        self,
        key: str,
        *,
        content_type: str,
        expires_seconds: int,
        content_length: int | None = None,
    ) -> str:
        self.put_signatures.append((key, content_type, content_length))
        return f"https://storage.example.test/{key}?signed={len(self.put_signatures)}"

    def presign_get(self, key: str, *, filename: str, expires_seconds: int) -> str:
        self.get_signatures.append((key, filename))
        return f"https://storage.example.test/{key}?filename={quote(filename)}"

    def read(self, key: str, *, max_bytes: int) -> bytes:
        self.reads.append(key)
        if self.fail_read:
            raise StorageUnavailable("Object storage is unavailable.")
        if key not in self.objects:
            raise StorageObjectNotFound("Object is missing.")
        content = self.objects[key]
        if len(content) > max_bytes:
            raise StorageObjectTooLarge("Object is oversized.")
        return content

    def put(self, key: str, content: bytes, *, content_type: str) -> None:
        self.puts.append(key)
        if self.fail_put:
            raise StorageUnavailable("Object storage is unavailable.")
        self.objects[key] = content

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def copy(self, source_key: str, destination_key: str) -> None:
        raise AssertionError("Never seal mutable staging bytes using a server-side copy")


@contextmanager
def _db():
    sessions = get_database_session()
    try:
        yield next(sessions)
    finally:
        sessions.close()


@dataclass
class FileAPI:
    client: TestClient
    store: MemoryObjectStore
    kb_id: str
    user_id: str
    dispatched: list[tuple]

    @property
    def path(self) -> str:
        return f"/knowledge-bases/{self.kb_id}/documents/uploads"

    def initiate(self, content: bytes = b"# Source\n\nKeep exact facts.", **overrides):
        payload = {
            "title": "State source",
            "filename": "상태 원칙.md",
            "content_type": "text/markdown",
            "byte_size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            **overrides,
        }
        response = self.client.post(self.path, json=payload)
        assert response.status_code == 201, response.text
        receipt = response.json()
        assert receipt["method"] == "PUT"
        assert receipt["max_bytes"] == 5 * 1024 * 1024
        assert receipt["headers"]["Content-Type"] == "text/markdown"
        assert receipt["expires_at"]
        return receipt

    def stage(self, content: bytes = b"# Source\n\nKeep exact facts.", **overrides):
        receipt = self.initiate(content, **overrides)
        key = self.store.put_signatures[-1][0]
        self.store.objects[key] = content
        return receipt, key

    def complete(self, upload_id: str):
        return self.client.post(f"{self.path}/{upload_id}/complete")


@pytest.fixture
def files(monkeypatch, tmp_path):
    import my_agents.storage
    from my_agents.api import document_files

    monkeypatch.setenv("MY_AGENTS_STORAGE_PROVIDER", "r2")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'files.sqlite'}")
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("MY_AGENTS_STORAGE_DIRECT_UPLOADS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_R2_BUCKET_NAME", "unit-test-originals")
    monkeypatch.setenv("MY_AGENTS_R2_ENDPOINT_URL", "https://test.r2.cloudflarestorage.com")
    monkeypatch.setenv("MY_AGENTS_R2_ACCESS_KEY_ID", "test-only-access")
    monkeypatch.setenv("MY_AGENTS_R2_SECRET_ACCESS_KEY", "test-only-secret")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    store = MemoryObjectStore()
    monkeypatch.setattr(my_agents.storage, "get_object_store", lambda settings: store)
    monkeypatch.setattr(document_files, "get_object_store", lambda settings: store)
    dispatched = []
    monkeypatch.setattr(
        document_files,
        "_dispatch_extraction_run",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    client = TestClient(create_app())
    user_id = _signup_login(client, "file-owner@example.com")
    kb_id = _create_kb(client, "Private originals")
    yield FileAPI(client, store, kb_id, user_id, dispatched)
    client.close()


def _error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json()["code"] == code


def test_verified_original_is_sealed_and_completion_retries_never_duplicate(files):
    content = "# State 설계 원칙\n\nKeep messages + add_messages and next_step.".encode()
    receipt, staging_key = files.stage(content)
    completed = files.complete(receipt["upload_id"])
    assert completed.status_code == 201, completed.text
    result = completed.json()
    document = result["document"]
    assert document["original_retained"] is True
    assert document["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert document["source_byte_size"] == len(content)
    assert result["extraction_run"]["document_id"] == document["id"]
    assert len(files.dispatched) == 1

    with _db() as db:
        row = db.get(DocumentModel, document["id"])
        original = db.get(StoredFileModel, row.original_file_id)
        assert original.status == "ready"
        original_key = original.object_key
        assert original_key != staging_key
        assert row.content == content.decode()
        assert len(db.scalars(select(ExtractionRunModel)).all()) == 1
        cleanup = db.scalars(select(StorageCleanupModel)).all()
        assert staging_key in {item.object_key for item in cleanup}

    assert files.store.objects[original_key] == content
    files.store.objects[staging_key] = b"Replayed PUT must not rewrite the retained original"
    repeated = files.complete(receipt["upload_id"])
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["document"]["id"] == document["id"]
    assert repeated.json()["extraction_run"]["id"] == result["extraction_run"]["id"]
    assert len(files.dispatched) == 1
    assert files.store.objects[original_key] == content

    download = files.client.get(f"/documents/{document['id']}/original")
    assert download.status_code == 200, download.text
    assert download.json()["filename"] == "상태 원칙.md"
    assert download.json()["expires_at"]
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["referrer-policy"] == "no-referrer"
    assert files.store.get_signatures[-1] == (original_key, "상태 원칙.md")
    assert "content" not in download.json()
    assert "object_key" not in download.json()
    _error(
        files.client.delete(f"{files.path}/{receipt['upload_id']}"),
        409,
        "upload_in_progress",
    )


def test_upload_ids_are_scoped_by_owner_and_current_knowledge_base(files):
    receipt, _ = files.stage()
    uid = receipt["upload_id"]
    outsider = TestClient(create_app())
    _signup_login(outsider, "file-outsider@example.com")
    other_kb = _create_kb(files.client, "Other private originals")

    for client, prefix in (
        (outsider, files.path),
        (files.client, f"/knowledge-bases/{other_kb}/documents/uploads"),
    ):
        for method, suffix in (("post", "/refresh"), ("post", "/complete"), ("delete", "")):
            response = getattr(client, method)(f"{prefix}/{uid}{suffix}")
            assert response.status_code == 404, response.text

    assert files.store.reads == []
    completed = files.complete(uid)
    assert completed.status_code == 201, completed.text
    did = completed.json()["document"]["id"]
    for path in (f"/documents/{did}/original", f"/documents/{did}/reprocess"):
        response = outsider.get(path) if path.endswith("original") else outsider.post(path, json={})
        assert response.status_code == 404, response.text
    assert files.store.get_signatures == []
    outsider.close()


def test_refresh_preserves_upload_identity_and_pending_object(files):
    receipt, key = files.stage()
    refreshed = files.client.post(f"{files.path}/{receipt['upload_id']}/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["upload_id"] == receipt["upload_id"]
    assert refreshed.json()["url"] != receipt["url"]
    assert files.store.put_signatures[-1][0] == key
    assert files.complete(receipt["upload_id"]).status_code == 201


def test_expired_admission_cannot_refresh_or_finalize_and_busy_admission_cannot_abort(files):
    expired, _ = files.stage()
    with _db() as db:
        db.get(DocumentUploadSessionModel, expired["upload_id"]).expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
        db.commit()
    _error(files.complete(expired["upload_id"]), 410, "upload_expired")
    _error(
        files.client.post(f"{files.path}/{expired['upload_id']}/refresh"),
        410,
        "upload_expired",
    )
    busy, _ = files.stage()
    with _db() as db:
        db.get(DocumentUploadSessionModel, busy["upload_id"]).status = "verifying"
        db.commit()
    path = f"{files.path}/{busy['upload_id']}"
    _error(files.client.post(f"{path}/complete"), 409, "upload_in_progress")
    _error(files.client.post(f"{path}/refresh"), 409, "upload_in_progress")
    _error(files.client.delete(path), 409, "upload_in_progress")
    assert files.store.reads == []


def test_permission_revocation_between_upload_and_completion_is_rechecked(files):
    receipt, _ = files.stage()
    with _db() as db:
        db.get(KnowledgeBaseModel, files.kb_id).owner_user_id = "another-owner"
        db.commit()
    path = f"{files.path}/{receipt['upload_id']}"
    for method, suffix in (("post", "/complete"), ("post", "/refresh"), ("delete", "")):
        response = getattr(files.client, method)(f"{path}{suffix}")
        assert response.status_code == 404, response.text
    assert files.store.reads == []
    assert files.dispatched == []


@pytest.mark.parametrize("partial_upload", [False, True])
def test_abort_is_idempotent_and_complete_after_abort_is_expired(files, partial_upload):
    receipt = files.initiate()
    if partial_upload:
        files.store.objects[files.store.put_signatures[-1][0]] = b"partial"
    path = f"{files.path}/{receipt['upload_id']}"
    assert files.client.delete(path).status_code == 204
    assert files.client.delete(path).status_code == 204
    _error(files.complete(receipt["upload_id"]), 410, "upload_expired")
    _error(files.client.post(f"{path}/refresh"), 410, "upload_expired")
    with _db() as db:
        assert db.scalars(select(DocumentModel)).all() == []
        assert db.scalars(select(ExtractionRunModel)).all() == []
    assert files.store.reads == []


def test_missing_staging_object_can_be_uploaded_then_completed(files):
    receipt = files.initiate()
    _error(files.complete(receipt["upload_id"]), 409, "upload_not_ready")
    files.store.objects[files.store.put_signatures[-1][0]] = b"# Source\n\nKeep exact facts."
    assert files.complete(receipt["upload_id"]).status_code == 201


@pytest.mark.parametrize("bad_content", [b"short", b"x" * 25, b"x" * 100])
def test_size_and_sha_mismatch_never_create_documents_or_extraction_runs(files, bad_content):
    receipt = files.initiate(b"a" * 25)
    files.store.objects[files.store.put_signatures[-1][0]] = bad_content
    _error(files.complete(receipt["upload_id"]), 400, "upload_integrity_mismatch")
    with _db() as db:
        assert db.scalars(select(DocumentModel)).all() == []
        assert db.scalars(select(ExtractionRunModel)).all() == []
    assert files.store.puts == []
    assert files.dispatched == []


@pytest.mark.parametrize("failure", ["fail_read", "fail_put"])
def test_storage_failure_preserves_retry_without_publishing_partial_document(files, failure):
    receipt, _ = files.stage()
    setattr(files.store, failure, True)
    _error(files.complete(receipt["upload_id"]), 503, "storage_unavailable")
    with _db() as db:
        assert db.scalars(select(DocumentModel)).all() == []
        assert db.scalars(select(ExtractionRunModel)).all() == []
    setattr(files.store, failure, False)
    assert files.complete(receipt["upload_id"]).status_code == 201
    assert len(files.dispatched) == 1


def test_reprocess_rejects_overlap_and_queues_new_candidate_without_overwriting_active_text(files):
    receipt, _ = files.stage()
    result = files.complete(receipt["upload_id"]).json()
    did = result["document"]["id"]
    _error(files.client.post(f"/documents/{did}/reprocess", json={}), 409, "reprocess_in_progress")
    with _db() as db:
        db.get(ExtractionRunModel, result["extraction_run"]["id"]).status = "completed"
        db.get(DocumentModel, did).content = "Already searchable active text"
        db.commit()
    response = files.client.post(f"/documents/{did}/reprocess", json={"ingestion_mode": "standard"})
    assert response.status_code == 202, response.text
    assert response.json()["id"] != result["extraction_run"]["id"]
    with _db() as db:
        assert db.get(DocumentModel, did).content == "Already searchable active text"
        run = db.get(ExtractionRunModel, response.json()["id"])
        assert run.candidate_content == "# Source\n\nKeep exact facts."
        assert run.ingestion_mode == "standard"
    assert len(files.dispatched) == 2


def test_team_staging_completion_retains_original_without_starting_extraction(files):
    staging = files.client.post("/knowledge-bases/team-upload-staging")
    assert staging.status_code == 200
    files.kb_id = staging.json()["id"]
    receipt, _ = files.stage()
    response = files.complete(receipt["upload_id"])
    assert response.status_code == 201, response.text
    assert response.json()["document"]["original_retained"] is True
    assert response.json()["extraction_run"] is None
    assert files.dispatched == []
    repeated = files.complete(receipt["upload_id"])
    assert repeated.json()["document"]["id"] == response.json()["document"]["id"]
    assert repeated.json()["extraction_run"] is None


def test_ingestion_mode_is_pinned_at_upload_admission_even_if_preference_changes(files):
    with _db() as db:
        db.get(UserModel, files.user_id).document_ingestion_mode = "ai_assisted"
        db.commit()
    receipt, _ = files.stage()
    with _db() as db:
        db.get(UserModel, files.user_id).document_ingestion_mode = "standard"
        db.commit()
    response = files.complete(receipt["upload_id"])
    assert response.status_code == 201, response.text
    assert response.json()["document"]["ingestion_mode"] == "ai_assisted"
    with _db() as db:
        run = db.get(ExtractionRunModel, response.json()["extraction_run"]["id"])
        assert run.ingestion_mode == "ai_assisted"


def test_document_deletion_revokes_original_and_queues_object_erasure(files):
    receipt, _ = files.stage()
    result = files.complete(receipt["upload_id"]).json()
    did = result["document"]["id"]
    with _db() as db:
        original_id = db.get(DocumentModel, did).original_file_id
        db.get(ExtractionRunModel, result["extraction_run"]["id"]).status = "completed"
        db.commit()
    response = files.client.delete(f"/documents/{did}")
    assert response.status_code == 204, response.text
    assert files.client.get(f"/documents/{did}/original").status_code == 404
    assert files.complete(receipt["upload_id"]).status_code == 404
    with _db() as db:
        original = db.get(StoredFileModel, original_id)
        assert original.status == "delete_pending"
        cleanup = db.scalars(select(StorageCleanupModel)).all()
        assert original_id in {job.stored_file_id for job in cleanup}


def test_legacy_text_only_document_reports_original_unavailable(files):
    created = files.client.post(
        f"/knowledge-bases/{files.kb_id}/documents",
        json={"title": "Legacy text", "content": "Already searchable text without original bytes."},
    )
    assert created.status_code == 201, created.text
    assert created.json()["original_retained"] is False
    did = created.json()["id"]
    _error(files.client.get(f"/documents/{did}/original"), 404, "original_unavailable")
    _error(files.client.post(f"/documents/{did}/reprocess", json={}), 404, "original_unavailable")
    assert files.store.get_signatures == []
    assert files.store.reads == []


def test_wrong_bucket_original_is_not_read_or_signed_against_current_storage(files):
    receipt, _ = files.stage()
    result = files.complete(receipt["upload_id"]).json()
    did = result["document"]["id"]
    with _db() as db:
        original = db.get(StoredFileModel, db.get(DocumentModel, did).original_file_id)
        original.bucket = "a-previous-deployment-bucket"
        db.get(ExtractionRunModel, result["extraction_run"]["id"]).status = "completed"
        db.commit()
    before_reads = list(files.store.reads)
    for response in (
        files.client.get(f"/documents/{did}/original"),
        files.client.post(f"/documents/{did}/reprocess", json={}),
    ):
        _error(response, 503, "storage_unavailable")
    assert files.store.get_signatures == []
    assert files.store.reads == before_reads


def test_guest_direct_upload_is_denied_before_signing_a_url(files):
    guest = TestClient(create_app())
    _guest_login(guest)
    guest_kb = _create_kb(guest, "Guest files")
    body = {
        "title": "Guest source",
        "filename": "source.txt",
        "content_type": "text/plain",
        "byte_size": 5,
        "sha256": hashlib.sha256(b"hello").hexdigest(),
    }
    denied = guest.post(f"/knowledge-bases/{guest_kb}/documents/uploads", json=body)
    assert denied.status_code == 403, denied.text
    assert files.store.put_signatures == []
    guest.close()


@pytest.mark.parametrize("deployment, enabled", [("local", "false"), ("preview", "true")])
def test_direct_upload_gate_is_honest_and_legacy_multipart_stays_available(
    files, monkeypatch, deployment, enabled
):
    monkeypatch.setenv("MY_AGENTS_STORAGE_DIRECT_UPLOADS_ENABLED", enabled)
    monkeypatch.setenv("MY_AGENTS_DEPLOYMENT_ENVIRONMENT", deployment)
    get_settings.cache_clear()
    health = files.client.get("/health")
    assert health.status_code == 200
    config = health.json()["frontend_config"]["documents"]
    assert config["direct_uploads"] is False
    assert config["max_upload_bytes"] == 5 * 1024 * 1024
    assert config["multipart_max_bytes"] == 4_000_000
    response = files.client.post(
        files.path,
        json={
            "title": "Unavailable direct upload",
            "filename": "source.txt",
            "content_type": "text/plain",
            "byte_size": 5,
            "sha256": hashlib.sha256(b"hello").hexdigest(),
        },
    )
    _error(response, 503, "storage_unavailable")
    multipart = files.client.post(
        f"/knowledge-bases/{files.kb_id}/documents/upload",
        data={"title": "Registered multipart"},
        files={"file": ("source.txt", b"Registered multipart remains available.", "text/plain")},
    )
    assert multipart.status_code == 201, multipart.text


def test_pending_upload_capacity_counts_toward_storage_quota(files, monkeypatch):
    # The second upload can reserve its original but not its staging copy. The
    # entire rejected admission must roll back, including the first reservation.
    monkeypatch.setenv("MY_AGENTS_STORAGE_MAX_BYTES", "70")
    get_settings.cache_clear()
    files.initiate(b"a" * 20)
    with _db() as db:
        assert db.get(StorageQuotaModel, 1).used_bytes == 40
    response = files.client.post(
        files.path,
        json={
            "title": "Over capacity",
            "filename": "source.txt",
            "content_type": "text/plain",
            "byte_size": 20,
            "sha256": hashlib.sha256(b"b" * 20).hexdigest(),
        },
    )
    _error(response, 413, "storage_quota_exceeded")
    assert len(files.store.put_signatures) == 1
    with _db() as db:
        assert db.get(StorageQuotaModel, 1).used_bytes == 40
        assert len(db.scalars(select(StoredFileModel)).all()) == 2
        assert len(db.scalars(select(DocumentUploadSessionModel)).all()) == 1


def test_staging_and_original_capacity_release_only_after_their_own_erasure(files):
    content = b"Retain original text."
    receipt, staging_key = files.stage(content)
    with _db() as db:
        admission = db.get(DocumentUploadSessionModel, receipt["upload_id"])
        original_id = admission.stored_file_id
        staging_id = admission.staging_file_id
        staging_file = db.get(StoredFileModel, staging_id)
        assert staging_file.object_key == staging_key
        assert staging_file.status == "delete_pending"
        assert db.get(StorageQuotaModel, 1).used_bytes == len(content) * 2
        staging_cleanup = db.scalar(
            select(StorageCleanupModel).where(StorageCleanupModel.stored_file_id == staging_id)
        )
        cleanup_at = staging_cleanup.not_before.replace(tzinfo=UTC)
        expires_at = admission.expires_at.replace(tzinfo=UTC)
        assert cleanup_at >= expires_at + timedelta(minutes=2)

    completed = files.complete(receipt["upload_id"])
    assert completed.status_code == 201, completed.text
    did = completed.json()["document"]["id"]
    with _db() as db:
        original_key = db.get(StoredFileModel, original_id).object_key
        assert db.get(StorageQuotaModel, 1).used_bytes == len(content) * 2
        assert (
            cleanup_once(
                db, get_settings(), store=files.store, now=cleanup_at - timedelta(seconds=1)
            )
            == {}
        )
        assert db.get(StorageQuotaModel, 1, populate_existing=True).used_bytes == len(content) * 2
        assert staging_key in files.store.objects
        assert cleanup_once(db, get_settings(), store=files.store, now=cleanup_at) == {"deleted": 1}
        assert db.get(StorageQuotaModel, 1, populate_existing=True).used_bytes == len(content)
        assert db.get(StoredFileModel, staging_id, populate_existing=True).status == "deleted"
        assert db.get(StoredFileModel, original_id, populate_existing=True).status == "ready"
    assert staging_key not in files.store.objects
    assert files.store.objects[original_key] == content

    with _db() as db:
        db.get(ExtractionRunModel, completed.json()["extraction_run"]["id"]).status = "completed"
        db.commit()
    assert files.client.delete(f"/documents/{did}").status_code == 204
    with _db() as db:
        assert db.get(StorageQuotaModel, 1).used_bytes == len(content)
        assert cleanup_once(
            db, get_settings(), store=files.store, now=cleanup_at + timedelta(seconds=1)
        ) == {"deleted": 1}
        assert db.get(StorageQuotaModel, 1, populate_existing=True).used_bytes == 0
        assert db.get(StoredFileModel, original_id, populate_existing=True).status == "deleted"
    assert original_key not in files.store.objects


@pytest.mark.parametrize("erase_expired", [False, True])
def test_reservation_expiring_during_parsing_cannot_be_written_or_recreated(
    files, monkeypatch, erase_expired
):
    from my_agents.api import document_files

    receipt, _ = files.stage()
    parse_original = document_files.parse_original
    expired_original_keys = []

    def expire_while_parsing(original, content, settings):
        parsed = parse_original(original, content, settings)
        expired_original_keys.append(original.object_key)
        with _db() as db:
            row = db.get(StoredFileModel, original.id)
            row.reserved_until = datetime.now(UTC) - timedelta(minutes=5)
            db.commit()
            if erase_expired:
                assert cleanup_once(db, settings, store=files.store) == {"deleted": 1}
                assert (
                    db.get(StoredFileModel, original.id, populate_existing=True).status == "deleted"
                )
        return parsed

    monkeypatch.setattr(document_files, "parse_original", expire_while_parsing)
    _error(files.complete(receipt["upload_id"]), 503, "storage_unavailable")
    assert files.store.puts == []
    assert files.dispatched == []
    assert all(key not in files.store.objects for key in expired_original_keys)
    with _db() as db:
        assert db.scalars(select(DocumentModel)).all() == []
        assert db.scalars(select(ExtractionRunModel)).all() == []


def test_old_bucket_upload_cannot_refresh_or_complete_against_current_bucket(files, monkeypatch):
    receipt, _ = files.stage()
    signatures = list(files.store.put_signatures)
    monkeypatch.setenv("MY_AGENTS_R2_BUCKET_NAME", "different-deployment-bucket")
    get_settings.cache_clear()
    _error(
        files.client.post(f"{files.path}/{receipt['upload_id']}/refresh"),
        503,
        "storage_unavailable",
    )
    _error(files.complete(receipt["upload_id"]), 503, "storage_unavailable")
    assert files.store.put_signatures == signatures
    assert files.store.reads == []
    assert files.store.puts == []
    assert files.dispatched == []


def test_guest_denial_precedes_storage_configuration_or_object_lookup(files, monkeypatch):
    from my_agents.api import document_files

    guest = TestClient(create_app())
    _guest_login(guest)
    kb_id = _create_kb(guest, "Guest documents")
    monkeypatch.setenv("MY_AGENTS_STORAGE_PROVIDER", "disabled")
    get_settings.cache_clear()
    provider_calls = []

    def unconfigured_store(settings):
        provider_calls.append(settings.storage_provider)
        raise StorageUnavailable("Object storage is not configured.")

    monkeypatch.setattr(document_files, "get_object_store", unconfigured_store)
    upload_path = f"/knowledge-bases/{kb_id}/documents/uploads"
    denied = guest.post(
        upload_path,
        json={
            "title": "Guest source",
            "filename": "source.txt",
            "content_type": "text/plain",
            "byte_size": 5,
            "sha256": hashlib.sha256(b"hello").hexdigest(),
        },
    )
    _error(denied, 403, "permission_denied")
    for response in (
        guest.post(f"{upload_path}/missing/refresh"),
        guest.post(f"{upload_path}/missing/complete"),
        guest.get("/documents/missing/original"),
        guest.post("/documents/missing/reprocess", json={}),
    ):
        _error(response, 403, "permission_denied")
    assert provider_calls == []
    guest.close()


@pytest.mark.parametrize(
    "error, expected_code",
    [
        (
            "document_processing_fidelity_failed: source facts were omitted",
            "document_processing_fidelity_failed",
        ),
        (
            "RuntimeError: untrusted provider diagnostic",
            "document_processing_failed",
        ),
        (None, None),
    ],
)
def test_extraction_poll_exposes_stable_error_codes_for_localized_copy(files, error, expected_code):
    receipt, _ = files.stage()
    result = files.complete(receipt["upload_id"]).json()
    document_id = result["document"]["id"]
    run_id = result["extraction_run"]["id"]
    with _db() as db:
        run = db.get(ExtractionRunModel, run_id)
        run.status = "failed" if error else "completed"
        run.error = error
        db.commit()
    response = files.client.get(f"/documents/{document_id}/extraction-runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["error_code"] == expected_code
    assert response.json()["status"] == ("failed" if error else "completed")
    listed = files.client.get(f"/documents/{document_id}/extraction-runs")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["error_code"] == expected_code


@pytest.mark.parametrize("nested", [False, True])
def test_manual_content_edit_clears_processing_provenance_but_title_edit_preserves_it(
    files, nested
):
    receipt, _ = files.stage()
    result = files.complete(receipt["upload_id"]).json()
    document_id = result["document"]["id"]
    provenance = {
        "schema_version": 1,
        "mode": "standard",
        "provider": "local",
        "model": None,
        "version": "test-processor-v1",
        "input_sha256": result["document"]["source_sha256"],
        "output_sha256": hashlib.sha256(b"# Source\n\nKeep exact facts.").hexdigest(),
        "section_count": 1,
        "warnings": [],
    }
    with _db() as db:
        document = db.get(DocumentModel, document_id)
        original_id = document.original_file_id
        document.processing_provenance_json = json.dumps(provenance)
        document.processed_at = datetime.now(UTC)
        db.get(ExtractionRunModel, result["extraction_run"]["id"]).status = "completed"
        db.commit()
    path = (
        f"/knowledge-bases/{files.kb_id}/documents/{document_id}"
        if nested
        else f"/documents/{document_id}"
    )
    renamed = files.client.patch(path, json={"title": "A clearer source title"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["processing_provenance"] == provenance
    assert renamed.json()["processed_at"] is not None
    assert datetime.fromisoformat(renamed.json()["processed_at"]).utcoffset() == timedelta(0)
    with _db() as db:
        document = db.get(DocumentModel, document_id)
        assert json.loads(document.processing_provenance_json) == provenance
        assert document.processed_at is not None

    edited = files.client.patch(path, json={"content": "Manually corrected source text."})
    assert edited.status_code == 200, edited.text
    assert edited.json()["processing_provenance"] is None
    assert edited.json()["processed_at"] is None
    assert edited.json()["original_retained"] is True
    with _db() as db:
        document = db.get(DocumentModel, document_id)
        assert document.content == "Manually corrected source text."
        assert document.processing_provenance_json is None
        assert document.processed_at is None
        assert document.original_file_id == original_id


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("processing_status", ["pending", "running"])
def test_content_edit_cannot_race_processing_but_title_edit_is_allowed(
    files, nested, processing_status
):
    receipt, _ = files.stage()
    result = files.complete(receipt["upload_id"]).json()
    did = result["document"]["id"]
    run_id = result["extraction_run"]["id"]
    with _db() as db:
        db.get(ExtractionRunModel, run_id).status = processing_status
        original_content = db.get(DocumentModel, did).content
        db.commit()
    path = f"/knowledge-bases/{files.kb_id}/documents/{did}" if nested else f"/documents/{did}"
    rejected = files.client.patch(
        path, json={"title": "This title must not apply", "content": "Racing manual edit"}
    )
    _error(rejected, 409, "reprocess_in_progress")
    with _db() as db:
        document = db.get(DocumentModel, did)
        assert document.content == original_content
        assert document.title == result["document"]["title"]
        assert db.get(ExtractionRunModel, run_id).candidate_content == original_content

    renamed = files.client.patch(path, json={"title": "Allowed metadata rename"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Allowed metadata rename"
    with _db() as db:
        assert db.get(DocumentModel, did).content == original_content
        assert db.get(ExtractionRunModel, run_id).status == processing_status


@pytest.mark.parametrize("manager_type", ["root", "system"])
def test_system_knowledge_manager_can_upload_originals_but_normal_user_cannot(files, manager_type):
    with _db() as db:
        db.get(UserModel, files.user_id).user_type = manager_type
        db.commit()
    system_kb = files.client.post(
        "/knowledge-bases", json={"name": "System source originals", "scope": "system"}
    )
    assert system_kb.status_code == 201, system_kb.text
    files.kb_id = system_kb.json()["id"]
    normal = TestClient(create_app())
    _signup_login(normal, "system-files-normal@example.com")
    content = b"System source facts."
    denied = normal.post(
        files.path,
        json={
            "title": "Unauthorized system file",
            "filename": "source.txt",
            "content_type": "text/plain",
            "byte_size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    assert denied.status_code == 404, denied.text
    assert files.store.put_signatures == []

    receipt, _ = files.stage(content)
    upload_path = f"{files.path}/{receipt['upload_id']}"
    for method, suffix in (("post", "/refresh"), ("post", "/complete"), ("delete", "")):
        denied = getattr(normal, method)(f"{upload_path}{suffix}")
        assert denied.status_code == 404, denied.text
    refreshed = files.client.post(f"{upload_path}/refresh")
    assert refreshed.status_code == 200, refreshed.text
    completed = files.complete(receipt["upload_id"])
    assert completed.status_code == 201, completed.text
    document = completed.json()["document"]
    assert document["knowledge_base_id"] == files.kb_id
    assert document["original_retained"] is True
    assert files.client.get(f"/documents/{document['id']}/original").status_code == 200
    assert normal.get(f"/documents/{document['id']}/original").status_code == 404
    normal.close()


def test_private_original_uploads_require_persistent_ownership_database(files, monkeypatch):
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    get_settings.cache_clear()
    ephemeral = TestClient(create_app())
    _signup_login(ephemeral, "ephemeral-files@example.com")
    kb_id = _create_kb(ephemeral, "Ephemeral metadata")
    health = ephemeral.get("/health")
    assert health.status_code == 200, health.text
    assert health.json()["frontend_config"]["documents"]["direct_uploads"] is False
    content = b"This original needs durable ownership."
    admission = ephemeral.post(
        f"/knowledge-bases/{kb_id}/documents/uploads",
        json={
            "title": "Unsafe ephemeral admission",
            "filename": "source.txt",
            "content_type": "text/plain",
            "byte_size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    _error(admission, 503, "storage_unavailable")
    multipart = ephemeral.post(
        f"/knowledge-bases/{kb_id}/documents/upload",
        data={"title": "Unsafe ephemeral multipart"},
        files={"file": ("source.txt", content, "text/plain")},
    )
    _error(multipart, 503, "storage_unavailable")
    assert files.store.put_signatures == []
    assert files.store.reads == []
    assert files.store.puts == []
    assert files.store.objects == {}
    assert files.dispatched == []
    with _db() as db:
        assert db.scalars(select(DocumentUploadSessionModel)).all() == []
        assert db.scalars(select(StoredFileModel)).all() == []
        assert db.scalars(select(DocumentModel)).all() == []
    ephemeral.close()
