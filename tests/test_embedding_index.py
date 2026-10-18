"""Embedding provenance, legacy fallback, and permission isolation."""

import json
import os
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from my_agents.auth.models import UserModel
from my_agents.knowledge.embedding_index import (
    compatible_embedding,
    embedding_identity,
    register_embedding_index,
)
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    EmbeddingIndexModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
)
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models
from tests.test_permission_aware_rag import SemanticFakeEmbeddingProvider


@pytest.fixture(params=["sqlite", "postgresql"])
def index_db(request):
    import_all_models()
    admin = None
    schema = None
    if request.param == "postgresql":
        url = os.environ.get("MY_AGENTS_TEST_DATABASE_URL", "")
        if not url.startswith("postgresql"):
            pytest.skip("Optional isolated PostgreSQL database not configured")
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        schema = "embedding_test_" + uuid.uuid4().hex
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            make_url(url).update_query_dict({"options": f"-csearch_path={schema},public"})
        )
        with engine.begin() as connection:
            Base.metadata.create_all(
                connection.execution_options(schema_translate_map={None: schema})
            )
    else:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            for user in ["owner", "other"]:
                db.add(
                    UserModel(
                        id=user, email=f"{user}@example.com", nickname=user, password_hash="fixture"
                    )
                )
                db.flush()
                db.add(
                    KnowledgeBaseModel(
                        id=f"kb-{user}", name=user, scope="personal", owner_user_id=user
                    )
                )
            db.commit()
            yield db
    finally:
        engine.dispose()
        if admin is not None:
            with admin.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def seed(db, name, *, provider=None, owner="owner", content="automobile handbook", vector=None):
    vector = vector or [1.0, 0.0]
    index_id = (
        register_embedding_index(db, embedding_identity(provider, vector)) if provider else None
    )
    document = DocumentModel(
        id=name, title=name, content=content, owner_user_id=owner, knowledge_base_id=f"kb-{owner}"
    )
    db.add(document)
    db.flush()
    run = ExtractionRunModel(id=f"run-{name}", document_id=name, status="completed")
    db.add(run)
    db.flush()
    chunk = DocumentChunkModel(
        id=f"chunk-{name}",
        document_id=name,
        extraction_run_id=run.id,
        ordinal=0,
        content=content,
        start_offset=0,
        end_offset=len(content),
        embedding_index_id=index_id,
        embedding_json=json.dumps(vector),
    )
    db.add(chunk)
    db.flush()
    if db.get_bind().dialect.name == "postgresql":
        table = DocumentChunkModel.__table__
        db.execute(update(table).where(table.c.id == chunk.id).values(embedding_vector=vector))
    db.commit()
    return document, chunk


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "different-provider"),
        ("model", "different-model"),
        ("dimensions", 3),
        ("encoding", "different-encoding"),
        ("preprocessing", "different-preprocessing"),
        ("version", 2),
    ],
)
def test_every_identity_axis_participates_in_compatibility(field, value):
    provider = SemanticFakeEmbeddingProvider()
    vector = [1.0, 0.0]
    identity = embedding_identity(provider, vector)
    assert compatible_embedding(provider, vector, vector, identity.key)
    assert not compatible_embedding(
        provider, vector, vector, replace(identity, **{field: value}).key
    )
    assert not compatible_embedding(provider, vector, vector, None)


def test_vector_retrieval_excludes_unknown_incompatible_and_unauthorized_rows(index_db):
    provider = SemanticFakeEmbeddingProvider()
    wrong = SemanticFakeEmbeddingProvider()
    wrong.model = "same-dimension-different-model"
    seed(index_db, "known", provider=provider)
    seed(index_db, "unknown")
    seed(index_db, "wrong", provider=wrong)
    seed(index_db, "private", provider=provider, owner="other")
    # Even a corrupt same-identity SQL vector must not reach a dimension-mismatched distance.
    _, corrupt = seed(index_db, "bad-dimensions", vector=[1, 0, 0])
    corrupt.embedding_index_id = embedding_identity(provider, [1, 0]).key
    index_db.commit()
    service = RetrievalService(index_db, provider)
    # Car and automobile share a fake semantic vector but no lexical term.
    found = service._direct_authorized_matches(
        user_id="owner",
        query_embedding=[1.0, 0.0],
        terms={"car"},
        knowledge_base_ids=None,
        semantic_only=True,
    )
    assert [item.document.id for item in found] == ["known"]
    lexical = service._json_authorized_matches(
        user_id="owner",
        query_embedding=[1.0, 0.0],
        terms={"automobile"},
        knowledge_base_ids=None,
        semantic_only=False,
    )
    assert {item.document.id for item in lexical} == {"known", "unknown", "wrong", "bad-dimensions"}
    assert {item.source for item in lexical if item.document.id != "known"} == {"keyword_match"}
    assert {
        item.document.id
        for item in service.retrieve_lexical_scoped(user_id="owner", query="automobile", limit=10)
    } == {"known", "unknown", "wrong", "bad-dimensions"}
    assert {
        item.document.id
        for item in service.retrieve_selected_document(
            user_id="owner", document_id="private", query="car"
        )
    } == set()


def test_unknown_metadata_profile_uses_only_lexical_signal(index_db):
    provider = SemanticFakeEmbeddingProvider()
    document, chunk = seed(index_db, "profile-doc")
    index_db.add(
        DocumentMetadataProfileModel(
            document_id=document.id,
            extraction_run_id=chunk.extraction_run_id,
            generated_title="generated",
            description="generated",
            summary="generated",
            keywords_json="[]",
            topics_json="[]",
            entities_json="[]",
            language="en",
            confidence="high",
            generator="test",
            model="metadata-generator",
            prompt_version="v1",
            search_text="automobile",
            embedding_json="[1,0]",
        )
    )
    index_db.commit()
    service = RetrievalService(index_db, provider)

    def retrieve(terms):
        return service._document_metadata_profile_matches(
            user_id="owner",
            terms=terms,
            query_embedding=[1.0, 0.0],
            knowledge_base_ids=None,
            limit=5,
            matched_document_chunk_rows_cache={},
        )

    assert retrieve({"car"}) == []
    assert retrieve({"automobile"})


def test_ingestion_and_reingestion_stamp_known_space_without_guessing_legacy(index_db, monkeypatch):
    provider = SemanticFakeEmbeddingProvider()
    document, _ = seed(index_db, "legacy-reindex")
    monkeypatch.setattr("my_agents.knowledge.extraction.get_embedding_provider", lambda: provider)
    service = KnowledgeExtractionService(index_db)
    service.ingest_document(document)
    expected = embedding_identity(provider, [1.0, 0.0]).key
    chunks = index_db.scalars(
        select(DocumentChunkModel).where(DocumentChunkModel.document_id == document.id)
    ).all()
    profiles = index_db.scalars(
        select(DocumentMetadataProfileModel).where(
            DocumentMetadataProfileModel.document_id == document.id
        )
    ).all()
    assert chunks and profiles
    assert {row.embedding_index_id for row in [*chunks, *profiles]} == {expected}
    index = index_db.get(EmbeddingIndexModel, expected)
    assert index.model == provider.model
    # Re-indexing is explicit. Old vectors are not relabelled to a new provider.
    index_db.execute(update(DocumentChunkModel).values(embedding_index_id=None))
    index_db.commit()
    provider.model = "semantic-fake-v2"
    service.ingest_document(document)
    assert (
        index_db.scalar(select(DocumentChunkModel.embedding_index_id))
        == embedding_identity(provider, [1, 0]).key
    )
    assert index_db.get(EmbeddingIndexModel, expected).model == "semantic-fake-v1"


@pytest.mark.parametrize("vector", [[], [float("nan"), 0], [float("inf"), 0], [True, 0], [1, 0, 0]])
def test_invalid_or_misconfigured_vectors_cannot_be_registered(vector):
    with pytest.raises(ValueError):
        embedding_identity(SemanticFakeEmbeddingProvider(), vector)
