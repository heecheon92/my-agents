"""Seed a local SQLite demo account, knowledge base, document, and ingestion run.

This script is intentionally local-demo-only. It refuses in-memory or non-SQLite URLs so
it cannot accidentally mutate production data. Use it with a file-backed SQLite database
such as `/tmp/my-agents-openai-interactive.db`.
"""

from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from my_agents.auth.models import UserModel
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentModel,
    EntityMentionModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
    KnowledgeBaseScope,
)
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings

DEMO_EMAIL = "test@test.com"
DEMO_PASSWORD = "correct horse battery staple"
DEMO_KNOWLEDGE_BASE_NAME = "V1 Demo Knowledge Base"
DEMO_DOCUMENT_TITLE = "V1 Portfolio Chat Service Demo"
DEMO_DOCUMENT_CONTENT = """The portfolio chat service uses FastAPI for the backend API.

LangGraph routes assistant messages and Server-Sent Events stream answer_delta chunks.

SQLite or Postgres stores app-owned users, documents, conversations, runs, events,
and citations for refresh-safe demos.
"""


@dataclass(frozen=True)
class LocalDemoSeedResult:
    """Printable result of local demo seeding."""

    database_url: str
    database_path: Path
    user_id: str
    email: str
    password: str
    knowledge_base_id: str
    document_id: str
    extraction_run_id: str
    chunk_count: int
    entity_count: int
    relationship_count: int
    created_user: bool
    created_knowledge_base: bool
    created_document: bool
    created_extraction_run: bool


def seed_local_demo(
    *,
    settings: Settings,
    email: str = DEMO_EMAIL,
    password: str = DEMO_PASSWORD,
    reset_database: bool = False,
) -> LocalDemoSeedResult:
    """Seed local demo data in a file-backed SQLite database."""
    database_path = _sqlite_database_path(settings.database_url)
    if reset_database and database_path.exists():
        database_path.unlink()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    reset_database_caches()
    initialize_database(settings)

    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        return _seed_with_session(
            db=db,
            settings=settings,
            database_path=database_path,
            email=email,
            password=password,
        )


def _seed_with_session(
    *,
    db: Session,
    settings: Settings,
    database_path: Path,
    email: str,
    password: str,
) -> LocalDemoSeedResult:
    normalized_email = email.strip().casefold()
    if not normalized_email:
        raise ValueError("demo email must not be blank")
    if not password.strip():
        raise ValueError("demo password must not be blank")

    now = datetime.now(UTC)
    hasher = PasswordHasher()
    user = db.scalar(select(UserModel).where(UserModel.email == normalized_email))
    created_user = user is None
    if user is None:
        user = UserModel(
            id=str(uuid.uuid4()),
            email=normalized_email,
            password_hash=hasher.hash(password),
            email_verified_at=now,
        )
    else:
        user.password_hash = hasher.hash(password)
        user.email_verified_at = user.email_verified_at or now
    db.add(user)
    db.flush()

    knowledge_base = db.scalar(
        select(KnowledgeBaseModel).where(
            KnowledgeBaseModel.owner_user_id == user.id,
            KnowledgeBaseModel.scope == KnowledgeBaseScope.PERSONAL.value,
            KnowledgeBaseModel.name == DEMO_KNOWLEDGE_BASE_NAME,
        )
    )
    created_knowledge_base = knowledge_base is None
    if knowledge_base is None:
        knowledge_base = KnowledgeBaseModel(
            name=DEMO_KNOWLEDGE_BASE_NAME,
            scope=KnowledgeBaseScope.PERSONAL.value,
            owner_user_id=user.id,
            group_id=None,
        )
        db.add(knowledge_base)
        db.flush()

    document = db.scalar(
        select(DocumentModel).where(
            DocumentModel.owner_user_id == user.id,
            DocumentModel.knowledge_base_id == knowledge_base.id,
            DocumentModel.title == DEMO_DOCUMENT_TITLE,
        )
    )
    created_document = document is None
    if document is None:
        document = DocumentModel(
            title=DEMO_DOCUMENT_TITLE,
            content=DEMO_DOCUMENT_CONTENT,
            owner_user_id=user.id,
            group_id=None,
            knowledge_base_id=knowledge_base.id,
        )
        db.add(document)
        db.flush()

    extraction_run = db.scalar(
        select(ExtractionRunModel)
        .where(ExtractionRunModel.document_id == document.id)
        .order_by(ExtractionRunModel.created_at.desc(), ExtractionRunModel.id.desc())
    )
    created_extraction_run = extraction_run is None
    if extraction_run is None:
        summary = KnowledgeExtractionService(db).ingest_document(document)
        extraction_run = summary.run
        chunk_count = summary.chunk_count
        entity_count = summary.entity_count
        relationship_count = summary.relationship_count
    else:
        db.commit()
        chunk_count = _count_chunks(db, extraction_run.id)
        entity_count = _count_entities(db, extraction_run.id)
        relationship_count = _count_relationships(db, extraction_run.id)

    return LocalDemoSeedResult(
        database_url=settings.database_url,
        database_path=database_path,
        user_id=user.id,
        email=user.email,
        password=password,
        knowledge_base_id=knowledge_base.id,
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        chunk_count=chunk_count,
        entity_count=entity_count,
        relationship_count=relationship_count,
        created_user=created_user,
        created_knowledge_base=created_knowledge_base,
        created_document=created_document,
        created_extraction_run=created_extraction_run,
    )


def _sqlite_database_path(database_url: str) -> Path:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        raise ValueError("local demo seed only supports SQLite database URLs")
    if not url.database or url.database == ":memory:":
        raise ValueError("local demo seed requires a file-backed SQLite database, not memory")
    return Path(url.database).expanduser().resolve()


def _count_chunks(db: Session, extraction_run_id: str) -> int:
    return len(
        db.scalars(
            select(DocumentChunkModel).where(
                DocumentChunkModel.extraction_run_id == extraction_run_id
            )
        ).all()
    )


def _count_entities(db: Session, extraction_run_id: str) -> int:
    return len(
        {
            mention.entity_id
            for mention in db.scalars(
                select(EntityMentionModel).where(
                    EntityMentionModel.extraction_run_id == extraction_run_id
                )
            ).all()
        }
    )


def _count_relationships(db: Session, extraction_run_id: str) -> int:
    return len(
        db.scalars(
            select(EntityRelationshipModel).where(
                EntityRelationshipModel.extraction_run_id == extraction_run_id
            )
        ).all()
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed local demo data into a file-backed SQLite database."
    )
    parser.add_argument("--email", default=DEMO_EMAIL, help="Demo account email.")
    parser.add_argument("--password", default=DEMO_PASSWORD, help="Demo account password.")
    parser.add_argument(
        "--reset-database",
        action="store_true",
        help=(
            "Delete the configured SQLite database file before seeding. "
            "Use only when the dev server is stopped."
        ),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    settings = Settings(_env_file=".env")
    result = seed_local_demo(
        settings=settings,
        email=args.email,
        password=args.password,
        reset_database=args.reset_database,
    )
    print("Local demo seed complete")
    print(f"database_url={result.database_url}")
    print(f"database_path={result.database_path}")
    print(f"email={result.email}")
    print(f"password={result.password}")
    print(f"user_id={result.user_id}")
    print(f"knowledge_base_id={result.knowledge_base_id}")
    print(f"document_id={result.document_id}")
    print(f"extraction_run_id={result.extraction_run_id}")
    print(f"chunk_count={result.chunk_count}")
    print(f"entity_count={result.entity_count}")
    print(f"relationship_count={result.relationship_count}")
    print(f"created_user={result.created_user}")
    print(f"created_knowledge_base={result.created_knowledge_base}")
    print(f"created_document={result.created_document}")
    print(f"created_extraction_run={result.created_extraction_run}")
    print("sample_prompt=How does the portfolio chat service stream answers and persist app state?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
