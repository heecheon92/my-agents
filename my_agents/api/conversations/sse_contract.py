"""Backend-owned OpenAPI extension for named conversation SSE events."""

from __future__ import annotations

from copy import deepcopy

from my_agents.conversations.schemas import ReasoningSummaryDeltaEventData

_REASONING_SUMMARY_DELTA_SCHEMA = ReasoningSummaryDeltaEventData.model_json_schema()


def conversation_sse_responses(description: str) -> dict[int, dict[str, object]]:
    """Describe named SSE payloads without pretending the stream is JSON."""
    return {
        200: {
            "description": description,
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        "event: reasoning_summary_delta\n"
                        'data: {"stage":"retrieval_planning","delta":"Focused search.",'
                        '"sequence":1}\n\n'
                        "event: answer_delta\n"
                        'data: {"delta":"Hello","sequence":1}\n\n'
                        "event: run_completed\n"
                        'data: {"run_id":"...","reply":"Hello"}\n\n'
                    ),
                    "x-sse-events": {
                        "reasoning_summary_delta": {
                            "description": (
                                "Optional model-authored reasoning-summary text delta. Invalid or "
                                "unsupported summary events must not prevent answer completion."
                            ),
                            "schema": deepcopy(_REASONING_SUMMARY_DELTA_SCHEMA),
                        }
                    },
                }
            },
        }
    }
