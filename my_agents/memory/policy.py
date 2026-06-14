"""Deterministic policy gates for long-term memory writes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from my_agents.memory.models import MemoryCategory, MemoryProvenanceType, MemorySensitivity


class MemoryWriteMode(StrEnum):
    """Supported memory write paths."""

    EXPLICIT = "explicit"
    SUGGEST_CONFIRM = "suggest_confirm"
    AUTO_STORE = "auto_store"


class MemoryPolicyDecision(StrEnum):
    """Outcome of deterministic memory write policy evaluation."""

    ALLOW = "allow"
    REJECT_SENSITIVE = "reject_sensitive"
    REJECT_UNSUPPORTED_CATEGORY = "reject_unsupported_category"
    REJECT_MISSING_DOCUMENT_PROVENANCE = "reject_missing_document_provenance"
    REJECT_UNSUPPORTED_CONTENT = "reject_unsupported_content"


@dataclass(frozen=True)
class MemoryWritePolicyResult:
    """Policy decision with normalized category/provenance/sensitivity."""

    decision: MemoryPolicyDecision
    category: MemoryCategory | None
    provenance_type: MemoryProvenanceType | None
    sensitivity: MemorySensitivity
    reason: str


SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(ssn|social security number)\b",
        r"\b(password|passcode|api key|secret key|access token|refresh token)\b",
        r"\b\d{3}-\d{2}-\d{4}\b",
        r"\b(?:\d[ -]*?){13,19}\b",
        r"\b(diagnosed with|medical record|prescription|mental health)\b",
        r"\b(religion|political affiliation|union membership)\b",
    )
)

PREFERENCE_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(prefer|prefers|preference|like|likes|want|wants|usually|always|default)\b",
        r"\b(use|answer|respond|explain|format|tone|language|call me)\b",
        r"(선호|좋아|답변|한국어|간결|자세히)",
    )
)


AUTO_STORE_CATEGORIES = frozenset(
    {
        MemoryCategory.STABLE_PREFERENCE,
        MemoryCategory.PROJECT_CONTEXT,
        MemoryCategory.PERSONAL_FACT,
        MemoryCategory.DOCUMENT_DERIVED_FACT,
    }
)


def evaluate_memory_write(
    *,
    content: str,
    category: MemoryCategory | str,
    mode: MemoryWriteMode | str,
    source_document_id: str | None = None,
) -> MemoryWritePolicyResult:
    """Apply deterministic category, sensitivity, and provenance gates."""
    normalized_category = MemoryCategory(str(category))
    normalized_mode = MemoryWriteMode(str(mode))
    sensitivity = detect_memory_sensitivity(content)
    provenance_type = provenance_for_write_mode(normalized_mode, normalized_category)

    if sensitivity == MemorySensitivity.SENSITIVE:
        return MemoryWritePolicyResult(
            decision=MemoryPolicyDecision.REJECT_SENSITIVE,
            category=normalized_category,
            provenance_type=provenance_type,
            sensitivity=sensitivity,
            reason="sensitive facts are not stored in long-term memory",
        )
    if (
        normalized_mode == MemoryWriteMode.AUTO_STORE
        and normalized_category not in AUTO_STORE_CATEGORIES
    ):
        return MemoryWritePolicyResult(
            decision=MemoryPolicyDecision.REJECT_UNSUPPORTED_CATEGORY,
            category=normalized_category,
            provenance_type=provenance_type,
            sensitivity=sensitivity,
            reason="category is not allowed for auto-store",
        )
    if normalized_category == MemoryCategory.STABLE_PREFERENCE and not has_preference_shape(
        content
    ):
        return MemoryWritePolicyResult(
            decision=MemoryPolicyDecision.REJECT_UNSUPPORTED_CONTENT,
            category=normalized_category,
            provenance_type=provenance_type,
            sensitivity=sensitivity,
            reason="stable preference memory must describe a durable user preference",
        )
    if normalized_category == MemoryCategory.DOCUMENT_DERIVED_FACT and not source_document_id:
        return MemoryWritePolicyResult(
            decision=MemoryPolicyDecision.REJECT_MISSING_DOCUMENT_PROVENANCE,
            category=normalized_category,
            provenance_type=provenance_type,
            sensitivity=sensitivity,
            reason="document-derived memory requires source_document_id",
        )
    return MemoryWritePolicyResult(
        decision=MemoryPolicyDecision.ALLOW,
        category=normalized_category,
        provenance_type=provenance_type,
        sensitivity=sensitivity,
        reason="allowed",
    )


def detect_memory_sensitivity(content: str) -> MemorySensitivity:
    """Return a conservative sensitivity label for memory policy decisions."""
    if any(pattern.search(content) for pattern in SENSITIVE_PATTERNS):
        return MemorySensitivity.SENSITIVE
    return MemorySensitivity.NON_SENSITIVE


def has_preference_shape(content: str) -> bool:
    """Return whether content looks like a durable preference, not an arbitrary fact."""
    return any(pattern.search(content) for pattern in PREFERENCE_SHAPE_PATTERNS)


def provenance_for_write_mode(
    mode: MemoryWriteMode,
    category: MemoryCategory,
) -> MemoryProvenanceType:
    if category == MemoryCategory.DOCUMENT_DERIVED_FACT:
        return MemoryProvenanceType.DOCUMENT_DERIVED
    if mode == MemoryWriteMode.EXPLICIT:
        return MemoryProvenanceType.EXPLICIT_USER
    if mode == MemoryWriteMode.SUGGEST_CONFIRM:
        return MemoryProvenanceType.ASSISTANT_SUGGESTED
    return MemoryProvenanceType.AUTO_STORED
