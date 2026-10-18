"""Private-file migration preserves old evidence and refuses to orphan cloud objects."""

from __future__ import annotations

import sqlite3
from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command

PREVIOUS_REVISION = "20261017_0041"
REVISION = "20261018_0042"
NEW_TABLES = {"stored_files", "storage_quota", "storage_cleanup", "document_upload_sessions"}


@pytest.fixture(autouse=True)
def preserve_test_logging(monkeypatch):
    monkeypatch.setattr("logging.config.fileConfig", lambda *args, **kwargs: None)


@pytest.fixture
def legacy_database(tmp_path, monkeypatch):
    path = tmp_path / "private-files-migration.sqlite"
    url = f"sqlite+pysqlite:///{path}"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(url)
    try:
        with engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO users (id,email,nickname,password_hash,approval_status,"
                    "account_type,user_type,created_at) VALUES ('u','fixture@example.test',"
                    "'fixture','unused-test-hash','approved','registered','normal',CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO knowledge_bases (id,name,scope,owner_user_id,purpose,created_at) "
                    "VALUES ('k','fixture','personal','u','standard',CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO documents (id,title,content,source_type,source_sha256,"
                    "owner_user_id,knowledge_base_id,created_at) "
                    "VALUES ('d','fixture','unchanged source',"
                    "'markdown',:sha,'u','k',CURRENT_TIMESTAMP)"
                ),
                {"sha": "a" * 64},
            )
            db.execute(
                text(
                    "INSERT INTO extraction_runs (id,document_id,status,created_at) "
                    "VALUES ('e','d','completed',CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO document_parse_artifacts (id,document_id,source_type,"
                    "parser_provider,parser_name,markdown_content,created_at) "
                    "VALUES ('artifact','d','markdown','local','fixture-parser',"
                    "'unchanged source',CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO document_chunks (id,document_id,extraction_run_id,ordinal,content,"
                    "start_offset,end_offset,embedding_json) "
                    "VALUES ('chunk','d','e',0,'unchanged source',0,16,'[1,0]')"
                )
            )
            db.execute(
                text(
                    "INSERT INTO conversations (id,owner_user_id,title,created_at) "
                    "VALUES ('conversation','u','fixture',CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO agent_runs (id,conversation_id,user_id,status,created_at) "
                    "VALUES ('run','conversation','u','completed',CURRENT_TIMESTAMP)"
                )
            )
        yield config, engine, path
    finally:
        engine.dispose()


def test_upgrade_preserves_legacy_content_and_defaults_without_inventing_originals(
    legacy_database,
) -> None:
    config, engine, _path = legacy_database
    command.upgrade(config, REVISION)

    with engine.connect() as db:
        assert db.execute(
            text(
                "SELECT content,source_sha256,original_file_id,ingestion_mode,"
                "processing_provenance_json,processed_at FROM documents WHERE id='d'"
            )
        ).one() == ("unchanged source", "a" * 64, None, "standard", None, None)
        assert db.execute(
            text(
                "SELECT ingestion_mode,candidate_content,candidate_elements_json "
                "FROM extraction_runs WHERE id='e'"
            )
        ).one() == ("standard", None, None)
        assert db.execute(
            text("SELECT markdown_content,processing_provenance_json FROM document_parse_artifacts")
        ).one() == ("unchanged source", None)
        assert db.execute(
            text(
                "SELECT document_retrieval_priority,document_ingestion_mode FROM users WHERE id='u'"
            )
        ).one() == ("automatic", "standard")
        assert (
            db.scalar(text("SELECT document_retrieval_priority FROM agent_runs WHERE id='run'"))
            == "automatic"
        )
        assert db.execute(text("SELECT content,embedding_json FROM document_chunks")).one() == (
            "unchanged source",
            "[1,0]",
        )
        for table in NEW_TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0

    inspector = inspect(engine)
    assert NEW_TABLES.issubset(inspector.get_table_names())
    assert any(
        fk["constrained_columns"] == ["original_file_id"] and fk["referred_table"] == "stored_files"
        for fk in inspector.get_foreign_keys("documents")
    )
    assert any(
        item["name"] == "ix_documents_original_file_id"
        for item in inspector.get_indexes("documents")
    )


def test_empty_storage_downgrade_and_reupgrade_preserve_legacy_evidence(legacy_database) -> None:
    config, engine, _path = legacy_database
    command.upgrade(config, REVISION)
    with engine.begin() as db:
        db.execute(text("INSERT INTO storage_quota (id,used_bytes) VALUES (1,0)"))

    command.downgrade(config, PREVIOUS_REVISION)

    assert not NEW_TABLES.intersection(inspect(engine).get_table_names())
    with engine.connect() as db:
        assert db.execute(text("SELECT content,source_sha256 FROM documents")).one() == (
            "unchanged source",
            "a" * 64,
        )
        assert db.scalar(text("SELECT embedding_json FROM document_chunks")) == "[1,0]"
        assert db.scalar(text("SELECT markdown_content FROM document_parse_artifacts")) == (
            "unchanged source"
        )
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
    command.upgrade(config, REVISION)
    with engine.connect() as db:
        assert db.scalar(text("SELECT original_file_id FROM documents")) is None
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == REVISION


def _insert_stored_file(db, *, file_id: str = "file", byte_size: int = 12) -> None:
    db.execute(
        text(
            "INSERT INTO stored_files (id,owner_user_id,bucket,object_key,byte_size,sha256,"
            "content_type,filename,status,created_at) VALUES (:id,'u','unit-test-files',"
            "'originals/file',:size,:sha,'text/plain','fixture.txt','ready',CURRENT_TIMESTAMP)"
        ),
        {"id": file_id, "size": byte_size, "sha": "b" * 64},
    )


@pytest.mark.parametrize("obligation", ["stored_file", "cleanup", "quota", "upload_session"])
def test_downgrade_refuses_to_discard_private_file_cleanup_obligations(
    legacy_database, obligation: str
) -> None:
    config, engine, _path = legacy_database
    command.upgrade(config, REVISION)
    with engine.begin() as db:
        if obligation == "stored_file":
            _insert_stored_file(db)
        elif obligation == "cleanup":
            db.execute(
                text(
                    "INSERT INTO storage_cleanup "
                    "(id,bucket,object_key,not_before,attempts,created_at) "
                    "VALUES ('cleanup','unit-test-files','staging/file',CURRENT_TIMESTAMP,"
                    "0,CURRENT_TIMESTAMP)"
                )
            )
        elif obligation == "quota":
            db.execute(text("INSERT INTO storage_quota (id,used_bytes) VALUES (1,12)"))
        else:
            _insert_stored_file(db)
            db.execute(
                text(
                    "INSERT INTO document_upload_sessions (id,owner_user_id,knowledge_base_id,"
                    "stored_file_id,title,ingestion_mode,staging_key,status,created_at,expires_at,"
                    "signed_until) VALUES ('upload','u','k','file','fixture','standard',"
                    "'staging/file','pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )

    with pytest.raises(RuntimeError, match="Remove retained originals and drain object cleanup"):
        command.downgrade(config, PREVIOUS_REVISION)

    assert NEW_TABLES.issubset(inspect(engine).get_table_names())
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
        assert db.scalar(text("SELECT content FROM documents")) == "unchanged source"


def test_storage_constraints_protect_byte_accounting_and_independent_object_keys(
    legacy_database,
) -> None:
    config, engine, _path = legacy_database
    command.upgrade(config, REVISION)

    with pytest.raises(IntegrityError), engine.begin() as db:
        _insert_stored_file(db, byte_size=-1)
    with pytest.raises(IntegrityError), engine.begin() as db:
        db.execute(text("INSERT INTO storage_quota (id,used_bytes) VALUES (2,0)"))
    with pytest.raises(IntegrityError), engine.begin() as db:
        db.execute(text("INSERT INTO storage_quota (id,used_bytes) VALUES (1,-1)"))
    with engine.begin() as db:
        _insert_stored_file(db)
    with pytest.raises(IntegrityError), engine.begin() as db:
        _insert_stored_file(db, file_id="duplicate")

    with engine.connect() as db:
        db.exec_driver_sql("PRAGMA foreign_keys=ON")
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE documents SET original_file_id='missing' WHERE id='d'"))
        db.rollback()


def test_sqlite_offline_upgrade_sql_is_executable_without_copying_legacy_tables(
    legacy_database, monkeypatch
) -> None:
    _config, engine, path = legacy_database
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", f"sqlite+pysqlite:///{path}")
    command.upgrade(config, f"{PREVIOUS_REVISION}:{REVISION}", sql=True)
    sql = output.getvalue()

    assert "ALTER TABLE documents ADD COLUMN original_file_id" in sql
    assert "REFERENCES stored_files (id)" in sql
    assert "_alembic_tmp" not in sql
    assert "CREATE TABLE documents" not in sql
    assert "UPDATE documents" not in sql
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(sql)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    with engine.connect() as db:
        assert db.execute(text("SELECT content,original_file_id FROM documents")).one() == (
            "unchanged source",
            None,
        )


def test_postgres_offline_upgrade_and_downgrade_include_constraints_and_cleanup_guard(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "postgresql+psycopg://localhost/offline_test")
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, f"{PREVIOUS_REVISION}:{REVISION}", sql=True)
    sql = output.getvalue()
    for table in NEW_TABLES:
        assert f"CREATE TABLE {table}" in sql
    assert "FOREIGN KEY(original_file_id) REFERENCES stored_files (id)" in sql
    assert "document_retrieval_priority VARCHAR(20) DEFAULT 'automatic' NOT NULL" in sql
    assert "UPDATE documents" not in sql

    output.truncate(0)
    output.seek(0)
    command.downgrade(config, f"{REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_sql = output.getvalue()
    assert "DO $$ BEGIN IF EXISTS" in downgrade_sql
    assert "Remove retained originals and drain object cleanup before downgrade" in downgrade_sql
    assert downgrade_sql.index("IF EXISTS") < downgrade_sql.index("DROP TABLE stored_files")
    assert "DROP CONSTRAINT fk_documents_original_file_id_stored_files" in downgrade_sql


def test_sqlite_offline_downgrade_requires_an_online_cleanup_check(monkeypatch) -> None:
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini", output_buffer=StringIO())
    with pytest.raises(RuntimeError, match="requires an online object-cleanup check"):
        command.downgrade(config, f"{REVISION}:{PREVIOUS_REVISION}", sql=True)
