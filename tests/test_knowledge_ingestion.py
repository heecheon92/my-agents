"""Knowledge-base ingestion and deterministic extraction tests."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import my_agents.knowledge.extraction as extraction_module
from my_agents.knowledge.extraction import (
    _chunk_pdf_text,
    _deterministic_embedding,
    _extract_entity_names,
)
from my_agents.knowledge.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentModel,
    DocumentPermissionModel,
    EntityMentionModel,
    EntityRelationshipModel,
    ExtractionRunModel,
)
from my_agents.knowledge.pdf_uploads import PdfUploadError, parse_uploaded_pdf
from my_agents.persistence.database import get_database_session

from .conftest import load_app, verify_latest_auth_email


class FakeEmbeddingProvider:
    provider = "fake"
    model = "fake-embedding-model"
    dimensions = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(index), float(len(text)), 1.0] for index, text in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(load_app())


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _text_pdf(*pages: str) -> bytes:
    objects = ["1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj"]
    kids = []
    for index, page in enumerate(pages, start=1):
        page_obj = 2 + (index * 2) - 1
        stream_obj = page_obj + 1
        kids.append(f"{page_obj} 0 R")
        escaped = page.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
        objects.append(
            f"{page_obj} 0 obj << /Type /Page /Parent 2 0 R /Contents {stream_obj} 0 R >> endobj"
        )
        objects.append(
            f"{stream_obj} 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj"
        )
    objects.insert(
        1, f"2 0 obj << /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >> endobj"
    )
    return ("%PDF-1.4\n" + "\n".join(objects) + "\n%%EOF\n").encode()


def _compressed_text_pdf(*pages: str) -> bytes:
    objects = ["1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj"]
    kids = []
    for index, page in enumerate(pages, start=1):
        page_obj = 2 + (index * 2) - 1
        stream_obj = page_obj + 1
        kids.append(f"{page_obj} 0 R")
        escaped = page.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        compressed = zlib.compress(stream)
        objects.append(
            f"{page_obj} 0 obj << /Type /Page /Parent 2 0 R /Contents {stream_obj} 0 R >> endobj"
        )
        objects.append(
            b"".join(
                [
                    (
                        f"{stream_obj} 0 obj "
                        f"<< /Length {len(compressed)} /Filter /FlateDecode >> stream\n"
                    ).encode(),
                    compressed,
                    b"\nendstream endobj",
                ]
            ).decode("latin-1")
        )
    objects.insert(
        1, f"2 0 obj << /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >> endobj"
    )
    return ("%PDF-1.4\n" + "\n".join(objects) + "\n%%EOF\n").encode("latin-1")


def _binary_noise_pdf() -> bytes:
    noise = b"$\xa6\xedO\x7f /\x89\xe1\x88e<m\xde(\x8d\x97Xz\xf7x\xb5q;)\xff\xfe"
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj",
        b"4 0 obj << /Length "
        + str(len(noise)).encode()
        + b" >> stream\n"
        + noise
        + b"\nendstream endobj",
    ]
    return b"%PDF-1.4\n" + b"\n".join(objects) + b"\n%%EOF\n"


def _raw_stream_pdf(stream: str) -> bytes:
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj",
        f"4 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj",
    ]
    return ("%PDF-1.4\n" + "\n".join(objects) + "\n%%EOF\n").encode()


def _database_rows(statement):  # noqa: ANN001 - SQLAlchemy statement type is verbose
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return db.scalars(statement).all()
    finally:
        session_generator.close()


def test_personal_knowledge_base_document_ingestion_creates_extraction_artifacts(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "kb-owner@example.com")

    kb = client.post("/knowledge-bases", json={"name": "Personal KB", "scope": "personal"})
    assert kb.status_code == 201
    kb_id = kb.json()["id"]

    document = client.post(
        "/documents",
        json={
            "title": "Agent Notes",
            "content": "OpenAI builds agents with LangGraph.\n\nLangGraph helps Heecheon Park.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert document.json()["knowledge_base_id"] == kb_id
    assert document.json()["source_type"] == "text"
    assert document.json()["source_filename"] is None

    ingest = client.post(f"/documents/{document.json()['id']}/ingest")

    assert ingest.status_code == 200
    payload = ingest.json()
    assert payload["status"] == "completed"
    assert payload["chunk_count"] == 2
    assert payload["entity_count"] >= 3
    assert payload["relationship_count"] >= 1

    runs = client.get(f"/documents/{document.json()['id']}/extraction-runs")
    assert runs.status_code == 200
    assert runs.json()[0]["id"] == payload["id"]
    chunks = _database_rows(
        select(DocumentChunkModel).where(DocumentChunkModel.document_id == document.json()["id"])
    )
    assert {chunk.source_page for chunk in chunks} == {None}


def test_ingestion_uses_configured_embedding_provider(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        extraction_module,
        "get_embedding_provider",
        lambda: FakeEmbeddingProvider(),
    )
    client = _client(monkeypatch)
    _signup_login(client, "provider-ingest@example.com")

    document = client.post(
        "/documents",
        json={
            "title": "Provider Embeddings",
            "content": "First provider chunk.\n\nSecond provider chunk.",
        },
    )
    assert document.status_code == 201

    ingest = client.post(f"/documents/{document.json()['id']}/ingest")

    assert ingest.status_code == 200
    chunks = _database_rows(
        select(DocumentChunkModel)
        .where(DocumentChunkModel.document_id == document.json()["id"])
        .order_by(DocumentChunkModel.ordinal)
    )
    assert [chunk.embedding_json for chunk in chunks] == [
        "[0.0, 21.0, 1.0]",
        "[1.0, 22.0, 1.0]",
    ]


def test_pdf_parser_tolerates_invalid_octal_like_literal_escape() -> None:
    parsed = parse_uploaded_pdf(
        filename="invalid-escape.pdf",
        content_type="application/pdf",
        content=_raw_stream_pdf(r"BT /F1 12 Tf 72 720 Td (Invalid \9 escape) Tj ET"),
    )

    assert parsed.content == "Invalid 9 escape"
    assert parsed.page_count == 1


def test_pdf_parser_decodes_flate_streams() -> None:
    parsed = parse_uploaded_pdf(
        filename="compressed.pdf",
        content_type="application/pdf",
        content=_compressed_text_pdf("Compressed resume mentions FastAPI and LangGraph."),
    )

    assert parsed.content == "Compressed resume mentions FastAPI and LangGraph."
    assert parsed.page_count == 1


def test_pdf_parser_rejects_binary_literal_noise() -> None:
    with pytest.raises(PdfUploadError, match="does not contain extractable text"):
        parse_uploaded_pdf(
            filename="noise.pdf",
            content_type="application/pdf",
            content=_binary_noise_pdf(),
        )


def test_pdf_upload_persists_metadata_and_ingests_page_provenance(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "pdf-owner@example.com")

    document = client.post(
        "/documents/upload",
        data={"title": "Portfolio PDF"},
        files={
            "file": (
                "portfolio.pdf",
                _text_pdf(
                    "OpenAI Agents page one mentions LangGraph.",
                    "Heecheon Park page two cites FastAPI.",
                ),
                "application/pdf",
            )
        },
    )

    assert document.status_code == 201
    payload = document.json()
    assert payload["title"] == "Portfolio PDF"
    assert payload["source_type"] == "pdf"
    assert payload["source_filename"] == "portfolio.pdf"
    assert payload["source_content_type"] == "application/pdf"
    assert payload["source_byte_size"] > 0
    assert len(payload["source_sha256"]) == 64
    assert payload["source_page_count"] == 2
    assert payload["parser_name"] == "deterministic_stream_fallback_v1"

    persisted = _database_rows(select(DocumentModel).where(DocumentModel.id == payload["id"]))
    assert persisted[0].content.count("\f") == 1

    ingest = client.post(f"/documents/{payload['id']}/ingest")
    assert ingest.status_code == 200
    assert ingest.json()["status"] == "completed"
    assert ingest.json()["chunk_count"] == 2

    chunks = _database_rows(
        select(DocumentChunkModel)
        .where(DocumentChunkModel.document_id == payload["id"])
        .order_by(DocumentChunkModel.ordinal)
    )
    assert [chunk.source_page for chunk in chunks] == [1, 2]
    assert "LangGraph" in chunks[0].content
    assert "FastAPI" in chunks[1].content
    assert len(_deterministic_embedding(chunks[0].content)) == 32


@pytest.mark.parametrize(
    ("filename", "content_type", "source_type", "source_content_type", "parser_name"),
    [
        ("portfolio-notes.md", "text/markdown", "markdown", "text/markdown", "utf8_markdown_v1"),
        ("portfolio-notes.txt", "text/plain", "text", "text/plain", "utf8_text_v1"),
    ],
)
def test_text_upload_persists_metadata_and_ingests_for_retrieval(
    monkeypatch,
    filename: str,
    content_type: str,
    source_type: str,
    source_content_type: str,
    parser_name: str,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, f"{source_type}-upload-owner@example.com")

    phrase = f"TextUpload {source_type} source says Heecheon Park uses LangGraph retrieval"
    document = client.post(
        "/documents/upload",
        data={"title": f"{source_type.title()} Upload"},
        files={
            "file": (
                filename,
                f"# Portfolio notes\r\n\r\n{phrase} with FastAPI citations.\n".encode(),
                content_type,
            )
        },
    )

    assert document.status_code == 201
    payload = document.json()
    assert payload["source_type"] == source_type
    assert payload["source_filename"] == filename
    assert payload["source_content_type"] == source_content_type
    assert payload["source_byte_size"] > 0
    assert len(payload["source_sha256"]) == 64
    assert payload["source_page_count"] is None
    assert payload["parser_name"] == parser_name

    persisted = _database_rows(select(DocumentModel).where(DocumentModel.id == payload["id"]))
    assert "\r" not in persisted[0].content
    assert phrase in persisted[0].content

    ingest = client.post(f"/documents/{payload['id']}/ingest")
    assert ingest.status_code == 200
    assert ingest.json()["status"] == "completed"
    assert ingest.json()["chunk_count"] >= 1

    conversation = client.post("/conversations", json={"title": f"{source_type} RAG"})
    assert conversation.status_code == 201
    run = client.post(
        f"/conversations/{conversation.json()['id']}/runs",
        json={"message": f"What does my uploaded {source_type} file say about TextUpload?"},
    )

    assert run.status_code == 200
    run_payload = run.json()
    assert run_payload["citations"]
    assert run_payload["citations"][0]["document_id"] == payload["id"]
    assert run_payload["citations"][0]["source_filename"] == filename
    assert phrase in run_payload["reply"]


def test_langgraph_academy_pdf_regression_extracts_real_text_when_available() -> None:
    sample = (
        Path.home() / "Downloads/LangChain_Academy_-_Introduction_to_LangGraph_-_Motivation.pdf"
    )
    if not sample.exists():
        pytest.skip("local LangGraph Academy sample PDF is not available")

    parsed = parse_uploaded_pdf(
        filename=sample.name,
        content_type="application/pdf",
        content=sample.read_bytes(),
    )
    chunks = _chunk_pdf_text(parsed.content)
    entities = {name for chunk, *_ in chunks for name in _extract_entity_names(chunk)}

    assert parsed.parser_name == "pypdf_text_v2"
    assert parsed.page_count == 17
    assert "LangChain Academy" in parsed.content
    assert "LangGraph" in parsed.content
    assert len(chunks) >= 10
    assert len(entities) >= 10


def test_pdf_upload_ingest_and_conversation_retrieval_pipeline(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "pdf-rag-owner@example.com")

    resume_phrase = "Heecheon Park builds FastAPI and LangGraph portfolio systems"
    document = client.post(
        "/documents/upload",
        data={"title": "Resume PDF"},
        files={
            "file": (
                "resume.pdf",
                _compressed_text_pdf(f"{resume_phrase} with permission-aware retrieval."),
                "application/pdf",
            )
        },
    )
    assert document.status_code == 201

    ingest = client.post(f"/documents/{document.json()['id']}/ingest")
    assert ingest.status_code == 200
    assert ingest.json()["chunk_count"] == 1

    conversation = client.post("/conversations", json={"title": "Resume PDF RAG"})
    assert conversation.status_code == 201
    run = client.post(
        f"/conversations/{conversation.json()['id']}/runs",
        json={"message": "Tell me about me from my uploaded resume."},
    )

    assert run.status_code == 200
    payload = run.json()
    assert payload["citations"]
    assert payload["citations"][0]["document_id"] == document.json()["id"]
    assert payload["citations"][0]["source_filename"] == "resume.pdf"
    assert resume_phrase in payload["reply"]


def test_owner_can_delete_uploaded_pdf_and_cleanup_ingestion_artifacts(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    reader = _client(monkeypatch)
    _signup_login(owner, "delete-pdf-owner@example.com")
    reader_id = _signup_login(reader, "delete-pdf-reader@example.com")

    document = owner.post(
        "/documents/upload",
        data={"title": "Delete Me PDF"},
        files={
            "file": (
                "delete-me.pdf",
                _text_pdf("Delete Cleanup PDF mentions FastAPI and LangGraph."),
                "application/pdf",
            )
        },
    )
    assert document.status_code == 201
    document_id = document.json()["id"]

    permission = owner.patch(
        f"/documents/{document_id}/permissions",
        json={"user_id": reader_id, "can_read": True},
    )
    assert permission.status_code == 200
    ingest = owner.post(f"/documents/{document_id}/ingest")
    assert ingest.status_code == 200

    conversation = owner.post("/conversations", json={"title": "Delete cleanup RAG"})
    assert conversation.status_code == 201
    run = owner.post(
        f"/conversations/{conversation.json()['id']}/runs",
        json={"message": "What does Delete Cleanup PDF mention?"},
    )
    assert run.status_code == 200
    assert run.json()["citations"]

    assert _database_rows(select(DocumentModel).where(DocumentModel.id == document_id))
    assert _database_rows(
        select(DocumentChunkModel).where(DocumentChunkModel.document_id == document_id)
    )
    assert _database_rows(
        select(ExtractionRunModel).where(ExtractionRunModel.document_id == document_id)
    )
    assert _database_rows(
        select(EntityMentionModel).where(EntityMentionModel.document_id == document_id)
    )
    assert _database_rows(
        select(EntityRelationshipModel).where(EntityRelationshipModel.document_id == document_id)
    )
    assert _database_rows(
        select(DocumentPermissionModel).where(DocumentPermissionModel.document_id == document_id)
    )
    assert _database_rows(select(CitationModel).where(CitationModel.document_id == document_id))

    deleted = owner.delete(f"/documents/{document_id}")

    assert deleted.status_code == 204
    assert owner.get(f"/documents/{document_id}").status_code == 404
    assert _database_rows(select(DocumentModel).where(DocumentModel.id == document_id)) == []
    assert (
        _database_rows(
            select(DocumentChunkModel).where(DocumentChunkModel.document_id == document_id)
        )
        == []
    )
    assert (
        _database_rows(
            select(ExtractionRunModel).where(ExtractionRunModel.document_id == document_id)
        )
        == []
    )
    assert (
        _database_rows(
            select(EntityMentionModel).where(EntityMentionModel.document_id == document_id)
        )
        == []
    )
    assert (
        _database_rows(
            select(EntityRelationshipModel).where(
                EntityRelationshipModel.document_id == document_id
            )
        )
        == []
    )
    assert (
        _database_rows(
            select(DocumentPermissionModel).where(
                DocumentPermissionModel.document_id == document_id
            )
        )
        == []
    )
    assert (
        _database_rows(select(CitationModel).where(CitationModel.document_id == document_id)) == []
    )


def test_non_owner_cannot_delete_document_without_manage_authorization(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    reader = _client(monkeypatch)
    _signup_login(owner, "delete-denied-owner@example.com")
    reader_id = _signup_login(reader, "delete-denied-reader@example.com")

    document = owner.post(
        "/documents",
        json={"title": "Private delete guard", "content": "reader can see but not delete"},
    )
    assert document.status_code == 201
    document_id = document.json()["id"]
    grant = owner.patch(
        f"/documents/{document_id}/permissions",
        json={"user_id": reader_id, "can_read": True},
    )
    assert grant.status_code == 200
    assert reader.get(f"/documents/{document_id}").status_code == 200

    denied = reader.delete(f"/documents/{document_id}")

    assert denied.status_code == 404
    assert owner.get(f"/documents/{document_id}").status_code == 200


def test_upload_rejects_unsupported_or_unsafe_input(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "pdf-safety@example.com")

    unsupported = client.post(
        "/documents/upload",
        data={"title": "Docx"},
        files={"file": ("notes.docx", b"not supported", "text/plain")},
    )
    assert unsupported.status_code == 415

    binary_text = client.post(
        "/documents/upload",
        data={"title": "Binary text"},
        files={"file": ("notes.txt", b"hello\x00not text", "text/plain")},
    )
    assert binary_text.status_code == 400

    unsafe_name = client.post(
        "/documents/upload",
        data={"title": "Bad name"},
        files={"file": ("../bad.pdf", _text_pdf("Safe text"), "application/pdf")},
    )
    assert unsafe_name.status_code == 400


def test_group_knowledge_base_requires_group_membership(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "group-kb-owner@example.com")
    _signup_login(outsider, "group-kb-outsider@example.com")
    group_id = owner.post("/groups", json={"name": "KB Group"}).json()["id"]

    kb = owner.post(
        "/knowledge-bases",
        json={"name": "Group KB", "scope": "group", "group_id": group_id},
    )
    assert kb.status_code == 201
    assert kb.json()["group_id"] == group_id

    denied = outsider.post(
        "/knowledge-bases",
        json={"name": "Denied", "scope": "group", "group_id": group_id},
    )
    assert denied.status_code == 403
    assert outsider.get("/knowledge-bases").json() == []
