"""Conservative post-hoc attribution from an answer to consulted source chunks."""

from __future__ import annotations

import re
from collections.abc import Sequence

_COMMON_TOKENS = frozenset(
    {
        "and",
        "are",
        "document",
        "for",
        "from",
        "that",
        "the",
        "this",
        "with",
        "내용",
        "대한",
        "문서",
        "요약",
        "있습니다",
        "합니다",
    }
)


def answer_supported_source_indices(*, reply: str, source_texts: Sequence[str]) -> list[int]:
    """Return consulted-source indexes with conservative lexical support in the answer.

    This intentionally prefers false negatives over false positives. The caller exposes the
    complete consulted set separately, so an unmatched paraphrase must not be relabeled as a
    citation merely because it was present in model context.
    """
    normalized_reply = _normalize_text(reply)
    reply_tokens = _meaningful_tokens(reply)
    if not normalized_reply or not reply_tokens:
        return []
    reply_trigrams = _ngrams(reply_tokens, size=3)
    reply_bigrams = {gram for gram in _ngrams(reply_tokens, size=2) if sum(map(len, gram)) >= 16}
    reply_long_tokens = {token for token in reply_tokens if len(token) >= 12}
    supported: list[int] = []
    for index, source_text in enumerate(source_texts):
        anchor = _snippet_anchor(source_text)
        source_tokens = _meaningful_tokens(source_text)
        if not source_tokens:
            continue
        source_trigrams = _ngrams(source_tokens, size=3)
        source_bigrams = {
            gram for gram in _ngrams(source_tokens, size=2) if sum(map(len, gram)) >= 16
        }
        source_long_tokens = {token for token in source_tokens if len(token) >= 12}
        if (
            (anchor and anchor in normalized_reply)
            or bool(source_trigrams & reply_trigrams)
            or bool(source_bigrams & reply_bigrams)
            or bool(source_long_tokens & reply_long_tokens)
        ):
            supported.append(index)
    return supported


def _meaningful_tokens(value: str) -> list[str]:
    tokens = (
        token.strip("./:-")
        for token in re.findall(r"[\w./:-]+", value.casefold(), flags=re.UNICODE)
    )
    return [token for token in tokens if len(token) >= 2 and token not in _COMMON_TOKENS]


def _ngrams(tokens: Sequence[str], *, size: int) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return set()
    return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _snippet_anchor(snippet: str, size: int = 24) -> str:
    return _normalize_text(snippet)[:size]
