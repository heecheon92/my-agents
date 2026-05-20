"""Local demo seed helper tests."""

from __future__ import annotations

from sqlalchemy import select

from my_agents.auth.models import UserModel
from my_agents.knowledge.models import DocumentModel, ExtractionRunModel, KnowledgeBaseModel
from my_agents.persistence.database import _sessionmaker_for_url, reset_database_caches
from my_agents.settings import Settings
from scripts.local_demo_seed import DEMO_PASSWORD, seed_local_demo


def _settings(monkeypatch, database_url: str) -> Settings:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", database_url)
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    return Settings(_env_file=None)


def test_local_demo_seed_creates_verified_user_document_and_ingestion(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'demo.db'}"
    settings = _settings(monkeypatch, database_url)

    result = seed_local_demo(settings=settings)

    assert result.email == "test@test.com"
    assert result.password == DEMO_PASSWORD
    assert result.created_user is True
    assert result.created_knowledge_base is True
    assert result.created_document is True
    assert result.created_extraction_run is True
    assert result.chunk_count >= 1
    assert result.entity_count >= 1

    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        user = db.scalar(select(UserModel).where(UserModel.email == result.email))
        assert user is not None
        assert user.email_verified_at is not None
        assert db.get(KnowledgeBaseModel, result.knowledge_base_id) is not None
        assert db.get(DocumentModel, result.document_id) is not None
        assert db.get(ExtractionRunModel, result.extraction_run_id) is not None

    reset_database_caches()


def test_local_demo_seed_is_idempotent_for_extraction_runs(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'demo.db'}"
    settings = _settings(monkeypatch, database_url)

    first = seed_local_demo(settings=settings)
    second = seed_local_demo(settings=settings)

    assert second.created_user is False
    assert second.created_knowledge_base is False
    assert second.created_document is False
    assert second.created_extraction_run is False
    assert second.user_id == first.user_id
    assert second.knowledge_base_id == first.knowledge_base_id
    assert second.document_id == first.document_id
    assert second.extraction_run_id == first.extraction_run_id

    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        runs = db.scalars(
            select(ExtractionRunModel).where(ExtractionRunModel.document_id == first.document_id)
        ).all()
        assert len(runs) == 1

    reset_database_caches()


def test_local_demo_seed_reset_database_recreates_file(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    database_path = tmp_path / "demo.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    settings = _settings(monkeypatch, database_url)
    first = seed_local_demo(settings=settings)

    reset = seed_local_demo(settings=settings, reset_database=True)

    assert database_path.exists()
    assert reset.created_user is True
    assert reset.user_id != first.user_id

    reset_database_caches()
