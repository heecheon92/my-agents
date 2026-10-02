"""Narrow OpenRouter Jev boundary for bounded choices/scores, never authorization."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

import httpx
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

from my_agents.settings import Settings

logger = logging.getLogger(__name__)
JEV_MODEL = "typesafe/jev-1.13"
JEV_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"


class ChoiceAnswer(BaseModel):
    """Validate provider output before mapping it into application state."""

    model_config = ConfigDict(strict=True)
    type: str
    choice: str
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ChoiceDecider(Protocol):
    def choose(
        self, *, state: Mapping[str, object], instructions: str, criteria: Mapping[str, str]
    ) -> str | None:
        """Return an allowed label, or None to request local fallback."""
        ...


class ScoreAnswer(BaseModel):
    """An ordinal rubric score, not a retrieval similarity or authorization signal."""

    model_config = ConfigDict(strict=True)
    type: Literal["score"]
    score: float = Field(ge=0, allow_inf_nan=False)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ScoreDecider(Protocol):
    def score(
        self,
        *,
        state: Mapping[str, object],
        questions: Mapping[str, Mapping[str, object]],
        timeout_seconds: float,
    ) -> dict[str, float] | None:
        """Score every named question, or return None for whole-pass fallback."""
        ...


class JevScoreClient:
    """The existing Decisions endpoint with strictly validated score answers."""

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    def score(
        self,
        *,
        state: Mapping[str, object],
        questions: Mapping[str, Mapping[str, object]],
        timeout_seconds: float,
    ) -> dict[str, float] | None:
        key = self._settings.openrouter_api_key
        if not key or not key.get_secret_value().strip():
            return None
        try:
            with httpx.Client(timeout=timeout_seconds, transport=self._transport) as client:
                response = client.post(
                    JEV_ENDPOINT,
                    headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                    json={"model": JEV_MODEL, "state": dict(state), "questions": dict(questions)},
                )
                response.raise_for_status()
                answers = response.json()["answers"]
                if not isinstance(answers, dict) or set(answers) != set(questions):
                    raise ValueError("Score answers do not match requested candidates")
                scores = {}
                for question_id, question in questions.items():
                    answer = ScoreAnswer.model_validate(answers[question_id])
                    if answer.score > len(question["criteria"]) - 1:
                        raise ValueError("Score exceeds the rubric")
                    scores[question_id] = answer.score
                return scores
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Jev scoring failed (%s); preserving retrieval order", type(exc).__name__
            )
            return None


class JevDecisionClient:
    """One bounded request, no retries; failures reveal no prompts or credentials."""

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    def choose(
        self, *, state: Mapping[str, object], instructions: str, criteria: Mapping[str, str]
    ) -> str | None:
        key = self._settings.openrouter_api_key
        if not key or not key.get_secret_value().strip():
            logger.warning("Jev unavailable: missing OPENROUTER_API_KEY; using local rules")
            return None
        try:
            with httpx.Client(
                timeout=self._settings.jev_timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(
                    JEV_ENDPOINT,
                    headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                    json={
                        "model": JEV_MODEL,
                        "state": dict(state),
                        "questions": {
                            "decision": {
                                "type": "choice",
                                "instructions": instructions,
                                "criteria": dict(criteria),
                            }
                        },
                    },
                )
                response.raise_for_status()
                answer = ChoiceAnswer.model_validate(response.json()["answers"]["decision"])
                if answer.type != "choice" or answer.choice not in criteria:
                    raise ValueError("Invalid decision")
                return answer.choice
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Jev decision failed (%s); using local rules", type(exc).__name__)
            return None


def decision_state(messages: Sequence[BaseMessage]) -> dict[str, object]:
    """Bound text sent to the decision provider; exclude tools and system messages."""
    recent = []
    for message in messages:
        if message.type not in {"human", "ai"}:
            continue
        content = message.content
        if isinstance(content, list):
            content = "\n".join(
                item if isinstance(item, str) else item.get("text", "")
                for item in content
                if isinstance(item, str)
                or (isinstance(item, dict) and isinstance(item.get("text"), str))
            )
        if content:
            recent.append({"role": message.type, "text": content[-4000:]})
    return {"recent_conversation": recent[-4:]}


def use_jev(settings: Settings) -> bool:
    return settings.response_mode != "deterministic" and settings.decision_provider == "jev"
