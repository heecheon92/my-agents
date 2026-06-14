"""Safe document-upload dispatch for supported ingestion source files."""

from __future__ import annotations

import hashlib
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from my_agents.knowledge.office_uploads import (
    OfficeUploadError,
    ParsedOfficeDocument,
    parse_uploaded_office_document,
)
from my_agents.knowledge.pdf_uploads import (
    MAX_PDF_UPLOAD_BYTES,
    DoclingExtractionConfig,
    PdfUploadError,
    TesseractOcrConfig,
    parse_uploaded_pdf,
)

MAX_TEXT_UPLOAD_BYTES = MAX_PDF_UPLOAD_BYTES
TEXT_UPLOAD_PARSER_NAME = "utf8_text_v1"
MARKDOWN_UPLOAD_PARSER_NAME = "utf8_markdown_v1"
_SUPPORTED_UPLOAD_SUFFIXES = frozenset({".pdf", ".md", ".markdown", ".txt", ".xlsx", ".pptx"})
_TEXT_UPLOAD_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_OFFICE_UPLOAD_SUFFIXES = frozenset({".xlsx", ".pptx"})
_GENERIC_UPLOAD_CONTENT_TYPES = frozenset({"", "application/octet-stream"})
_MARKDOWN_CONTENT_TYPES = frozenset({"text/markdown", "text/x-markdown", "text/plain"})
_PLAIN_TEXT_CONTENT_TYPES = frozenset({"text/plain"})
_SAFE_CONTROL_CHARACTERS = frozenset({"\t", "\n", "\r", "\f"})
logger = logging.getLogger(__name__)


class DocumentUploadError(ValueError):
    """Raised when an uploaded document is unsafe or unsupported."""


class UnsupportedDocumentUploadError(DocumentUploadError):
    """Raised when the upload file type is not part of the supported V1 set."""


@dataclass(frozen=True)
class ParsedUploadParseArtifact:
    """Derived parser artifact ready for persistence beside the document."""

    parser_provider: str
    parser_name: str
    parser_version: str | None
    parser_mode: str
    markdown_content: str
    elements: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedDocumentUpload:
    """Text and provenance metadata extracted from a supported upload."""

    content: str
    source_type: str
    source_content_type: str
    byte_size: int
    sha256: str
    page_count: int | None
    parser_name: str
    parse_artifact: ParsedUploadParseArtifact | None = None


def parse_uploaded_document(
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
    docling_config: DoclingExtractionConfig | None = None,
    tesseract_config: TesseractOcrConfig | None = None,
) -> ParsedDocumentUpload:
    """Parse a supported upload into normalized text and source metadata."""
    safe_filename = _validate_upload_filename(filename)
    suffix = _filename_suffix(safe_filename)
    logger.info(
        "document_upload.dispatch filename=%s suffix=%s content_type=%s bytes=%d sha256=%s",
        safe_filename,
        suffix,
        content_type,
        len(content),
        hashlib.sha256(content).hexdigest(),
    )
    if suffix == ".pdf":
        return _parse_pdf(
            filename=safe_filename,
            content_type=content_type,
            content=content,
            docling_config=docling_config,
            tesseract_config=tesseract_config,
        )
    if suffix in _OFFICE_UPLOAD_SUFFIXES:
        return _parse_office_file(
            filename=safe_filename,
            content_type=content_type,
            content=content,
        )
    if suffix in _TEXT_UPLOAD_SUFFIXES:
        return _parse_text_file(
            filename=safe_filename,
            suffix=suffix,
            content_type=content_type,
            content=content,
        )
    raise UnsupportedDocumentUploadError(
        "only .pdf, .md, .markdown, .txt, .xlsx, or .pptx uploads are supported"
    )


def _parse_pdf(
    *,
    filename: str,
    content_type: str | None,
    content: bytes,
    docling_config: DoclingExtractionConfig | None,
    tesseract_config: TesseractOcrConfig | None,
) -> ParsedDocumentUpload:
    try:
        parsed = parse_uploaded_pdf(
            filename=filename,
            content_type=content_type,
            content=content,
            docling_config=docling_config,
            tesseract_config=tesseract_config,
        )
    except PdfUploadError as exc:
        logger.warning(
            "document_upload.pdf.failed filename=%s content_type=%s bytes=%d error=%s",
            filename,
            content_type,
            len(content),
            exc,
        )
        if _is_pdf_unsupported_media_error(str(exc)):
            raise UnsupportedDocumentUploadError(str(exc)) from exc
        raise DocumentUploadError(str(exc)) from exc
    upload = ParsedDocumentUpload(
        content=parsed.content,
        source_type="pdf",
        source_content_type="application/pdf",
        byte_size=parsed.byte_size,
        sha256=parsed.sha256,
        page_count=parsed.page_count,
        parser_name=parsed.parser_name,
    )
    logger.info(
        "document_upload.pdf.parsed filename=%s parser=%s pages=%s chars=%d",
        filename,
        upload.parser_name,
        upload.page_count,
        len(upload.content),
    )
    return upload


def _parse_office_file(
    *,
    filename: str,
    content_type: str | None,
    content: bytes,
) -> ParsedDocumentUpload:
    try:
        parsed = parse_uploaded_office_document(
            filename=filename,
            content_type=content_type,
            content=content,
        )
    except OfficeUploadError as exc:
        logger.warning(
            "document_upload.office.failed filename=%s content_type=%s bytes=%d error=%s",
            filename,
            content_type,
            len(content),
            exc,
        )
        if _is_office_unsupported_media_error(str(exc)):
            raise UnsupportedDocumentUploadError(str(exc)) from exc
        raise DocumentUploadError(str(exc)) from exc

    upload = ParsedDocumentUpload(
        content=parsed.content,
        source_type=parsed.source_type,
        source_content_type=parsed.source_content_type,
        byte_size=parsed.byte_size,
        sha256=parsed.sha256,
        page_count=None,
        parser_name=parsed.parser_name,
        parse_artifact=_office_parse_artifact(parsed),
    )
    logger.info(
        "document_upload.office.parsed filename=%s source_type=%s parser=%s chars=%d "
        "elements=%d warnings=%d",
        filename,
        upload.source_type,
        upload.parser_name,
        len(upload.content),
        len(parsed.elements),
        len(parsed.warnings),
    )
    return upload


def _parse_text_file(
    *,
    filename: str,
    suffix: str,
    content_type: str | None,
    content: bytes,
) -> ParsedDocumentUpload:
    normalized_content_type = _normalize_content_type(content_type)
    expected_content_type, parser_name, source_type = _text_source_metadata(suffix)
    _validate_text_content_type(
        suffix=suffix,
        content_type=normalized_content_type,
        expected_content_type=expected_content_type,
    )
    if not content:
        raise DocumentUploadError("uploaded text file is empty")
    if len(content) > MAX_TEXT_UPLOAD_BYTES:
        raise DocumentUploadError("uploaded text file exceeds the 5 MiB V1 limit")
    if b"\x00" in content:
        raise DocumentUploadError(f"{filename} appears to be binary, not UTF-8 text")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentUploadError(f"{filename} must be UTF-8 text") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise DocumentUploadError("uploaded text file has no extractable text")
    if _contains_unsafe_control_characters(normalized):
        raise DocumentUploadError(f"{filename} contains unsupported control characters")
    return ParsedDocumentUpload(
        content=normalized,
        source_type=source_type,
        source_content_type=expected_content_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        page_count=None,
        parser_name=parser_name,
    )


def _validate_upload_filename(filename: str | None) -> str:
    if filename is None or not filename.strip():
        raise DocumentUploadError("document upload requires a filename")
    stripped = filename.strip()
    if "/" in stripped or "\\" in stripped:
        raise DocumentUploadError("document filename must not contain path separators")
    suffix = _filename_suffix(stripped)
    if suffix not in _SUPPORTED_UPLOAD_SUFFIXES:
        raise UnsupportedDocumentUploadError(
            "only .pdf, .md, .markdown, .txt, .xlsx, or .pptx uploads are supported"
        )
    return stripped


def _filename_suffix(filename: str) -> str:
    if "." not in filename:
        return ""
    return f".{filename.rsplit('.', maxsplit=1)[-1].casefold()}"


def _normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", maxsplit=1)[0].strip().casefold()


def _text_source_metadata(suffix: str) -> tuple[str, str, str]:
    if suffix in {".md", ".markdown"}:
        return ("text/markdown", MARKDOWN_UPLOAD_PARSER_NAME, "markdown")
    return ("text/plain", TEXT_UPLOAD_PARSER_NAME, "text")


def _validate_text_content_type(
    *,
    suffix: str,
    content_type: str,
    expected_content_type: str,
) -> None:
    allowed = _PLAIN_TEXT_CONTENT_TYPES if suffix == ".txt" else _MARKDOWN_CONTENT_TYPES
    if content_type in allowed or content_type in _GENERIC_UPLOAD_CONTENT_TYPES:
        return
    raise UnsupportedDocumentUploadError(
        f"{suffix} uploads must use {expected_content_type} compatible content"
    )


def _contains_unsafe_control_characters(text: str) -> bool:
    return any(
        unicodedata.category(char) == "Cc" and char not in _SAFE_CONTROL_CHARACTERS for char in text
    )


def _is_pdf_unsupported_media_error(message: str) -> bool:
    return "only" in message or "not a PDF" in message


def _office_parse_artifact(parsed: ParsedOfficeDocument) -> ParsedUploadParseArtifact:
    return ParsedUploadParseArtifact(
        parser_provider=parsed.parser_provider,
        parser_name=parsed.parser_name,
        parser_version=parsed.parser_version,
        parser_mode=parsed.parser_mode,
        markdown_content=parsed.content,
        elements=parsed.elements,
        warnings=parsed.warnings,
    )


def _is_office_unsupported_media_error(message: str) -> bool:
    return (
        "only .xlsx and .pptx" in message or "must use" in message or message.startswith("only .")
    )
