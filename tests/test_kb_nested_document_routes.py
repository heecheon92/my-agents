"""KB-nested document route contract tests for the knowledge-base boundary."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.knowledge.models import DocumentModel
from my_agents.persistence.database import get_database_session

from .conftest import load_app, verify_latest_auth_email


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


def _create_personal_kb(client: TestClient, name: str) -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _documents_by_id(document_ids: set[str]) -> list[DocumentModel]:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return db.scalars(select(DocumentModel).where(DocumentModel.id.in_(document_ids))).all()
    finally:
        session_generator.close()


def test_kb_detail_and_nested_document_routes_enforce_kb_boundary(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "kb-boundary-owner@example.com")
    _signup_login(outsider, "kb-boundary-outsider@example.com")
    kb_id = _create_personal_kb(owner, "Boundary KB")
    other_kb_id = _create_personal_kb(owner, "Other KB")

    detail = owner.get(f"/knowledge-bases/{kb_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == kb_id
    assert outsider.get(f"/knowledge-bases/{kb_id}").status_code == 404

    created = owner.post(
        f"/knowledge-bases/{kb_id}/documents",
        json={"title": "Nested Text", "content": "KB-scoped text content."},
    )
    assert created.status_code == 201
    created_payload = created.json()
    assert created_payload["knowledge_base_id"] == kb_id

    uploaded = owner.post(
        f"/knowledge-bases/{kb_id}/documents/upload",
        data={"title": "Nested Upload"},
        files={"file": ("notes.txt", b"KB-scoped upload content", "text/plain")},
    )
    assert uploaded.status_code == 201
    uploaded_payload = uploaded.json()
    assert uploaded_payload["knowledge_base_id"] == kb_id

    listed = owner.get(f"/knowledge-bases/{kb_id}/documents")
    assert listed.status_code == 200
    listed_payload = listed.json()
    listed_ids = {document["id"] for document in listed_payload}
    assert {created_payload["id"], uploaded_payload["id"]}.issubset(listed_ids)
    assert all(document["knowledge_base_id"] == kb_id for document in listed_payload)
    assert outsider.get(f"/knowledge-bases/{kb_id}/documents").status_code == 404

    wrong_kb_ingest = owner.post(
        f"/knowledge-bases/{other_kb_id}/documents/{created_payload['id']}/ingest"
    )
    assert wrong_kb_ingest.status_code == 404

    sync_ingest = owner.post(f"/knowledge-bases/{kb_id}/documents/{created_payload['id']}/ingest")
    assert sync_ingest.status_code == 200

    runs = owner.get(f"/knowledge-bases/{kb_id}/documents/{created_payload['id']}/extraction-runs")
    assert runs.status_code == 200
    assert runs.json()
    wrong_kb_runs = owner.get(
        f"/knowledge-bases/{other_kb_id}/documents/{created_payload['id']}/extraction-runs"
    )
    assert wrong_kb_runs.status_code == 404

    persisted = _documents_by_id({created_payload["id"], uploaded_payload["id"]})
    assert len(persisted) == 2
    assert all(document.knowledge_base_id == kb_id for document in persisted)


def test_openapi_exposes_kb_nested_document_contract(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    openapi = spec.json()
    paths = openapi["paths"]
    required_paths = {
        "/knowledge-bases/{knowledge_base_id}",
        "/knowledge-bases/{knowledge_base_id}/documents",
        "/knowledge-bases/{knowledge_base_id}/documents/upload",
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest",
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest/async",
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs",
        "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}",
    }
    assert required_paths.issubset(paths.keys())
