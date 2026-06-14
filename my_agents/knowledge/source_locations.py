"""Helpers for parser-derived source-location provenance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParseArtifactElement:
    """Offset-addressable source-location metadata from a parse artifact."""

    kind: str
    markdown_start: int
    markdown_end: int
    source_location: dict[str, Any]


def parse_source_location_json(raw_json: str | None) -> dict[str, object] | None:
    """Return a source-location dict from stored JSON, ignoring malformed values."""
    if raw_json is None:
        return None
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError, TypeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def source_location_json_for_offsets(
    *,
    elements_json: str | None,
    start_offset: int,
    end_offset: int,
) -> str | None:
    """Return the best source-location JSON for a Markdown chunk offset range."""
    elements = _parse_artifact_elements(elements_json)
    if not elements:
        return None
    best = max(
        elements,
        key=lambda element: (
            _offset_overlap(start_offset, end_offset, element.markdown_start, element.markdown_end),
            -max(0, element.markdown_end - element.markdown_start),
        ),
    )
    if _offset_overlap(start_offset, end_offset, best.markdown_start, best.markdown_end) <= 0:
        return None
    return json.dumps(best.source_location, ensure_ascii=False, sort_keys=True)


def _parse_artifact_elements(raw_json: str | None) -> list[ParseArtifactElement]:
    if raw_json is None:
        return []
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError, TypeError:
        return []
    if not isinstance(parsed, list):
        return []
    elements: list[ParseArtifactElement] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        markdown_start = item.get("markdown_start")
        markdown_end = item.get("markdown_end")
        source_location = item.get("source_location")
        if (
            not isinstance(kind, str)
            or not isinstance(markdown_start, int)
            or not isinstance(markdown_end, int)
            or markdown_end < markdown_start
            or not isinstance(source_location, dict)
        ):
            continue
        elements.append(
            ParseArtifactElement(
                kind=kind,
                markdown_start=markdown_start,
                markdown_end=markdown_end,
                source_location=source_location,
            )
        )
    return elements


def _offset_overlap(start: int, end: int, element_start: int, element_end: int) -> int:
    if end <= start:
        return 1 if element_start <= start <= element_end else 0
    return max(0, min(end, element_end) - max(start, element_start))
