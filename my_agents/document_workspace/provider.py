"""Provider boundary for hosted document understanding and artifact generation."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol

from openai import OpenAI

from my_agents.reasoning import openai_reasoning_payload
from my_agents.reasoning_summaries import provider_reasoning_summary
from my_agents.settings import ReasoningEffort, ReasoningMode, Settings


class DocumentWorkspaceProviderError(RuntimeError):
    """A provider operation failed without exposing provider internals to clients."""


class DocumentWorkspaceProviderConfigurationError(DocumentWorkspaceProviderError):
    """The hosted workspace cannot run with current configuration."""


@dataclass(frozen=True)
class ProviderUploadedFile:
    id: str
    bytes: int
    filename: str


@dataclass(frozen=True)
class ProviderContainer:
    id: str


@dataclass(frozen=True)
class ProviderContainerFile:
    id: str
    path: str
    bytes: int | None
    source: str

    @property
    def content_type(self) -> str:
        guessed, _encoding = mimetypes.guess_type(self.path)
        return guessed or "application/octet-stream"


@dataclass(frozen=True)
class ProviderExecutionResult:
    response_id: str
    output_text: str
    usage: dict[str, object]
    shell_used: bool
    reasoning_summary: str | None = None


class DocumentWorkspaceProvider(Protocol):
    """Narrow provider-neutral operations needed by the product service."""

    provider_name: str

    def upload_file(
        self,
        *,
        file: BinaryIO,
        filename: str,
        content_type: str,
        expires_after_seconds: int,
    ) -> ProviderUploadedFile: ...

    def delete_file(self, provider_file_id: str) -> None: ...

    def create_container(
        self,
        *,
        name: str,
        provider_file_ids: Sequence[str],
        idle_ttl_minutes: int,
        include_spreadsheet_skill: bool,
    ) -> ProviderContainer: ...

    def add_file_to_container(self, *, container_id: str, provider_file_id: str) -> None: ...

    def list_container_files(self, container_id: str) -> tuple[ProviderContainerFile, ...]: ...

    def execute(
        self,
        *,
        container_id: str,
        provider_file_ids: Sequence[str],
        instructions: str,
        prompt: str,
        safety_identifier: str,
        reasoning_mode: ReasoningMode,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderExecutionResult: ...

    def download_container_file(
        self, *, container_id: str, provider_file_id: str
    ) -> Iterator[bytes]: ...

    def delete_container(self, provider_container_id: str) -> None: ...


class OpenAIDocumentWorkspaceProvider:
    """OpenAI Files + Responses + Hosted Shell implementation."""

    provider_name = "openai"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        api_key = settings.openai_api_key_value()
        if client is None and not api_key:
            raise DocumentWorkspaceProviderConfigurationError(
                "OPENAI_API_KEY is required for the document workspace"
            )
        self._settings = settings
        self._client = client or OpenAI(
            api_key=api_key,
            timeout=settings.document_workspace_timeout_seconds,
        )

    def upload_file(
        self,
        *,
        file: BinaryIO,
        filename: str,
        content_type: str,
        expires_after_seconds: int,
    ) -> ProviderUploadedFile:
        try:
            file.seek(0)
            uploaded = self._client.files.create(
                file=(filename, file, content_type),
                purpose="user_data",
                expires_after={"anchor": "created_at", "seconds": expires_after_seconds},
            )
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI file upload failed") from exc
        return ProviderUploadedFile(
            id=str(uploaded.id),
            bytes=int(uploaded.bytes),
            filename=str(uploaded.filename),
        )

    def delete_file(self, provider_file_id: str) -> None:
        try:
            self._client.files.delete(provider_file_id)
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI file deletion failed") from exc

    def create_container(
        self,
        *,
        name: str,
        provider_file_ids: Sequence[str],
        idle_ttl_minutes: int,
        include_spreadsheet_skill: bool,
    ) -> ProviderContainer:
        skills: list[dict[str, str]] = []
        if include_spreadsheet_skill:
            skills.append(
                {
                    "type": "skill_reference",
                    "skill_id": "openai-spreadsheets",
                    "version": "latest",
                }
            )
        try:
            container = self._client.containers.create(
                name=name,
                file_ids=list(provider_file_ids),
                memory_limit="1g",
                network_policy={"type": "disabled"},
                expires_after={"anchor": "last_active_at", "minutes": idle_ttl_minutes},
                skills=skills,
            )
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI container creation failed") from exc
        return ProviderContainer(id=str(container.id))

    def add_file_to_container(self, *, container_id: str, provider_file_id: str) -> None:
        try:
            self._client.containers.files.create(container_id, file_id=provider_file_id)
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI container file mount failed") from exc

    def list_container_files(self, container_id: str) -> tuple[ProviderContainerFile, ...]:
        try:
            page = self._client.containers.files.list(container_id, limit=100, order="asc")
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI container file listing failed") from exc
        return tuple(
            ProviderContainerFile(
                id=str(item.id),
                path=str(item.path),
                bytes=int(item.bytes) if item.bytes is not None else None,
                source=str(item.source or "unknown"),
            )
            for item in page.data
        )

    def execute(
        self,
        *,
        container_id: str,
        provider_file_ids: Sequence[str],
        instructions: str,
        prompt: str,
        safety_identifier: str,
        reasoning_mode: ReasoningMode,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderExecutionResult:
        content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
        content.extend(
            {"type": "input_file", "file_id": provider_file_id}
            for provider_file_id in provider_file_ids
        )
        try:
            response = self._client.responses.create(
                model=self._settings.document_workspace_model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                tools=[
                    {
                        "type": "shell",
                        "environment": {
                            "type": "container_reference",
                            "container_id": container_id,
                        },
                    }
                ],
                tool_choice="auto",
                reasoning=openai_reasoning_payload(
                    model=self._settings.document_workspace_model,
                    mode=reasoning_mode,
                    effort=reasoning_effort,
                ),
                max_output_tokens=self._settings.document_workspace_max_output_tokens,
                safety_identifier=safety_identifier,
                store=False,
            )
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI document execution failed") from exc
        usage = _model_dump(getattr(response, "usage", None))
        output = getattr(response, "output", ()) or ()
        return ProviderExecutionResult(
            response_id=str(response.id),
            output_text=str(response.output_text or "").strip(),
            usage=usage,
            shell_used=any(getattr(item, "type", None) == "shell_call" for item in output),
            reasoning_summary=provider_reasoning_summary(response),
        )

    def download_container_file(
        self, *, container_id: str, provider_file_id: str
    ) -> Iterator[bytes]:
        try:
            response = self._client.containers.files.content.retrieve(
                provider_file_id,
                container_id=container_id,
            )
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI artifact download failed") from exc

        def chunks() -> Iterator[bytes]:
            try:
                yield from response.iter_bytes()
            finally:
                response.close()

        return chunks()

    def delete_container(self, provider_container_id: str) -> None:
        try:
            self._client.containers.delete(provider_container_id)
        except Exception as exc:
            raise DocumentWorkspaceProviderError("OpenAI container deletion failed") from exc


def _model_dump(value: Any) -> dict[str, object]:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        return {}
    dumped = model_dump(mode="json")
    return dict(dumped) if isinstance(dumped, dict) else {}


def new_container_files(
    before: Iterable[ProviderContainerFile],
    after: Iterable[ProviderContainerFile],
) -> tuple[ProviderContainerFile, ...]:
    before_ids = {item.id for item in before}
    return tuple(item for item in after if item.id not in before_ids)
