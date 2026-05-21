"""Safe document-upload dispatch for supported ingestion source files."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

from my_agents.knowledge.pdf_uploads import (
    MAX_PDF_UPLOAD_BYTES,
    PdfUploadError,
    parse_uploaded_pdf,
)

MAX_TEXT_UPLOAD_BYTES = MAX_PDF_UPLOAD_BYTES
TEXT_UPLOAD_PARSER_NAME = "utf8_text_v1"
MARKDOWN_UPLOAD_PARSER_NAME = "utf8_markdown_v1"
_SUPPORTED_UPLOAD_SUFFIXES = frozenset({".pdf", ".md", ".markdown", ".txt"})
_TEXT_UPLOAD_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_GENERIC_UPLOAD_CONTENT_TYPES = frozenset({"", "application/octet-stream"})
_MARKDOWN_CONTENT_TYPES = frozenset({"text/markdown", "text/x-markdown", "text/plain"})
_PLAIN_TEXT_CONTENT_TYPES = frozenset({"text/plain"})
_SAFE_CONTROL_CHARACTERS = frozenset({"\t", "\n", "\r", "\f"})


class DocumentUploadError(ValueError):
    """Raised when an uploaded document is unsafe or unsupported."""


class UnsupportedDocumentUploadError(DocumentUploadError):
    """Raised when the upload file type is not part of the supported V1 set."""


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


def parse_uploaded_document(
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> ParsedDocumentUpload:
    """Parse a supported upload into normalized text and source metadata."""
    safe_filename = _validate_upload_filename(filename)
    suffix = _filename_suffix(safe_filename)
    if suffix == ".pdf":
        return _parse_pdf(filename=safe_filename, content_type=content_type, content=content)
    if suffix in _TEXT_UPLOAD_SUFFIXES:
        return _parse_text_file(
            filename=safe_filename,
            suffix=suffix,
            content_type=content_type,
            content=content,
        )
    raise UnsupportedDocumentUploadError("only .pdf, .md, .markdown, or .txt uploads are supported")


def _parse_pdf(
    *,
    filename: str,
    content_type: str | None,
    content: bytes,
) -> ParsedDocumentUpload:
    try:
        parsed = parse_uploaded_pdf(filename=filename, content_type=content_type, content=content)
    except PdfUploadError as exc:
        if _is_pdf_unsupported_media_error(str(exc)):
            raise UnsupportedDocumentUploadError(str(exc)) from exc
        raise DocumentUploadError(str(exc)) from exc
    return ParsedDocumentUpload(
        content=parsed.content,
        source_type="pdf",
        source_content_type="application/pdf",
        byte_size=parsed.byte_size,
        sha256=parsed.sha256,
        page_count=parsed.page_count,
        parser_name=parsed.parser_name,
    )


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
            "only .pdf, .md, .markdown, or .txt uploads are supported"
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
