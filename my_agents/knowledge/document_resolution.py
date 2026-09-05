"""Pure document-reference matching over already-authorized metadata.

No SQL, provider calls, or permission decisions belong here.
"""

from __future__ import annotations

import heapq
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Literal

from my_agents.knowledge.retrieval_contracts import (
    AuthorizedDocumentOption,
    RankedAuthorizedDocumentOption,
)

_DOCUMENT_REFERENCE_EXTENSION_TOKENS = {
    "csv",
    "doc",
    "docx",
    "html",
    "htm",
    "json",
    "md",
    "odt",
    "pdf",
    "ppt",
    "pptx",
    "rtf",
    "text",
    "txt",
    "xls",
    "xlsx",
    "xml",
}
_DOCUMENT_REFERENCE_IGNORED_TOKENS = {
    "all",
    "analyze",
    "and",
    "beginning",
    "check",
    "compare",
    "complete",
    "completely",
    "content",
    "cover",
    "document",
    "end",
    "entire",
    "every",
    "everything",
    "extract",
    "file",
    "for",
    "from",
    "identify",
    "list",
    "me",
    "omission",
    "omissions",
    "or",
    "please",
    "read",
    "requirement",
    "requirements",
    "review",
    "section",
    "sections",
    "source",
    "summarize",
    "summary",
    "that",
    "the",
    "this",
    "to",
    "whole",
    "without",
    "검토해줘",
    "끝까지",
    "누락",
    "내용을",
    "모든",
    "모두",
    "문서",
    "문서의",
    "문서를",
    "분석해줘",
    "빠짐없이",
    "섹션",
    "없이",
    "요구사항",
    "요약",
    "요약해줘",
    "읽고",
    "읽어줘",
    "자료",
    "전부",
    "전체를",
    "전체",
    "정리해줘",
    "처음부터",
    "파일",
}


def _rank_document_options(
    options: Iterable[AuthorizedDocumentOption],
    *,
    query: str,
    limit: int | None = None,
) -> list[RankedAuthorizedDocumentOption]:
    """Rank compact authorized metadata without reading or persisting document bodies."""
    query_tokens = _document_query_tokens(query)
    normalized_query = _normalize_document_text(query)
    query_has_filename_reference = _query_has_filename_reference(query)
    ranked = (
        candidate
        for option in options
        if (
            candidate := _rank_document_option(
                option,
                query_tokens=query_tokens,
                normalized_query=normalized_query,
                query_has_filename_reference=query_has_filename_reference,
            )
        )
        is not None
    )
    if limit is not None:
        return heapq.nsmallest(limit, ranked, key=_document_candidate_sort_key)
    return sorted(ranked, key=_document_candidate_sort_key)


def _document_candidate_sort_key(
    item: RankedAuthorizedDocumentOption,
) -> tuple[int, bool, float, str, str]:
    return (
        -item.matched_tokens,
        not item.exact,
        -item.score,
        _normalize_document_reference(item.title),
        item.document_id,
    )


def _rank_document_option(
    option: AuthorizedDocumentOption,
    *,
    query_tokens: Sequence[str],
    normalized_query: str,
    query_has_filename_reference: bool,
) -> RankedAuthorizedDocumentOption | None:
    references: list[tuple[str, str, bool]] = []
    if option.source_filename:
        references.extend(
            (
                (option.source_filename, "exact_filename", True),
                (
                    option.source_filename.rsplit(".", maxsplit=1)[0],
                    "exact_filename",
                    False,
                ),
            )
        )
    references.append((option.title, "exact_title", False))

    best: RankedAuthorizedDocumentOption | None = None
    for raw_reference, exact_reason, is_complete_filename in references:
        reference_tokens = _document_reference_tokens(raw_reference)
        if not reference_tokens:
            continue
        distinctive_reference_tokens = [
            token for token in reference_tokens if token not in _DOCUMENT_REFERENCE_IGNORED_TOKENS
        ]
        exact_filename_match = is_complete_filename and _filename_appears_in_query(
            raw_reference,
            normalized_query=normalized_query,
        )
        exact_named_reference_match = (
            not query_has_filename_reference
            and bool(distinctive_reference_tokens)
            and _named_reference_appears_in_query(
                raw_reference,
                normalized_query=normalized_query,
            )
        )
        if exact_filename_match or exact_named_reference_match:
            matched_tokens = len(reference_tokens)
        else:
            matched_tokens, _query_start, _reference_start = _longest_common_token_window(
                query_tokens,
                reference_tokens,
            )
        if matched_tokens == 0:
            continue
        covers_query_reference = not query_tokens or set(query_tokens).issubset(reference_tokens)
        exact = (exact_filename_match or exact_named_reference_match) and covers_query_reference
        overlap = len(set(query_tokens).intersection(reference_tokens))
        dice = (2 * overlap) / max(len(set(query_tokens)) + len(set(reference_tokens)), 1)
        reference_coverage = matched_tokens / len(reference_tokens)
        if exact:
            score = min(1.0, 0.9 + (0.02 * min(matched_tokens, 5)))
            confidence: Literal["high", "medium", "low"] = "high"
            reason: Literal[
                "exact_title",
                "exact_filename",
                "partial_title",
                "partial_filename",
                "metadata_overlap",
            ] = exact_reason  # type: ignore[assignment]
        else:
            query_coverage = matched_tokens / max(len(query_tokens), 1)
            score = min(
                0.89,
                (0.5 * query_coverage) + (0.2 * reference_coverage) + (0.3 * dice),
            )
            confidence = "medium" if score >= 0.6 else "low"
            if matched_tokens < 2:
                reason = "metadata_overlap"
            elif exact_reason == "exact_filename":
                reason = "partial_filename"
            else:
                reason = "partial_title"
        candidate = RankedAuthorizedDocumentOption(
            **option.__dict__,
            score=round(score, 6),
            matched_tokens=matched_tokens,
            match_confidence=confidence,
            match_reason_code=reason,
            exact=exact,
        )
        if best is None or (
            candidate.exact,
            candidate.matched_tokens,
            candidate.score,
        ) > (
            best.exact,
            best.matched_tokens,
            best.score,
        ):
            best = candidate
    return best


def _document_reference_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return [
        token
        for token in re.findall(r"[^\W_]+", normalized)
        if token not in _DOCUMENT_REFERENCE_EXTENSION_TOKENS
    ]


def _document_query_tokens(value: str) -> list[str]:
    return [
        token
        for token in _document_reference_tokens(value)
        if token not in _DOCUMENT_REFERENCE_IGNORED_TOKENS
    ]


def _normalize_document_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _query_has_filename_reference(value: str) -> bool:
    extensions = "|".join(sorted(_DOCUMENT_REFERENCE_EXTENSION_TOKENS, key=len, reverse=True))
    return bool(re.search(rf"(?i)\.(?:{extensions})(?![a-z0-9])", value))


def _filename_appears_in_query(raw_filename: str, *, normalized_query: str) -> bool:
    filename = _normalize_document_text(raw_filename)
    start = normalized_query.find(filename)
    while start >= 0:
        end = start + len(filename)
        before_ok = start == 0 or not normalized_query[start - 1].isalnum()
        after_character = normalized_query[end : end + 1]
        after_ok = not after_character or not re.match(r"[a-z0-9_]", after_character)
        if before_ok and after_ok:
            return True
        start = normalized_query.find(filename, start + 1)
    return False


def _named_reference_appears_in_query(raw_reference: str, *, normalized_query: str) -> bool:
    reference = _normalize_document_text(raw_reference)
    start = normalized_query.find(reference)
    while start >= 0:
        end = start + len(reference)
        before_ok = start == 0 or not normalized_query[start - 1].isalnum()
        after_ok = end == len(normalized_query) or not normalized_query[end].isalnum()
        if re.match(r"\.[a-z0-9_]", normalized_query[end:]):
            after_ok = False
        if before_ok and after_ok:
            return True
        start = normalized_query.find(reference, start + 1)
    return False


def _longest_common_token_window(
    left: Sequence[str],
    right: Sequence[str],
) -> tuple[int, int, int]:
    """Return length and starts for the longest contiguous shared token window."""
    if not left or not right:
        return 0, 0, 0
    previous = [0] * (len(right) + 1)
    best = (0, 0, 0)
    for left_index, left_token in enumerate(left):
        current = [0] * (len(right) + 1)
        for right_index, right_token in enumerate(right):
            if left_token != right_token:
                continue
            current[right_index + 1] = previous[right_index] + 1
            length = current[right_index + 1]
            candidate = (length, left_index - length + 1, right_index - length + 1)
            if candidate[0] > best[0]:
                best = candidate
        previous = current
    return best


def _normalize_document_reference(value: str) -> str:
    return " ".join(_document_reference_tokens(value))


def _unranked_document_option(
    candidate: RankedAuthorizedDocumentOption,
) -> AuthorizedDocumentOption:
    return AuthorizedDocumentOption(
        document_id=candidate.document_id,
        title=candidate.title,
        source_filename=candidate.source_filename,
        knowledge_base_id=candidate.knowledge_base_id,
        knowledge_base_name=candidate.knowledge_base_name,
    )
