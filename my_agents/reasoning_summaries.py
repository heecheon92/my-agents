"""Bounded, display-safe model-authored reasoning summary helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal, TypedDict

ReasoningSummaryStage = Literal["retrieval_planning", "answer_synthesis"]
ReasoningSummarySource = Literal["model_generated", "provider_reasoning_summary"]

MAX_REASONING_SUMMARY_CHARS = 500
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class ReasoningSummaryValue(TypedDict):
    """Compact graph-state representation of one public reasoning summary."""

    stage: ReasoningSummaryStage
    text: str
    source: ReasoningSummarySource


def bounded_reasoning_summary(value: object) -> str | None:
    """Normalize and bound display prose without exposing provider internals."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    if not text:
        return None
    if len(text) <= MAX_REASONING_SUMMARY_CHARS:
        return text
    return f"{text[: MAX_REASONING_SUMMARY_CHARS - 1].rstrip()}…"


def provider_reasoning_summary(response: Any) -> str | None:
    """Extract only provider-authored summary text, never raw reasoning content."""
    content = getattr(response, "content", None)
    indexed_deltas: dict[int, list[str]] = {}
    complete_parts: list[str] = []
    _collect_summary_text(content, indexed_deltas, complete_parts)
    if not indexed_deltas and not complete_parts:
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(mode="json")
            except TypeError:
                dumped = model_dump()
            _collect_summary_text(dumped, indexed_deltas, complete_parts)
    parts = ["".join(indexed_deltas[index]) for index in sorted(indexed_deltas)]
    parts.extend(complete_parts)
    return bounded_reasoning_summary(" ".join(parts))


def summaries_from_graph_state(state: dict[str, Any] | None) -> list[ReasoningSummaryValue]:
    """Build the ordered public list from two independent compact state fields."""
    if not state:
        return []
    summaries: list[ReasoningSummaryValue] = []
    planning = bounded_reasoning_summary(state.get("retrieval_planning_summary"))
    if planning:
        summaries.append(
            {
                "stage": "retrieval_planning",
                "text": planning,
                "source": "model_generated",
            }
        )
    synthesis = bounded_reasoning_summary(state.get("answer_synthesis_summary"))
    if synthesis:
        summaries.append(
            {
                "stage": "answer_synthesis",
                "text": synthesis,
                "source": "provider_reasoning_summary",
            }
        )
    return summaries


def _collect_summary_text(
    value: object,
    indexed_deltas: dict[int, list[str]],
    complete_parts: list[str],
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_summary_text(item, indexed_deltas, complete_parts)
        return
    if not isinstance(value, dict):
        return
    if value.get("type") == "summary_text" and isinstance(value.get("text"), str):
        index = value.get("index")
        if isinstance(index, int):
            indexed_deltas.setdefault(index, []).append(value["text"])
        else:
            complete_parts.append(value["text"])
        return
    if value.get("type") == "reasoning":
        _collect_summary_text(value.get("summary"), indexed_deltas, complete_parts)
        return
    for key in ("content", "output"):
        _collect_summary_text(value.get(key), indexed_deltas, complete_parts)


def joined_summary_deltas(deltas: Iterable[str]) -> str | None:
    """Bound accumulated provider deltas for final graph state."""
    return bounded_reasoning_summary("".join(deltas))
