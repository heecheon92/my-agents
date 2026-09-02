"""Versioned OpenAI File Inputs allowlist and artifact-certification registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REGISTRY_VERIFIED_AT = "2026-08-09"

_CATEGORY_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "spreadsheet": (
        ".xla",
        ".xlb",
        ".xlc",
        ".xlm",
        ".xls",
        ".xlsx",
        ".xlt",
        ".xlw",
        ".csv",
        ".tsv",
        ".iif",
    ),
    "rich_document": (".doc", ".docx", ".dot", ".odt", ".rtf"),
    "presentation": (".pot", ".ppa", ".pps", ".ppt", ".pptx", ".pwz", ".wiz"),
    "text_or_code": (
        ".asm",
        ".bat",
        ".c",
        ".cc",
        ".conf",
        ".cpp",
        ".css",
        ".cxx",
        ".def",
        ".dic",
        ".eml",
        ".h",
        ".hh",
        ".htm",
        ".html",
        ".ics",
        ".ifb",
        ".in",
        ".js",
        ".json",
        ".ksh",
        ".list",
        ".log",
        ".markdown",
        ".md",
        ".mht",
        ".mhtml",
        ".mime",
        ".mjs",
        ".nws",
        ".pl",
        ".py",
        ".rst",
        ".s",
        ".sql",
        ".srt",
        ".text",
        ".txt",
        ".vcf",
        ".vtt",
        ".xml",
    ),
}

_CATEGORY_MIME_TYPES: dict[str, tuple[str, ...]] = {
    "pdf": ("application/pdf",),
    "spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/csv",
        "application/csv",
        "text/tsv",
        "text/x-iif",
        "application/x-iif",
        "application/vnd.google-apps.spreadsheet",
    ),
    "rich_document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/rtf",
        "text/rtf",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.apple.pages",
        "application/vnd.google-apps.document",
        "application/vnd.apple.iwork",
    ),
    "presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
        "application/vnd.apple.keynote",
        "application/vnd.google-apps.presentation",
        "application/vnd.apple.iwork",
    ),
    "text_or_code": (
        "application/javascript",
        "application/typescript",
        "application/xml",
        "application/xhtml+xml",
        "application/x-mimearchive",
        "multipart/related",
        "text/xml",
        "text/x-shellscript",
        "text/x-rst",
        "text/x-makefile",
        "text/x-lisp",
        "text/x-asm",
        "text/vbscript",
        "text/css",
        "message/rfc822",
        "application/x-sql",
        "application/x-scala",
        "application/x-rust",
        "application/x-powershell",
        "text/x-diff",
        "text/x-patch",
        "application/x-patch",
        "text/plain",
        "text/markdown",
        "text/x-java",
        "text/x-script.python",
        "text/x-python",
        "text/x-c",
        "text/x-c++",
        "text/x-golang",
        "text/html",
        "text/x-php",
        "application/x-php",
        "application/x-httpd-php",
        "application/x-httpd-php-source",
        "text/x-ruby",
        "text/x-sh",
        "text/x-bash",
        "application/x-bash",
        "text/x-zsh",
        "text/x-tex",
        "text/x-csharp",
        "application/json",
        "text/x-typescript",
        "text/javascript",
        "text/x-go",
        "text/x-rust",
        "text/x-scala",
        "text/x-kotlin",
        "text/x-swift",
        "text/x-lua",
        "text/x-r",
        "text/x-R",
        "text/x-julia",
        "text/x-perl",
        "text/x-objectivec",
        "text/x-objectivec++",
        "text/x-erlang",
        "text/x-elixir",
        "text/x-haskell",
        "text/x-clojure",
        "text/x-groovy",
        "text/x-dart",
        "text/x-awk",
        "application/x-awk",
        "text/jsx",
        "text/tsx",
        "text/x-handlebars",
        "text/x-mustache",
        "text/x-ejs",
        "text/x-jinja2",
        "text/x-liquid",
        "text/x-erb",
        "text/x-twig",
        "text/x-pug",
        "text/x-jade",
        "text/x-tmpl",
        "text/x-cmake",
        "text/x-dockerfile",
        "text/x-gradle",
        "text/x-ini",
        "text/x-properties",
        "text/x-protobuf",
        "application/x-protobuf",
        "text/x-sql",
        "text/x-sass",
        "text/x-scss",
        "text/x-less",
        "text/x-hcl",
        "text/x-terraform",
        "application/x-terraform",
        "text/x-toml",
        "application/x-toml",
        "application/graphql",
        "application/x-graphql",
        "text/x-graphql",
        "application/x-ndjson",
        "application/json5",
        "application/x-json5",
        "text/x-yaml",
        "application/toml",
        "application/x-yaml",
        "application/yaml",
        "text/x-astro",
        "text/srt",
        "application/x-subrip",
        "text/x-subrip",
        "text/vtt",
        "text/x-vcard",
        "text/calendar",
    ),
}

CERTIFIED_ARTIFACT_EXTENSIONS = frozenset(
    {
        ".xlsx",
        ".csv",
        ".tsv",
        ".docx",
        ".pptx",
        ".pdf",
        ".md",
        ".markdown",
        ".html",
        ".htm",
    }
)
ARTIFACT_CANDIDATE_EXTENSIONS = frozenset(
    {
        ".xlsx",
        ".csv",
        ".tsv",
        ".docx",
        ".pptx",
        ".pdf",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".txt",
    }
)
GENERIC_UPLOAD_MIME_TYPES = frozenset({"", "application/octet-stream"})


@dataclass(frozen=True)
class DocumentFormat:
    extension: str
    category: str
    mime_types: tuple[str, ...]
    analysis_supported: bool = True
    artifact_status: str = "unavailable"


def document_format_for_filename(filename: str) -> DocumentFormat | None:
    extension = Path(filename).suffix.casefold()
    for category, extensions in _CATEGORY_EXTENSIONS.items():
        if extension in extensions:
            return DocumentFormat(
                extension=extension,
                category=category,
                mime_types=_CATEGORY_MIME_TYPES[category],
                artifact_status=(
                    "certified" if extension in CERTIFIED_ARTIFACT_EXTENSIONS else "unavailable"
                ),
            )
    return None


def content_type_matches(document_format: DocumentFormat, content_type: str | None) -> bool:
    normalized = (content_type or "").partition(";")[0].strip()
    if normalized in GENERIC_UPLOAD_MIME_TYPES:
        return True
    return normalized in document_format.mime_types


def list_document_formats() -> tuple[DocumentFormat, ...]:
    formats: list[DocumentFormat] = []
    for category, extensions in _CATEGORY_EXTENSIONS.items():
        for extension in extensions:
            formats.append(
                DocumentFormat(
                    extension=extension,
                    category=category,
                    mime_types=_CATEGORY_MIME_TYPES[category],
                    artifact_status=(
                        "certified" if extension in CERTIFIED_ARTIFACT_EXTENSIONS else "unavailable"
                    ),
                )
            )
    return tuple(formats)
