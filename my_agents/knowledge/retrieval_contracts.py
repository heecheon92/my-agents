"""Compact value objects shared by document retrieval and pure resolution."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AuthorizedDocumentOption:
    """Compact display-safe document option inside an authorized retrieval scope."""

    document_id: str
    title: str
    source_filename: str | None
    knowledge_base_id: str | None
    knowledge_base_name: str | None


@dataclass(frozen=True)
class RankedAuthorizedDocumentOption(AuthorizedDocumentOption):
    """One authorization-filtered metadata candidate for document-selection HITL."""

    score: float
    matched_tokens: int
    match_confidence: Literal["high", "medium", "low"]
    match_reason_code: Literal[
        "exact_title",
        "exact_filename",
        "partial_title",
        "partial_filename",
        "metadata_overlap",
    ]
    exact: bool = False


@dataclass(frozen=True)
class FullDocumentTargetResolution:
    """One authorized full-document target, or an ambiguity/unavailable result."""

    target: AuthorizedDocumentOption | None
    option_count: int
    library_count: int = 0
    candidates: tuple[RankedAuthorizedDocumentOption, ...] = ()
    mode: Literal["selected", "single", "exact", "relevant", "unresolved"] = "unresolved"
