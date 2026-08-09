"""Offline contract tests for temporary OpenAI-hosted document workspaces."""

from __future__ import annotations

import logging
from io import BytesIO
from types import SimpleNamespace
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.api.document_workspace import get_document_workspace_provider
from my_agents.auth.contracts import Principal
from my_agents.document_workspace.formats import document_format_for_filename
from my_agents.document_workspace.models import UsageEventModel
from my_agents.document_workspace.provider import (
    OpenAIDocumentWorkspaceProvider,
    ProviderContainer,
    ProviderContainerFile,
    ProviderExecutionResult,
    ProviderUploadedFile,
)
from my_agents.document_workspace.service import _normalized_response_units, capability_response
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

from .conftest import verify_latest_auth_email


@pytest.fixture(autouse=True)
def _restore_rich_debug_handlers():
    """Keep create_app logging setup from leaking a captured stream to later modules."""
    root_logger = logging.getLogger()
    existing_handlers = set(root_logger.handlers)
    yield
    for handler in list(root_logger.handlers):
        if handler not in existing_handlers and handler.name == "my_agents_rich_debug":
            root_logger.removeHandler(handler)
            handler.close()


class FakeDocumentWorkspaceProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}
        self.container_files: list[ProviderContainerFile] = []
        self.deleted_files: list[str] = []
        self.deleted_containers: list[str] = []
        self.spreadsheet_skill_enabled = False
        self.reasoning: tuple[str, str] | None = None

    def upload_file(
        self,
        *,
        file: BinaryIO,
        filename: str,
        content_type: str,  # noqa: ARG002
        expires_after_seconds: int,  # noqa: ARG002
    ) -> ProviderUploadedFile:
        data = file.read()
        file_id = f"file-{len(self.uploaded) + 1}"
        self.uploaded[file_id] = data
        return ProviderUploadedFile(id=file_id, bytes=len(data), filename=filename)

    def delete_file(self, provider_file_id: str) -> None:
        self.deleted_files.append(provider_file_id)

    def create_container(
        self,
        *,
        name: str,  # noqa: ARG002
        provider_file_ids: list[str],
        idle_ttl_minutes: int,  # noqa: ARG002
        include_spreadsheet_skill: bool,
    ) -> ProviderContainer:
        self.spreadsheet_skill_enabled = include_spreadsheet_skill
        self.container_files = [
            ProviderContainerFile(
                id=file_id,
                path=f"/mnt/data/{file_id}.xlsx",
                bytes=len(self.uploaded[file_id]),
                source="user",
            )
            for file_id in provider_file_ids
        ]
        return ProviderContainer(id="container-1")

    def add_file_to_container(self, *, container_id: str, provider_file_id: str) -> None:  # noqa: ARG002
        self.container_files.append(
            ProviderContainerFile(
                id=provider_file_id,
                path=f"/mnt/data/{provider_file_id}.xlsx",
                bytes=len(self.uploaded[provider_file_id]),
                source="user",
            )
        )

    def list_container_files(self, container_id: str) -> tuple[ProviderContainerFile, ...]:  # noqa: ARG002
        return tuple(self.container_files)

    def execute(
        self,
        *,
        container_id: str,  # noqa: ARG002
        provider_file_ids: list[str],  # noqa: ARG002
        instructions: str,  # noqa: ARG002
        prompt: str,  # noqa: ARG002
        safety_identifier: str,
        reasoning_mode: str,
        reasoning_effort: str,
    ) -> ProviderExecutionResult:
        assert len(safety_identifier) == 64
        assert reasoning_mode in {"standard", "pro"}
        assert reasoning_effort in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        self.reasoning = (reasoning_mode, reasoning_effort)
        self.container_files.append(
            ProviderContainerFile(
                id="artifact-1",
                path="/mnt/data/output/analysis.xlsx",
                bytes=14,
                source="assistant",
            )
        )
        return ProviderExecutionResult(
            response_id="response-1",
            output_text="분석한 스프레드시트를 만들었습니다.",
            usage={"input_tokens": 100, "output_tokens": 20},
            shell_used=True,
        )

    def download_container_file(
        self,
        *,
        container_id: str,  # noqa: ARG002
        provider_file_id: str,
    ):
        assert provider_file_id == "artifact-1"
        yield b"artifact-bytes"

    def delete_container(self, provider_container_id: str) -> None:
        self.deleted_containers.append(provider_container_id)


def _client(monkeypatch) -> tuple[TestClient, FakeDocumentWorkspaceProvider]:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    get_settings.cache_clear()
    provider = FakeDocumentWorkspaceProvider()
    app = create_app()
    app.dependency_overrides[get_document_workspace_provider] = lambda: provider
    return TestClient(app), provider


def _signup_login(client: TestClient) -> None:
    email = "document-workspace@example.com"
    password = "correct horse battery staple"
    response = client.post(
        "/auth/signup",
        json={"email": email, "nickname": "Workspace User", "password": password},
    )
    assert response.status_code == 201
    verify_latest_auth_email(client, email)
    assert (
        client.post("/auth/login", json={"email": email, "password": password}).status_code == 200
    )


def test_document_workspace_capability_is_honest_for_disabled_and_guest() -> None:
    disabled = capability_response(
        settings=Settings(
            _env_file=None,
            MY_AGENTS_RESPONSE_MODE="deterministic",
            MY_AGENTS_DOCUMENT_WORKSPACE_ENABLED=False,
        ),
        principal=Principal(user_id="user-1", session_id="session-1"),
    )
    assert disabled.enabled is False
    assert disabled.eligible is False
    assert disabled.reason_code == "document_workspace_disabled"

    guest = capability_response(
        settings=Settings(
            _env_file=None,
            MY_AGENTS_RESPONSE_MODE="deterministic",
            MY_AGENTS_DOCUMENT_WORKSPACE_ENABLED=True,
            OPENAI_API_KEY="test-only-key",
        ),
        principal=Principal(user_id="guest-1", session_id="session-2", is_guest=True),
    )
    assert guest.enabled is True
    assert guest.eligible is False
    assert guest.reason_code == "guest_document_workspace_forbidden"


def test_format_registry_covers_openai_document_families() -> None:
    assert document_format_for_filename("report.pdf").category == "pdf"
    assert document_format_for_filename("analysis.xlsx").artifact_status == "certified"
    assert document_format_for_filename("memo.docx").category == "rich_document"
    assert document_format_for_filename("slides.pptx").category == "presentation"
    assert document_format_for_filename("notes.md").category == "text_or_code"
    assert document_format_for_filename("video.mp4") is None


def test_document_usage_normalizes_reasoning_tokens_for_cost_ledger() -> None:
    assert _normalized_response_units(
        {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens": 80,
            "output_tokens_details": {"reasoning_tokens": 60},
        },
        shell_used=True,
    ) == {
        "model_input_tokens": 100,
        "model_cached_input_tokens": 20,
        "model_output_tokens": 80,
        "model_reasoning_tokens": 60,
        "hosted_shell_calls": 1,
    }


def test_openai_adapter_uses_network_disabled_container_and_hosted_shell() -> None:
    calls: dict[str, dict] = {}

    class Containers:
        class Files:
            def list(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
                return SimpleNamespace(
                    data=[
                        SimpleNamespace(
                            id="generated-1",
                            path="/mnt/data/output/generated.csv",
                            bytes=None,
                            source=None,
                        )
                    ]
                )

        def __init__(self) -> None:
            self.files = self.Files()

        def create(self, **kwargs):  # noqa: ANN003, ANN201
            calls["container"] = kwargs
            return SimpleNamespace(id="container-1")

    class Responses:
        def create(self, **kwargs):  # noqa: ANN003, ANN201
            calls["response"] = kwargs
            return SimpleNamespace(
                id="response-1",
                output_text="done",
                output=[SimpleNamespace(type="shell_call")],
                usage=SimpleNamespace(
                    model_dump=lambda **_kwargs: {"input_tokens": 10, "output_tokens": 2}
                ),
            )

    client = SimpleNamespace(containers=Containers(), responses=Responses())
    provider = OpenAIDocumentWorkspaceProvider(
        Settings(
            _env_file=None,
            MY_AGENTS_RESPONSE_MODE="deterministic",
            OPENAI_API_KEY="test-only-key",
        ),
        client=client,
    )
    container = provider.create_container(
        name="conversation-1",
        provider_file_ids=["file-1"],
        idle_ttl_minutes=20,
        include_spreadsheet_skill=True,
    )
    result = provider.execute(
        container_id=container.id,
        provider_file_ids=["file-1"],
        instructions="safe instructions",
        prompt="analyze",
        safety_identifier="safety-id",
        reasoning_mode="pro",
        reasoning_effort="max",
    )

    assert calls["container"]["network_policy"] == {"type": "disabled"}
    assert calls["container"]["memory_limit"] == "1g"
    assert calls["container"]["skills"][0]["skill_id"] == "openai-spreadsheets"
    assert calls["response"]["model"] == "gpt-5.6-sol"
    assert calls["response"]["reasoning"] == {"mode": "pro", "effort": "max"}
    assert calls["response"]["tools"] == [
        {
            "type": "shell",
            "environment": {
                "type": "container_reference",
                "container_id": "container-1",
            },
        }
    ]
    assert calls["response"]["store"] is False
    assert result.shell_used is True
    listed = provider.list_container_files("container-1")
    assert listed[0].bytes is None
    assert listed[0].source == "unknown"


def test_attachment_run_artifact_and_download_flow_is_offline(monkeypatch) -> None:  # noqa: ANN001
    client, provider = _client(monkeypatch)
    _signup_login(client)
    conversation = client.post("/conversations", json={"title": "Spreadsheet work"}).json()
    conversation_id = conversation["id"]

    no_consent = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={
            "file": (
                "analysis.xlsx",
                BytesIO(b"xlsx-input"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"provider_consent": "false"},
    )
    assert no_consent.status_code == 400
    assert no_consent.json()["code"] == "document_provider_consent_required"

    uploaded = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={
            "file": (
                "analysis.xlsx",
                BytesIO(b"xlsx-input"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"provider_consent": "true"},
    )
    assert uploaded.status_code == 201
    attachment = uploaded.json()
    assert attachment["status"] == "available"

    completed = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "요약하고 새 표를 만들어줘",
            "attachment_ids": [attachment["id"]],
            "reasoning_mode": "pro",
            "reasoning_effort": "max",
        },
    )
    assert completed.status_code == 200, completed.text
    payload = completed.json()
    assert payload["reply"] == "분석한 스프레드시트를 만들었습니다."
    assert [item["id"] for item in payload["attachments"]] == [attachment["id"]]
    assert payload["artifacts"][0]["filename"] == "analysis.xlsx"
    assert provider.spreadsheet_skill_enabled is True
    assert provider.reasoning == ("pro", "max")

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        usage_events = db.scalars(
            select(UsageEventModel).order_by(UsageEventModel.occurred_at)
        ).all()
    finally:
        session_generator.close()
    assert [event.operation for event in usage_events] == [
        "file_upload",
        "container_start",
        "responses_create",
    ]
    assert len({event.idempotency_key for event in usage_events}) == 3

    events = client.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}/events").json()
    event_types = [item["event_type"] for item in events]
    assert "attachments_ready" in event_types
    assert "document_workspace_started" in event_types
    assert "artifact_created" in event_types

    download = client.get(payload["artifacts"][0]["download_url"])
    assert download.status_code == 200
    assert download.content == b"artifact-bytes"
    assert "attachment" in download.headers["content-disposition"]
