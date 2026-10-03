"""Expired guest erasure uses real foreign keys and preserves other owners' data."""

import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.store.memory import InMemoryStore
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from my_agents.auth.guest_cleanup import cleanup_batch, delete_expired_guest
from my_agents.auth.models import (
    GuestAccessCodeModel,
    GuestAccessRequestModel,
    GuestDeletionAuditModel,
    SessionModel,
    UserModel,
)
from my_agents.auth.service import AuthService
from my_agents.conversations.continuity_models import ConversationSummaryModel
from my_agents.conversations.models import (
    AgentEventModel,
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from my_agents.groups.models import GroupModel
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentModel,
    DocumentPermissionModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
)
from my_agents.memory.models import UserMemorySettingsModel
from my_agents.persistence.database import Base
from my_agents.persistence.langgraph import LangGraphPersistenceResources
from my_agents.persistence.models import import_all_models
from my_agents.settings import Settings

KEY = "test-only-guest-cleanup-hmac-key-32-bytes"
NOW = datetime.now(UTC)
CUTOFF = NOW - timedelta(days=1)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'cleanup.db'}")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    import_all_models()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as db:
        yield db


def guest(db, email="Guest@Example.com"):
    service = AuthService(db)
    code = service.issue_guest_access_code(email=email, ttl=timedelta(minutes=15))
    user = service.redeem_guest_access_code(code=code.code, access_ttl=timedelta(days=1)).user
    user.guest_expires_at = NOW - timedelta(days=2)
    db.commit()
    return user


def private_data(db, user):
    kb = KnowledgeBaseModel(owner_user_id=user.id, name="Private KB", scope="personal")
    db.add(kb)
    db.flush()
    doc = DocumentModel(
        owner_user_id=user.id,
        knowledge_base_id=kb.id,
        title="Private document",
        content="OpenAI uses LangGraph to build an assistant.",
    )
    db.add(doc)
    db.commit()
    KnowledgeExtractionService(db).ingest_document(doc)
    conv = ConversationModel(owner_user_id=user.id, title="Private conversation")
    db.add(conv)
    db.flush()
    msg = MessageModel(conversation_id=conv.id, role="user", content="Private question")
    run = AgentRunModel(conversation_id=conv.id, user_id=user.id, status="waiting_for_input")
    db.add_all([msg, run])
    db.flush()
    db.add(AgentEventModel(run_id=run.id, sequence=1, event_type="run_started", payload_json="{}"))
    db.add(UserMemorySettingsModel(user_id=user.id))
    db.add(
        ConversationSummaryModel(
            conversation_id=conv.id,
            owner_user_id=user.id,
            covered_message_id=msg.id,
            source_digest="test-digest",
            policy_version="test",
            model="test",
            body_json='{"summary":"Private summary"}',
        )
    )
    db.commit()
    return kb, doc, conv, run


def erase(db, user_id, resources=None, dry_run=False):
    return delete_expired_guest(
        db,
        user_id=user_id,
        cutoff=CUTOFF,
        email_hmac_key=KEY,
        resources=resources or LangGraphPersistenceResources(),
        dry_run=dry_run,
    )


def test_complete_private_erasure_preserves_other_guest_and_hmac_history(db):
    user = guest(db)
    user_id = user.id
    kb, doc, conv, run = private_data(db, user)
    other = guest(db, "other@example.com")
    other_kb, other_doc, _, other_run = private_data(db, other)
    run_id, doc_id = run.id, doc.id
    deleted_threads = []

    class Checkpointer:
        def delete_thread(self, thread_id):
            deleted_threads.append(thread_id)

    store = InMemoryStore()
    store.put((user_id, "memories", "facts"), "orphan", {"content": "Private fact"})
    store.put((other.id, "memories", "facts"), "keep", {"content": "Other fact"})
    resources = LangGraphPersistenceResources(checkpointer=Checkpointer(), store=store)
    assert erase(db, user_id, resources) == "deleted"
    assert deleted_threads == [run_id]
    db.expire_all()
    assert db.get(UserModel, user_id) is None
    assert db.get(DocumentModel, doc_id) is None
    assert db.get(AgentRunModel, run_id) is None
    assert db.get(DocumentModel, other_doc.id) is not None
    assert db.get(AgentRunModel, other_run.id) is not None
    assert store.search((user_id, "memories")) == []
    assert len(store.search((other.id, "memories"))) == 1
    assert (
        db.scalar(
            select(func.count()).select_from(SessionModel).where(SessionModel.user_id == user_id)
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(DocumentChunkModel)
            .where(DocumentChunkModel.document_id == doc_id)
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(GuestAccessCodeModel)
            .where(GuestAccessCodeModel.guest_user_id == user_id)
        )
        == 0
    )
    assert (
        db.scalar(
            select(GuestAccessRequestModel).where(
                GuestAccessRequestModel.email == "guest@example.com"
            )
        )
        is None
    )
    audit = db.get(GuestDeletionAuditModel, user_id)
    assert (
        audit.email_fingerprint
        == hmac.new(KEY.encode(), b"guest@example.com", hashlib.sha256).hexdigest()
    )
    assert json.loads(audit.counts_json)["documents"] == 1
    assert "Private" not in audit.counts_json and "@" not in audit.counts_json
    assert erase(db, user_id, resources) == "not_eligible"
    assert len(db.scalars(select(GuestDeletionAuditModel)).all()) == 1


def test_dry_run_and_grace_protect_data(db):
    user = guest(db)
    user_id = user.id
    _, doc, _, _ = private_data(db, user)
    assert erase(db, user_id, dry_run=True) == "eligible"
    assert db.get(DocumentModel, doc.id) is not None
    assert db.get(GuestDeletionAuditModel, user_id) is None
    user.guest_expires_at = NOW - timedelta(hours=1)
    db.commit()
    assert erase(db, user_id) == "not_eligible"
    user.guest_expires_at = NOW + timedelta(hours=1)
    db.commit()
    assert erase(db, user_id) == "not_eligible"
    user.account_type = "registered"
    user.guest_expires_at = NOW - timedelta(days=2)
    db.commit()
    assert erase(db, user_id) == "not_eligible"


@pytest.mark.parametrize(
    "reason", ["active_run", "active_ingestion", "shared_ownership", "shared_document"]
)
def test_unsafe_candidates_are_deferred_intact(db, reason):
    user = guest(db)
    user_id = user.id
    _, doc, _, run = private_data(db, user)
    if reason == "active_run":
        run.status = "running"
    elif reason == "active_ingestion":
        extraction = db.scalar(
            select(ExtractionRunModel).where(ExtractionRunModel.document_id == doc.id)
        )
        extraction.status = "running"
    elif reason == "shared_ownership":
        db.add(GroupModel(name="Shared", created_by_user_id=user_id))
    else:
        db.add(DocumentPermissionModel(document_id=doc.id, user_id="another-user"))
    db.commit()
    assert erase(db, user_id) == reason
    assert db.get(UserModel, user_id) is not None
    assert db.get(DocumentModel, doc.id) is not None
    assert db.get(GuestDeletionAuditModel, user_id) is None


def test_checkpoint_failure_rolls_back_then_retry_completes(db):
    user = guest(db)
    user_id = user.id
    private_data(db, user)

    class BrokenCheckpointer:
        def delete_thread(self, thread_id):
            raise RuntimeError("temporary checkpoint failure")

    with pytest.raises(RuntimeError):
        erase(db, user_id, LangGraphPersistenceResources(checkpointer=BrokenCheckpointer()))
    assert db.get(UserModel, user_id) is not None
    assert db.get(GuestDeletionAuditModel, user_id) is None
    assert erase(db, user_id) == "deleted"


def test_concurrent_cleanup_claims_one_account_once(db, engine):
    user_id = guest(db).id

    def sweep():
        with Session(engine) as session:
            return erase(session, user_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sweep(), range(2)))
    assert sorted(results) == ["deleted", "not_eligible"]
    assert len(db.scalars(select(GuestDeletionAuditModel)).all()) == 1


def test_keyset_scan_progresses_past_deferred_guests(db):
    for i in range(26):
        db.add(
            UserModel(
                id=f"guest-{i:02}",
                email=f"guest-{i}@example.com",
                nickname="Guest",
                password_hash="unused",
                account_type="guest",
                guest_expires_at=NOW - timedelta(days=2),
            )
        )
        if i < 25:
            db.add(GroupModel(name="Shared", created_by_user_id=f"guest-{i:02}"))
    db.commit()
    settings = Settings(_env_file=None, MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY=KEY)
    counts, cursor = cleanup_batch(db, settings, LangGraphPersistenceResources())
    assert counts == {"shared_ownership": 25}
    counts, cursor = cleanup_batch(db, settings, LangGraphPersistenceResources(), after_id=cursor)
    assert counts == {"deleted": 1}


def test_enabled_cleanup_requires_persistent_db_and_hmac_key():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="file-backed"):
        Settings(
            _env_file=None,
            MY_AGENTS_GUEST_CLEANUP_ENABLED=True,
            MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY=KEY,
        )
    with pytest.raises(ValidationError, match="32 bytes"):
        Settings(
            _env_file=None,
            MY_AGENTS_DATABASE_URL="sqlite+pysqlite:///test.db",
            MY_AGENTS_GUEST_CLEANUP_ENABLED=True,
        )


def test_only_orphaned_entities_are_deleted(db):
    from my_agents.knowledge.models import EntityMentionModel, EntityModel

    user = guest(db)
    user_id = user.id
    _, doc, _, _ = private_data(db, user)
    other = guest(db, "other@example.com")
    _, other_doc, _, _ = private_data(db, other)
    secret = EntityModel(name="Private entity")
    shared = EntityModel(name="Shared entity")
    db.add_all([secret, shared])
    db.flush()
    secret_id, shared_id = secret.id, shared.id
    for entity, target in [(secret, doc), (shared, doc), (shared, other_doc)]:
        chunk = db.scalar(
            select(DocumentChunkModel).where(DocumentChunkModel.document_id == target.id)
        )
        db.add(
            EntityMentionModel(
                entity_id=entity.id,
                chunk_id=chunk.id,
                document_id=target.id,
                extraction_run_id=chunk.extraction_run_id,
            )
        )
    db.commit()
    assert erase(db, user_id) == "deleted"
    assert db.get(EntityModel, secret_id) is None
    assert db.get(EntityModel, shared_id) is not None


def test_mid_transaction_failure_leaves_all_product_data_retryable(db, engine):
    user_id = guest(db).id
    user = db.get(UserModel, user_id)
    _, doc, _, _ = private_data(db, user)
    doc_id = doc.id

    def fail_last_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM users"):
            raise RuntimeError("injected final deletion failure")

    event.listen(engine, "before_cursor_execute", fail_last_delete)
    try:
        with pytest.raises(RuntimeError):
            erase(db, user_id)
    finally:
        event.remove(engine, "before_cursor_execute", fail_last_delete)
    assert db.get(UserModel, user_id) is not None
    assert db.get(DocumentModel, doc_id) is not None
    assert db.get(GuestDeletionAuditModel, user_id) is None
    assert erase(db, user_id) == "deleted"


def test_cli_dry_run_requires_explicit_apply(db, engine, monkeypatch, capsys):
    from scripts.cleanup_expired_guests import main

    user_id = guest(db).id
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", str(engine.url))
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"dry_run": True, "counts": {"eligible": 1}}
    assert db.get(UserModel, user_id) is not None
    with pytest.raises(SystemExit):
        main(["--apply"])


def test_cleanup_batch_stops_before_next_account(db):
    from threading import Event

    guest(db)
    stop = Event()
    stop.set()
    counts, _ = cleanup_batch(
        db, Settings(_env_file=None), LangGraphPersistenceResources(), stop=stop
    )
    assert counts == {}
    assert db.scalar(select(func.count()).select_from(UserModel)) == 1


def test_cleanup_worker_finishes_current_account_only_on_shutdown(db, engine, monkeypatch):
    import asyncio
    from threading import Event

    from my_agents.auth.guest_cleanup import guest_cleanup_loop

    first = guest(db)
    private_data(db, first)
    second = guest(db, "second@example.com")
    private_data(db, second)
    entered, release = Event(), Event()
    deleted = []

    class BlockingCheckpointer:
        def delete_thread(self, thread_id):
            deleted.append(thread_id)
            entered.set()
            assert release.wait(5)

    settings = Settings(
        _env_file=None,
        MY_AGENTS_DATABASE_URL=str(engine.url),
        MY_AGENTS_GUEST_CLEANUP_ENABLED=True,
        MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY=KEY,
    )

    async def exercise():
        task = asyncio.create_task(
            guest_cleanup_loop(
                settings, LangGraphPersistenceResources(checkpointer=BlockingCheckpointer())
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    db.expire_all()
    assert len(deleted) == 1
    assert db.scalar(select(func.count()).select_from(UserModel)) == 1
    assert db.scalar(select(func.count()).select_from(GuestDeletionAuditModel)) == 1
