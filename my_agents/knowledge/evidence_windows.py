"""Select bounded, contiguous source excerpts without rewriting evidence."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass

_STOP_WORDS = frozenset(
    {
        "the",
        "what",
        "which",
        "does",
        "this",
        "that",
        "with",
        "from",
        "are",
        "and",
        "for",
        "how",
        "value",
        "use",
        "무엇",
        "무엇인가요",
        "알려줘",
        "값은",
    }
)


@dataclass(frozen=True)
class EvidenceWindow:
    text: str
    start: int
    end: int
    truncated: bool


def select_evidence_window(text: str, query: str, *, max_bytes: int) -> EvidenceWindow:
    """Prefer query-bearing regions to a blind prefix under the same UTF-8 byte cap.

    This is a lexical selection heuristic, not a promise of full-document coverage.
    Original offsets and text are preserved. Tie breaks favor the earliest region;
    queries with no useful matches retain the bounded prefix behavior.
    """
    if max_bytes < 1:
        return EvidenceWindow("", 0, 0, bool(text))
    if len(text.encode("utf-8")) <= max_bytes:
        return EvidenceWindow(text, 0, len(text), False)
    byte_offsets = [0]
    for char in text:
        byte_offsets.append(byte_offsets[-1] + len(char.encode("utf-8")))
    terms = sorted(
        {
            token.casefold()
            for token in re.findall(r"\w+", query)
            if len(token) >= 2 and token.casefold() not in _STOP_WORDS
        },
        key=lambda term: (-len(term), term),
    )[:32]
    matches = []
    anchors = {0}
    for term in terms:
        hits = list(re.finditer(re.escape(term), text, re.IGNORECASE))
        matches.extend((hit.start(), hit.end(), term) for hit in hits)
        # Bound candidate evaluation while retaining beginning, tail, and interior anchors
        # for each term, so a repeated generic word cannot crowd out a rare identifier.
        if hits:
            for i in range(min(8, len(hits))):
                hit = hits[round(i * (len(hits) - 1) / max(1, min(8, len(hits)) - 1))]
                anchors.add(
                    bisect_left(byte_offsets, max(0, byte_offsets[hit.start()] - max_bytes // 3))
                )
    frequencies = Counter(term for _, _, term in matches)
    best = (0.0, 0, 0, min(len(text), bisect_right(byte_offsets, max_bytes) - 1))
    for start in sorted(anchors):
        end = bisect_right(byte_offsets, byte_offsets[start] + max_bytes) - 1
        present = {term for begin, finish, term in matches if start <= begin and finish <= end}
        score = sum(1 / frequencies[term] for term in present)
        candidate = (score, len(present), -start, end)
        if candidate[:3] > best[:3]:
            best = candidate
    start, end = -best[2], best[3]
    return EvidenceWindow(text[start:end], start, end, start != 0 or end != len(text))


def cross_encoder_windows(text: str, query: str, tokenizer, *, max_length: int) -> list[str]:
    """Cover a chunk with overlapping windows measured by that cross-encoder's tokenizer.

    Refuse unbounded work rather than silently score only a prefix. The caller can retain
    the fused shortlist if complete scoring cannot fit this operational bound.
    """
    if not isinstance(max_length, int) or not 1 <= max_length <= 100_000:
        raise ValueError("Cross-encoder token limit is unavailable")
    query_ids = tokenizer.encode(query, add_special_tokens=False)
    available = max_length - len(query_ids) - tokenizer.num_special_tokens_to_add(pair=True)
    if available < 1:
        raise ValueError("Query leaves no cross-encoder evidence budget")
    ids = tokenizer.encode(text, add_special_tokens=False)
    if not ids:
        return [""]
    overlap = min(32, available // 4)
    windows, start = [], 0
    while start < len(ids):
        if len(windows) >= 16:
            raise ValueError("Cross-encoder evidence exceeds the window bound")
        end = min(len(ids), start + available)
        while end > start:
            window = tokenizer.decode(
                ids[start:end], skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            size = len(tokenizer.encode(query, window, add_special_tokens=True))
            if size <= max_length:
                break
            end -= max(1, size - max_length)
        else:
            raise ValueError("Cross-encoder evidence cannot fit its token budget")
        windows.append(window)
        if end == len(ids):
            break
        start = max(start + 1, end - overlap)
    return windows
