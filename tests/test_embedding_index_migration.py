"""Migration preserves old vectors as unknown, with explicit re-indexing required."""

from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


@pytest.fixture(autouse=True)
def preserve_test_logging(monkeypatch):
    monkeypatch.setattr("logging.config.fileConfig", lambda *args, **kwargs: None)


def test_legacy_rows_remain_unknown_and_roundtrip_preserves_vectors(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'index-migration.sqlite'}"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20261017_0040")
    engine = create_engine(url)
    try:
        with engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO knowledge_bases (id,name,scope,owner_user_id,purpose,creat"
                    "ed_at) VALUES ('k','fixture','personal','u','standard',CURRENT_TIMESTA"
                    "MP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO documents (id,title,content,source_type,owner_user_id,know"
                    "ledge_base_id,created_at) VALUES ('d','fixture','legacy evidence','tex"
                    "t','u','k',CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO extraction_runs (id,document_id,status,created_at) VALUES "
                    "('r','d','completed',CURRENT_TIMESTAMP)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO document_chunks (id,document_id,extraction_run_id,ordinal,"
                    "content,start_offset,end_offset,embedding_json) VALUES ('c','d','r',0,"
                    "'legacy evidence',0,15,'[1,0]')"
                )
            )
            db.execute(
                text(
                    "INSERT INTO document_metadata_profiles (id,document_id,extraction_run_"
                    "id,generated_title,description,summary,keywords_json,topics_json,entit"
                    "ies_json,language,confidence,generator,model,prompt_version,search_tex"
                    "t,embedding_json,created_at) VALUES ('p','d','r','title','description'"
                    ",'summary','[]','[]','[]','en','high','fixture','metadata-model','v1',"
                    "'legacy evidence','[1,0]',CURRENT_TIMESTAMP)"
                )
            )
        command.upgrade(config, "20261017_0041")
        for table in ["document_chunks", "document_metadata_profiles"]:
            assert any(
                fk["referred_table"] == "embedding_indexes"
                for fk in inspect(engine).get_foreign_keys(table)
            )
            with engine.connect() as db:
                assert db.execute(
                    text(f"SELECT embedding_json,embedding_index_id FROM {table}")
                ).one() == ("[1,0]", None)
        with engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM embedding_indexes")) == 0
        command.downgrade(config, "20261017_0040")
        for table in ["document_chunks", "document_metadata_profiles"]:
            with engine.connect() as db:
                assert db.scalar(text(f"SELECT embedding_json FROM {table}")) == "[1,0]"
        command.upgrade(config, "20261017_0041")
    finally:
        engine.dispose()


def test_postgres_migration_has_nullable_identity_and_no_guessed_backfill(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "postgresql+psycopg://localhost/offline_test")
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "20261017_0040:20261017_0041", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE embedding_indexes" in sql
    assert "REFERENCES embedding_indexes (id)" in sql
    assert "UPDATE document_chunks" not in sql
    assert "UPDATE document_metadata_profiles" not in sql
