"""Safe PDF upload parsing for the strict V1 ingestion path."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import pdfplumber
import pymupdf
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_UPLOAD_BYTES = 5 * 1024 * 1024
PDF_CONTENT_TYPES = frozenset({"application/pdf", "application/octet-stream"})
PYMUPDF_PARSER_NAME = "pymupdf_text_v1"
DOCLING_PARSER_NAME = "docling_markdown_v1"
TESSERACT_PARSER_NAME = "tesseract_ocr_v1"
PDF_PARSER_NAME = "pypdf_text_v2"
PDFPLUMBER_PARSER_NAME = "pdfplumber_text_tables_v1"
LEGACY_STREAM_PARSER_NAME = "deterministic_stream_fallback_v1"
PDF_PAGE_SEPARATOR = "\n\n\f\n\n"
_SAFE_PUNCTUATION = set(".,;:!?@#%&/\\-_'\"+()[]{}<>|•·$€£₩")
_BOILERPLATE_TEXT = {"adobe ucs"}
_IMAGE_PLACEHOLDER_PATTERN = re.compile(r"^<!--\s*image\s*-->$", re.IGNORECASE)
_LOCALE_TOKEN_PATTERN = re.compile(r"\b[a-z]{2}-[A-Z]{2}\b")
_MIN_MEANINGFUL_CHARS = 3
_MAX_PRIVATE_USE_RATIO = 0.01
_MAX_WHITESPACE_RATIO = 0.65
_REPEATED_LOCALE_MIN_COUNT = 8
_REPEATED_LOCALE_MAX_TOKEN_RATIO = 0.25


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


@dataclass(frozen=True)
class DoclingExtractionConfig:
    """Runtime knobs for the Docling fallback extraction layer."""

    accelerator: str = "cpu"
    ocr_enabled: bool = False
    timeout_seconds: float = 30.0
    threads: int = 4


@dataclass(frozen=True)
class TesseractOcrConfig:
    """Runtime knobs for the Tesseract OCR fallback extraction layer."""

    enabled: bool = True
    languages: str = "kor+eng"
    page_segmentation_mode: int = 6
    render_scale: float = 3.0
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class PdfClassification:
    """Lightweight routing metadata for the local PDF extraction pipeline."""

    doc_type: str
    page_count: int
    native_pages: list[int] = field(default_factory=list)
    empty_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PdfValidation:
    """Quality gate result for extracted PDF text."""

    is_valid: bool
    warnings: list[str]


@dataclass(frozen=True)
class _ExtractionAttempt:
    """Internal result from one extraction strategy."""

    parser_name: str
    pages: list[str]


def parse_uploaded_pdf(
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
    docling_config: DoclingExtractionConfig | None = None,
    tesseract_config: TesseractOcrConfig | None = None,
) -> ParsedPdf:
    """Validate, classify, extract, clean, and quality-gate a V1 PDF upload.

    The local pipeline follows the production-pipeline pattern from the PDF extraction
    docs: classify before routing, try local text/table extractors, preserve a
    deterministic fallback for simple legacy streams, clean PostgreSQL-unsafe and
    encoding artifacts, and reject low-quality output instead of persisting garbage into
    RAG.
    """
    safe_filename = _validate_pdf_filename(filename)
    _validate_pdf_content_type(content_type)
    if not content:
        raise PdfUploadError("uploaded PDF is empty")
    if len(content) > MAX_PDF_UPLOAD_BYTES:
        raise PdfUploadError("uploaded PDF exceeds the 5 MiB V1 limit")
    if not content.startswith(b"%PDF-"):
        raise PdfUploadError("uploaded file is not a PDF")

    classification = _classify_pdf(content)
    if classification.doc_type == "encrypted":
        raise PdfUploadError("encrypted PDFs are not supported")
    best_validation = PdfValidation(is_valid=False, warnings=["extraction was not attempted"])
    effective_docling_config = docling_config or DoclingExtractionConfig()
    effective_tesseract_config = tesseract_config or TesseractOcrConfig()
    for attempt in _extraction_attempts(
        content,
        classification,
        safe_filename,
        effective_docling_config,
        effective_tesseract_config,
    ):
        validation = _validate_extracted_pages(attempt.pages)
        if validation.is_valid:
            return ParsedPdf(
                content=PDF_PAGE_SEPARATOR.join(attempt.pages),
                page_count=max(classification.page_count, len(attempt.pages)),
                sha256=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                parser_name=attempt.parser_name,
            )
        best_validation = validation

    raise PdfUploadError(_unsupported_text_message(safe_filename, best_validation))


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


def _classify_pdf(content: bytes) -> PdfClassification:
    try:
        reader = PdfReader(BytesIO(content), strict=False)
    except PdfReadError, KeyError, TypeError, ValueError:
        return PdfClassification(doc_type="corrupted", page_count=0)
    if reader.is_encrypted:
        return PdfClassification(doc_type="encrypted", page_count=len(reader.pages))

    native_pages: list[int] = []
    empty_pages: list[int] = []
    warnings: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except (PdfReadError, KeyError, TypeError, ValueError) as exc:
            warnings.append(f"page {page_index} extraction failed: {type(exc).__name__}")
            text = ""
        normalized = _normalize_page_text(text)
        if _has_enough_extractable_text([normalized]):
            native_pages.append(page_index)
        else:
            empty_pages.append(page_index)

    if native_pages and empty_pages:
        doc_type = "mixed_or_low_text"
    elif native_pages:
        doc_type = "native_text"
    else:
        doc_type = "no_extractable_text"
    return PdfClassification(
        doc_type=doc_type,
        page_count=len(reader.pages),
        native_pages=native_pages,
        empty_pages=empty_pages,
        warnings=warnings,
    )


def _extraction_attempts(
    content: bytes,
    classification: PdfClassification,
    filename: str,
    docling_config: DoclingExtractionConfig,
    tesseract_config: TesseractOcrConfig,
) -> Iterator[_ExtractionAttempt]:
    # PyMuPDF is the fast primary local extractor. It is tried before the older
    # pypdf/pdfplumber compatibility layer so normal text PDFs do not pay Docling's
    # heavier model startup cost.
    yield _ExtractionAttempt(PYMUPDF_PARSER_NAME, _extract_page_texts_with_pymupdf(content))

    # Docling is the structured primary fallback for PDFs where fast text extraction
    # is empty or fails the quality gate. It can produce RAG-friendly Markdown and
    # table structure while still staying local.
    yield _ExtractionAttempt(
        DOCLING_PARSER_NAME,
        _extract_page_texts_with_docling(filename, content, docling_config),
    )

    # Tesseract is the OCR fallback for image-heavy PDFs where text extractors only
    # return image placeholders or bullet artifacts.
    yield _ExtractionAttempt(
        TESSERACT_PARSER_NAME,
        _extract_page_texts_with_tesseract(content, tesseract_config),
    )

    # Keep the previous lightweight extractors as compatibility fallbacks.
    if classification.doc_type in {"native_text", "mixed_or_low_text"}:
        yield _ExtractionAttempt(PDF_PARSER_NAME, _extract_page_texts_with_pypdf(content))
    yield _ExtractionAttempt(PDFPLUMBER_PARSER_NAME, _extract_page_texts_with_pdfplumber(content))

    # Keep the deterministic legacy fallback so tiny fixture PDFs and simple literal streams
    # remain supported without pretending it is a robust production extractor.
    yield _ExtractionAttempt(LEGACY_STREAM_PARSER_NAME, _legacy_extract_page_texts(content))


def _extract_page_texts_with_pymupdf(content: bytes) -> list[str]:
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception:  # noqa: BLE001 - malformed PDFs can fail in MuPDF-specific ways.
        return []
    try:
        if document.needs_pass:
            raise PdfUploadError("encrypted PDFs are not supported")
        pages: list[str] = []
        for page in document:
            text = _normalize_page_text(page.get_text("text", sort=True) or "")
            if text:
                pages.append(text)
        return pages
    finally:
        document.close()


def _extract_page_texts_with_docling(
    filename: str,
    content: bytes,
    config: DoclingExtractionConfig,
) -> list[str]:
    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except Exception:  # noqa: BLE001 - optional heavy dependency should fail closed.
        return []

    try:
        source = DocumentStream(name=filename, stream=BytesIO(content))
        pipeline_options = PdfPipelineOptions()
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=config.threads,
            device=_docling_accelerator_device(config.accelerator, AcceleratorDevice),
        )
        pipeline_options.do_ocr = config.ocr_enabled
        pipeline_options.document_timeout = config.timeout_seconds
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        result = converter.convert(
            source,
            raises_on_error=False,
            max_file_size=MAX_PDF_UPLOAD_BYTES,
        )
        document = getattr(result, "document", None)
        if document is None:
            return []
        page_count = document.num_pages()
        pages = [
            _normalize_page_text(document.export_to_markdown(page_no=page_no))
            for page_no in range(1, page_count + 1)
        ]
        pages = [page for page in pages if page]
        if pages:
            return pages
        markdown = _normalize_page_text(document.export_to_markdown())
        return [markdown] if markdown else []
    except Exception:  # noqa: BLE001 - conversion failures should fall through to fallbacks.
        return []


def _extract_page_texts_with_tesseract(
    content: bytes,
    config: TesseractOcrConfig,
) -> list[str]:
    if not config.enabled:
        return []
    if shutil.which("tesseract") is None:
        return []

    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception:  # noqa: BLE001 - malformed PDFs can fail in MuPDF-specific ways.
        return []

    runtime_root = Path.cwd() / "tmp" / "ocr-runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="tesseract-", dir=runtime_root) as temp_dir:
            temp_path = Path(temp_dir)
            pages: list[str] = []
            for page_index, page in enumerate(document, start=1):
                image_path = temp_path / f"page-{page_index}.jpg"
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(config.render_scale, config.render_scale),
                    alpha=False,
                )
                pixmap.save(image_path)
                text = _run_tesseract_page_ocr(image_path, config, cwd=temp_path)
                normalized = _normalize_page_text(text)
                if normalized:
                    pages.append(normalized)
            return pages
    except OSError:
        return []
    finally:
        document.close()


def _run_tesseract_page_ocr(
    image_path: Path,
    config: TesseractOcrConfig,
    *,
    cwd: Path,
) -> str:
    try:
        result = subprocess.run(
            [
                "tesseract",
                image_path.name,
                "stdout",
                "-l",
                config.languages,
                "--psm",
                str(config.page_segmentation_mode),
                "quiet",
            ],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
        )
    except OSError, subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _docling_accelerator_device(accelerator: str, accelerator_device: Any) -> Any:
    """Map settings strings to Docling's AcceleratorDevice enum members."""
    normalized = accelerator.casefold()
    return {
        "auto": accelerator_device.AUTO,
        "cpu": accelerator_device.CPU,
        "cuda": accelerator_device.CUDA,
        "mps": accelerator_device.MPS,
        "xpu": accelerator_device.XPU,
    }.get(normalized, accelerator_device.CPU)


def _extract_page_texts_with_pypdf(content: bytes) -> list[str]:
    try:
        reader = PdfReader(BytesIO(content), strict=False)
    except PdfReadError, KeyError, TypeError, ValueError:
        return []
    if reader.is_encrypted:
        raise PdfUploadError("encrypted PDFs are not supported")
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except PdfReadError, KeyError, TypeError, ValueError:
            text = ""
        normalized = _normalize_page_text(text)
        if normalized:
            pages.append(normalized)
    return pages


def _extract_page_texts_with_pdfplumber(content: bytes) -> list[str]:
    pages: list[str] = []
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            for page in pdf.pages:
                parts: list[str] = []
                text = _normalize_page_text(page.extract_text() or "")
                if text:
                    parts.append(text)
                parts.extend(_markdown_tables_from_pdfplumber_page(page))
                page_text = _normalize_page_text("\n\n".join(parts))
                if page_text:
                    pages.append(page_text)
    except Exception:  # noqa: BLE001 - PDFs can fail inside pdfminer/pdfplumber in many ways.
        return []
    return pages


def _markdown_tables_from_pdfplumber_page(page: Any) -> list[str]:
    tables: list[str] = []
    try:
        raw_tables = page.extract_tables(
            {
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "edge_min_length": 3,
                "min_words_vertical": 3,
                "min_words_horizontal": 1,
            }
        )
    except Exception:  # noqa: BLE001 - table detection should not fail the text path.
        return tables

    for table in raw_tables:
        markdown = _table_to_markdown(table)
        if markdown:
            tables.append(markdown)
    return tables


def _table_to_markdown(table: list[list[str | None]]) -> str:
    rows = [[_normalize_table_cell(cell) for cell in row] for row in table if row]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows or not rows[0]:
        return ""

    width = max(len(row) for row in rows)
    padded_rows = [row + [""] * (width - len(row)) for row in rows]
    header, *body = padded_rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _normalize_table_cell(value: str | None) -> str:
    return _normalize_text(value or "").replace("|", "\\|")


def _legacy_extract_page_texts(content: bytes) -> list[str]:
    pages: list[str] = []
    for stream in _streams_from_blob(content):
        text = _extract_literal_text(stream)
        if text:
            pages.append(text)
    return pages


def _streams_from_blob(content: bytes) -> list[bytes]:
    streams: list[bytes] = []
    for match in re.finditer(
        rb"(?P<dictionary><<.*?>>)\s*stream\r?\n(?P<stream>.*?)\r?\nendstream",
        content,
        flags=re.DOTALL,
    ):
        dictionary = match.group("dictionary")
        stream = match.group("stream").strip(b"\r\n")
        decoded = _decode_stream(dictionary=dictionary, stream=stream)
        if decoded is not None:
            streams.append(decoded)
    return streams


def _decode_stream(*, dictionary: bytes, stream: bytes) -> bytes | None:
    if b"/Filter" not in dictionary:
        return stream
    if b"/FlateDecode" not in dictionary:
        return None
    try:
        return zlib.decompress(stream)
    except zlib.error:
        return None


def _extract_literal_text(stream: bytes) -> str:
    source = stream.decode("latin-1", errors="ignore")
    literals = [
        _decode_pdf_literal(match.group(1)) for match in _literal_pattern().finditer(source)
    ]
    text = " ".join(part for part in (_normalize_text(value) for value in literals) if part)
    text = _normalize_page_text(text)
    if not _looks_like_human_text(text):
        return ""
    return text


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
    without_unsafe = _remove_unsafe_control_characters(value)
    return re.sub(r"\s+", " ", without_unsafe).strip()


def _normalize_page_text(value: str) -> str:
    lines = [_normalize_text(line) for line in value.splitlines()]
    collapsed = "\n".join(line for line in lines if line)
    return _clean_extraction_artifacts(collapsed)


def _clean_extraction_artifacts(value: str) -> str:
    without_boilerplate = _remove_boilerplate_text(value)
    without_docling_placeholders = _remove_structural_placeholder_lines(without_boilerplate)
    without_locale_artifacts = _remove_repeated_locale_artifacts(without_docling_placeholders)
    return _remove_structural_placeholder_lines(_remove_boilerplate_text(without_locale_artifacts))


def _remove_unsafe_control_characters(value: str) -> str:
    return "".join(
        " " if char == "\x00" else char
        for char in value
        if char == "\x00" or ord(char) >= 32 or char in "\n\r\t\f"
    )


def _remove_boilerplate_text(value: str) -> str:
    lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.casefold() in _BOILERPLATE_TEXT:
            continue
        lines.append(stripped)
    return "\n".join(line for line in lines if line).strip()


def _remove_structural_placeholder_lines(value: str) -> str:
    lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if _is_structural_placeholder_line(stripped):
            continue
        lines.append(stripped)
    return "\n".join(line for line in lines if line).strip()


def _is_structural_placeholder_line(value: str) -> bool:
    if not value:
        return True
    if _IMAGE_PLACEHOLDER_PATTERN.fullmatch(value):
        return True
    without_list_marker = value.lstrip("-+*•▪· ").strip()
    return bool(value) and not any(char.isalnum() for char in without_list_marker)


def _remove_repeated_locale_artifacts(value: str) -> str:
    tokens = value.split()
    if not tokens:
        return value.strip()
    locale_counts: dict[str, int] = {}
    for token in tokens:
        stripped = _strip_token_punctuation(token)
        if _LOCALE_TOKEN_PATTERN.fullmatch(stripped):
            locale_counts[stripped] = locale_counts.get(stripped, 0) + 1
    repeated_locales = {
        locale
        for locale, count in locale_counts.items()
        if count >= _REPEATED_LOCALE_MIN_COUNT
        and count / len(tokens) >= _REPEATED_LOCALE_MAX_TOKEN_RATIO
    }
    if not repeated_locales:
        return value.strip()
    cleaned_tokens = [
        token for token in tokens if _strip_token_punctuation(token) not in repeated_locales
    ]
    return " ".join(cleaned_tokens).strip()


def _strip_token_punctuation(token: str) -> str:
    return token.strip(".,;:()[]{}")


def _validate_extracted_pages(pages: list[str]) -> PdfValidation:
    text = "\n".join(pages)
    warnings: list[str] = []
    if not text.strip():
        warnings.append("extraction produced empty output")
    if "\x00" in text:
        warnings.append("extraction output contains PostgreSQL-unsafe NUL bytes")
    if not _has_enough_extractable_text(pages):
        warnings.append("extraction output has too little meaningful text")

    char_count = len(text)
    if char_count:
        private_use_ratio = sum(1 for char in text if 0xE000 <= ord(char) <= 0xF8FF) / char_count
        if private_use_ratio > _MAX_PRIVATE_USE_RATIO:
            warnings.append("extraction output contains likely font-encoding garbage")
        whitespace_ratio = sum(1 for char in text if char.isspace()) / char_count
        if whitespace_ratio > _MAX_WHITESPACE_RATIO:
            warnings.append("extraction output contains excessive whitespace")
    if _has_repeated_locale_artifact(text):
        warnings.append("extraction output is dominated by repeated locale metadata")
    if re.search(r"(.)\1{40,}", text):
        warnings.append("extraction output contains repeated character artifacts")
    return PdfValidation(is_valid=not warnings, warnings=warnings)


def _has_repeated_locale_artifact(value: str) -> bool:
    tokens = value.split()
    if not tokens:
        return False
    for match in set(_LOCALE_TOKEN_PATTERN.findall(value)):
        count = sum(1 for token in tokens if _strip_token_punctuation(token) == match)
        if (
            count >= _REPEATED_LOCALE_MIN_COUNT
            and count / len(tokens) >= _REPEATED_LOCALE_MAX_TOKEN_RATIO
        ):
            return True
    return False


def _unsupported_text_message(filename: str, validation: PdfValidation) -> str:
    reason = "; ".join(validation.warnings[:2]) or "unsupported extraction output"
    return (
        f"{filename} does not contain extractable text cleanly supported "
        f"by the PDF parser: {reason}"
    )


def _has_enough_extractable_text(pages: list[str]) -> bool:
    meaningful_text = "\n".join(_clean_extraction_artifacts(page) for page in pages)
    meaningful_chars = sum(1 for char in meaningful_text if char.isalnum())
    return meaningful_chars >= _MIN_MEANINGFUL_CHARS


def _looks_like_human_text(value: str) -> bool:
    if not value:
        return False
    meaningful = [char for char in value if not char.isspace()]
    if len(meaningful) < _MIN_MEANINGFUL_CHARS:
        return False
    safe_chars = sum(1 for char in meaningful if _is_safe_text_char(char))
    return safe_chars / len(meaningful) >= 0.7


def _is_safe_text_char(char: str) -> bool:
    if char.isalnum():
        return True
    if "가" <= char <= "힣":
        return True
    return char in _SAFE_PUNCTUATION
