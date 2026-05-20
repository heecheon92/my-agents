"""Safe, deterministic PDF upload parsing for the strict V1 ingestion path."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_PDF_UPLOAD_BYTES = 5 * 1024 * 1024
PDF_CONTENT_TYPES = frozenset({"application/pdf", "application/octet-stream"})
PDF_PARSER_NAME = "deterministic_pdf_text_v1"
PDF_PAGE_SEPARATOR = "\n\n\f\n\n"


class PdfUploadError(ValueError):
    """Raised when an uploaded PDF is unsafe or unsupported by the V1 parser."""


@dataclass(frozen=True)
class ParsedPdf:
    """Text and metadata extracted from one uploaded PDF."""

    content: str
    page_count: int
    sha256: str
    byte_size: int
    parser_name: str = PDF_PARSER_NAME


def parse_uploaded_pdf(
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> ParsedPdf:
    """Validate and extract deterministic text from a V1 PDF upload.

    This intentionally small parser supports text-based PDFs that expose literal text
    operators in page streams. Scanned/encrypted/compressed PDFs are rejected instead
    of being accepted with misleading empty provenance.
    """
    safe_filename = _validate_pdf_filename(filename)
    _validate_pdf_content_type(content_type)
    if not content:
        raise PdfUploadError("uploaded PDF is empty")
    if len(content) > MAX_PDF_UPLOAD_BYTES:
        raise PdfUploadError("uploaded PDF exceeds the 5 MiB V1 limit")
    if not content.startswith(b"%PDF-"):
        raise PdfUploadError("uploaded file is not a PDF")

    pages = _extract_page_texts(content)
    if not pages:
        raise PdfUploadError(
            f"{safe_filename} does not contain extractable text supported by the V1 parser"
        )
    return ParsedPdf(
        content=PDF_PAGE_SEPARATOR.join(pages),
        page_count=len(pages),
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
    )


def _validate_pdf_filename(filename: str | None) -> str:
    if filename is None or not filename.strip():
        raise PdfUploadError("PDF upload requires a filename")
    stripped = filename.strip()
    if "/" in stripped or "\\" in stripped:
        raise PdfUploadError("PDF filename must not contain path separators")
    if not stripped.casefold().endswith(".pdf"):
        raise PdfUploadError("only .pdf uploads are supported")
    return stripped


def _validate_pdf_content_type(content_type: str | None) -> None:
    normalized = (content_type or "").split(";", maxsplit=1)[0].strip().casefold()
    if normalized not in PDF_CONTENT_TYPES:
        raise PdfUploadError("only application/pdf uploads are supported")


def _extract_page_texts(content: bytes) -> list[str]:
    pages: list[str] = []
    for stream in _all_streams(content):
        text = _extract_literal_text(stream)
        if text:
            pages.append(text)
    return pages


def _all_streams(content: bytes) -> list[bytes]:
    return _streams_from_blob(content)


def _streams_from_blob(content: bytes) -> list[bytes]:
    return [
        match.group("stream").strip(b"\r\n")
        for match in re.finditer(
            rb"stream\r?\n(?P<stream>.*?)\r?\nendstream",
            content,
            flags=re.DOTALL,
        )
    ]


def _extract_literal_text(stream: bytes) -> str:
    source = stream.decode("latin-1", errors="ignore")
    literals = [
        _decode_pdf_literal(match.group(1)) for match in _literal_pattern().finditer(source)
    ]
    return " ".join(part for part in (_normalize_text(value) for value in literals) if part)


def _literal_pattern() -> re.Pattern[str]:
    return re.compile(r"\(((?:\\.|[^\\)])*)\)")


def _decode_pdf_literal(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\":
            output.append(char)
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        if escaped in {"\\", "(", ")"}:
            output.append(escaped)
        elif escaped == "n":
            output.append("\n")
        elif escaped == "r":
            output.append("\r")
        elif escaped == "t":
            output.append("\t")
        elif escaped in {"b", "f"}:
            output.append(" ")
        elif escaped in "\n\r":
            pass
        elif escaped in "01234567":
            octal = escaped
            while index + 1 < len(value) and len(octal) < 3 and value[index + 1] in "01234567":
                index += 1
                octal += value[index]
            output.append(chr(int(octal, 8)))
        elif escaped.isdigit():
            output.append(escaped)
        else:
            output.append(escaped)
        index += 1
    return "".join(output)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
