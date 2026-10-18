"""SQLite polling must remain readable during spill-heavy ingestion transactions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, func, insert, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from my_agents.auth.models import SessionModel, UserModel
from my_agents.auth.service import AuthService
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
)
from my_agents.persistence.database import Base, _engine_for_url
from my_agents.persistence.models import import_all_models


def test_file_sqlite_connections_enable_wal_with_bounded_busy_wait(tmp_path):
    engine = _engine_for_url(f"sqlite+pysqlite:///{tmp_path / 'settings.db'}")
    try:
        with engine.connect() as first, engine.connect() as second:
            assert first.connection.driver_connection is not second.connection.driver_connection
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
                # Keep SQLite's FULL durability rather than reducing fsync guarantees.
                assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+pysqlite:///:memory:",
        "sqlite+pysqlite:///file:shared-memory?mode=memory&cache=shared&uri=true",
        "sqlite+pysqlite:///file::memory:?cache=shared&uri=true",
    ],
)
def test_in_memory_sqlite_does_not_attempt_wal(url):
    engine = _engine_for_url(url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "memory"
    finally:
        engine.dispose()


@pytest.mark.parametrize("app_engine", [False, True], ids=["rollback_control", "wal_fix"])
def test_authenticated_reader_sees_committed_document_during_large_atomic_write(
    tmp_path, app_engine
):
    """Control proves pager spill blocks auth; WAL preserves the previous snapshot."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'concurrent.db'}"
    engine = _engine_for_url(database_url) if app_engine else create_engine(database_url)
    import_all_models()
    Base.metadata.create_all(engine)
    session_token = "synthetic-sqlite-test-session"
    try:
        with Session(engine) as seed:
            seed.add(
                UserModel(
                    id="owner",
                    email="owner@example.com",
                    nickname="Owner",
                    password_hash="fixture",
                )
            )
            seed.flush()
            seed.add(
                SessionModel(
                    id="session",
                    user_id="owner",
                    token_hash=hashlib.sha256(session_token.encode()).hexdigest(),
                    csrf_token_hash="fixture",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            seed.add(
                KnowledgeBaseModel(id="kb", name="KB", scope="personal", owner_user_id="owner")
            )
            seed.flush()
            seed.add(
                DocumentModel(
                    id="document",
                    title="Document",
                    content="prior committed content",
                    owner_user_id="owner",
                    knowledge_base_id="kb",
                )
            )
            seed.flush()
            seed.add(ExtractionRunModel(id="run", document_id="document", status="running"))
            seed.flush()
            seed.add(
                DocumentChunkModel(
                    id="old-chunk",
                    document_id="document",
                    extraction_run_id="run",
                    ordinal=0,
                    content="prior committed chunk",
                    start_offset=0,
                    end_offset=21,
                    embedding_json="[]",
                )
            )
            seed.commit()

        with Session(engine) as writer:
            # Force dirty pages out of the pager cache well before COMMIT. With
            # rollback journaling this takes an EXCLUSIVE lock even on auth tables.
            writer.execute(text("PRAGMA cache_size=10"))
            writer.execute(text("PRAGMA cache_spill=ON"))
            writer.execute(delete(DocumentChunkModel))
            writer.execute(
                update(DocumentModel)
                .where(DocumentModel.id == "document")
                .values(content="new committed content")
            )
            writer.execute(
                insert(DocumentChunkModel),
                [
                    {
                        "id": f"new-chunk-{index}",
                        "document_id": "document",
                        "extraction_run_id": "run",
                        "ordinal": index,
                        "content": "new chunk " + "x" * 8192,
                        "start_offset": 0,
                        "end_offset": 8202,
                        "embedding_json": "[]",
                    }
                    for index in range(640)
                ],
            )
            with Session(engine) as reader:
                assert (
                    writer.connection().connection.driver_connection
                    is not reader.connection().connection.driver_connection
                )
                if app_engine:
                    principal = AuthService(reader).authenticate_session(session_token)
                    assert principal.user_id == "owner"
                    assert (
                        reader.get(DocumentModel, "document").content == "prior committed content"
                    )
                    assert reader.scalar(select(func.count()).select_from(DocumentChunkModel)) == 1
                else:
                    # Bound only the intentionally broken control case, not the fix.
                    reader.execute(text("PRAGMA busy_timeout=50"))
                    with pytest.raises(OperationalError, match="database is locked"):
                        AuthService(reader).authenticate_session(session_token)
            writer.commit()

        with Session(engine) as reader:
            assert AuthService(reader).authenticate_session(session_token).user_id == "owner"
            assert reader.get(DocumentModel, "document").content == "new committed content"
            assert reader.scalar(select(func.count()).select_from(DocumentChunkModel)) == 640
    finally:
        engine.dispose()
