"""Deterministic evaluation helpers for agent run observability fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from my_agents.agent_runtime.citation_attribution import answer_supported_source_indices


@dataclass(frozen=True)
class EvalCheck:
    """One deterministic eval result for demo-safe regression tests."""

    name: str
    passed: bool
    details: str


def evaluate_grounded_citations(*, reply: str, citation_snippets: list[str]) -> EvalCheck:
    """Check that a cited answer visibly uses at least one returned citation snippet."""
    if not citation_snippets:
        return EvalCheck("grounded_citations", False, "no citations returned")
    grounded = bool(answer_supported_source_indices(reply=reply, source_texts=citation_snippets))
    return EvalCheck(
        "grounded_citations",
        grounded,
        "at least one citation anchor appears in the reply"
        if grounded
        else "no citation anchor appears in the reply",
    )


def evaluate_permission_leakage(
    *, reply: str, citation_snippets: list[str], forbidden_terms: list[str]
) -> EvalCheck:
    """Check that forbidden private terms are absent from reply and citations."""
    haystack = "\n".join([reply, *citation_snippets]).casefold()
    leaked = [term for term in forbidden_terms if term.casefold() in haystack]
    return EvalCheck(
        "permission_leakage",
        not leaked,
        "no forbidden terms found" if not leaked else f"forbidden terms found: {', '.join(leaked)}",
    )


def evaluate_event_redaction(
    *, event_payloads: list[dict[str, Any]], forbidden_terms: list[str]
) -> EvalCheck:
    """Check structured events do not contain raw private text or secrets."""
    serialized = json.dumps(event_payloads, sort_keys=True).casefold()
    leaked = [term for term in forbidden_terms if term.casefold() in serialized]
    return EvalCheck(
        "event_redaction",
        not leaked,
        "event payloads are redacted" if not leaked else f"event payload leak: {', '.join(leaked)}",
    )


def evaluate_event_latency_budget(
    *, event_payloads: list[dict[str, Any]], max_latency_ms: float
) -> EvalCheck:
    """Check fixture latency metrics stay within a generous deterministic budget."""
    latencies = [
        payload["latency_ms"]
        for payload in event_payloads
        if isinstance(payload.get("latency_ms"), int | float)
    ]
    if not latencies:
        return EvalCheck("event_latency_budget", False, "no latency metrics found")
    worst = max(float(value) for value in latencies)
    return EvalCheck(
        "event_latency_budget",
        worst <= max_latency_ms,
        f"max latency {worst}ms <= budget {max_latency_ms}ms"
        if worst <= max_latency_ms
        else f"max latency {worst}ms exceeds budget {max_latency_ms}ms",
    )
