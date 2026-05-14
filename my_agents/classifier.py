"""Deterministic classify-only routing for the personal assistant graph."""

from collections.abc import Iterable

from my_agents.schemas import ChatMessage, RouteDecision, RouteLabel

_LABEL_KEYWORDS: tuple[tuple[RouteLabel, tuple[str, ...], str], ...] = (
    (
        "learning_coach",
        (
            "learn",
            "learning",
            "study",
            "practice",
            "tutorial",
            "course",
            "skill",
            "explain step",
            "step by step",
            "langgraph study",
        ),
        "This request is about study planning, practice, or skill development.",
    ),
    (
        "research_helper",
        (
            "research",
            "source",
            "sources",
            "find",
            "compare",
            "summarize",
            "paper",
            "docs",
            "documentation",
            "reference",
            "evidence",
        ),
        "This request asks for research, sources, documentation, or evidence gathering.",
    ),
    (
        "project_planner",
        (
            "plan",
            "milestone",
            "roadmap",
            "task",
            "tasks",
            "timeline",
            "scope",
            "break down",
            "next step",
            "project",
            "backend milestone",
        ),
        "This request is about planning project work, milestones, scope, or next steps.",
    ),
    (
        "career_helper",
        (
            "resume",
            "cv",
            "career",
            "recruiter",
            "headhunter",
            "interview",
            "bullet",
            "professional profile",
            "case study",
        ),
        (
            "This request is about career materials, professional presentation, "
            "or recruiter-facing wording."
        ),
    ),
)

_GENERAL_EXPLANATION = (
    "This request does not match a specific v0 route category, "
    "so it uses the general assistant route label."
)


def classify_message(message: str, history: Iterable[ChatMessage] | None = None) -> RouteDecision:
    """Classify a request into a future-agent route label.

    The classifier is intentionally local, deterministic, and credential-free. History is included
    only as extra context for classification; it does not represent persistent memory.
    """
    text = _classification_text(message, history)
    for label, keywords, explanation in _LABEL_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return RouteDecision(label=label, explanation=explanation)
    return RouteDecision(label="general_assistant", explanation=_GENERAL_EXPLANATION)


def _classification_text(message: str, history: Iterable[ChatMessage] | None) -> str:
    history_text = " ".join(item.content for item in history or ())
    return f"{message} {history_text}".casefold()
