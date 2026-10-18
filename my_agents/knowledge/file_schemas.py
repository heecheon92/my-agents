"""Private original upload and reprocessing contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from my_agents.knowledge.schemas import DocumentResponse, ExtractionRunResponse

UPLOAD_MAX_BYTES = 5 * 1024 * 1024
MULTIPART_MAX_BYTES = 4_000_000
UPLOAD_EXTENSIONS = [".pdf", ".md", ".markdown", ".txt", ".xlsx", ".pptx", ".docx"]
IngestionMode = Literal["standard", "ai_assisted"]


class DocumentUploadInitiate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    byte_size: int = Field(ge=1, le=UPLOAD_MAX_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ingestion_mode: IngestionMode | None = None

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if any(ord(c) < 32 for c in value) or "/" in value or "\\" in value:
            raise ValueError("filename must be a plain file name")
        if not any(value.lower().endswith(suffix) for suffix in UPLOAD_EXTENSIONS):
            raise ValueError("unsupported file extension")
        return value


class DocumentUploadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]
    expires_at: datetime
    max_bytes: int = UPLOAD_MAX_BYTES


class DocumentUploadCompleted(BaseModel):
    document: DocumentResponse
    extraction_run: ExtractionRunResponse | None


class DocumentOriginalDownload(BaseModel):
    url: str
    expires_at: datetime
    filename: str


class DocumentReprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ingestion_mode: IngestionMode | None = None
