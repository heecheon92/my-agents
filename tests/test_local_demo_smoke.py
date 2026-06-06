"""Local V1 API smoke helper unit tests."""

from __future__ import annotations

import pytest

from scripts.local_demo_seed import DEMO_DOCUMENT_TITLE
from scripts.local_demo_smoke import (
    SmokeFailure,
    assert_redacted_run_events,
    find_document_by_title,
    parse_sse_events,
)


def test_parse_sse_events_returns_named_json_payloads() -> None:
    body = (
        "event: answer_delta\n"
        'data: {"delta":"Hello","sequence":1}\n\n'
        "event: run_completed\n"
        'data: {"run_id":"run-1","citations":[{"id":"citation-1"}]}\n\n'
    )

    events = parse_sse_events(body)

    assert events == [
        {"event": "answer_delta", "data": {"delta": "Hello", "sequence": 1}},
        {
            "event": "run_completed",
            "data": {"run_id": "run-1", "citations": [{"id": "citation-1"}]},
        },
    ]


def test_parse_sse_events_rejects_missing_event_name() -> None:
    with pytest.raises(SmokeFailure, match="missing event name"):
        parse_sse_events('data: {"ok":true}\n\n')


def test_find_document_by_title_returns_seeded_document() -> None:
    document = find_document_by_title(
        [
            {"id": "other", "title": "Other"},
            {"id": "seeded", "title": DEMO_DOCUMENT_TITLE},
        ],
        DEMO_DOCUMENT_TITLE,
    )

    assert document["id"] == "seeded"


def test_find_document_by_title_explains_missing_seed_data() -> None:
    with pytest.raises(SmokeFailure, match="scripts.local_demo_seed"):
        find_document_by_title([], DEMO_DOCUMENT_TITLE)


def test_assert_redacted_run_events_accepts_safe_event_payloads() -> None:
    assert_redacted_run_events(
        [
            {"event_type": "user_message_stored", "payload": {"content_length": 18}},
            {"event_type": "retrieval_completed", "payload": {"authorized_context_count": 1}},
            {"event_type": "graph_invoked", "payload": {"route_label": "general_assistant"}},
            {"event_type": "answer_composed", "payload": {"reply_length": 42}},
        ],
        forbidden_text=["raw user prompt"],
    )


def test_assert_redacted_run_events_rejects_raw_prompt_leak() -> None:
    with pytest.raises(SmokeFailure, match="leaked forbidden raw text"):
        assert_redacted_run_events(
            [
                {"event_type": "user_message_stored", "payload": {"content_length": 18}},
                {"event_type": "retrieval_completed", "payload": {}},
                {"event_type": "graph_invoked", "payload": {"route_label": "raw user prompt"}},
                {"event_type": "answer_composed", "payload": {}},
            ],
            forbidden_text=["raw user prompt"],
        )


@pytest.mark.parametrize("forbidden_key", ["token", "password", "api_key", "raw_context"])
def test_assert_redacted_run_events_rejects_sensitive_payload_keys(forbidden_key: str) -> None:
    with pytest.raises(SmokeFailure, match="forbidden payload keys"):
        assert_redacted_run_events(
            [
                {"event_type": "user_message_stored", "payload": {"content_length": 18}},
                {
                    "event_type": "retrieval_completed",
                    "payload": {
                        "authorized_context_count": 1,
                        "nested": {forbidden_key: "secret-ish value"},
                    },
                },
                {"event_type": "graph_invoked", "payload": {"route_label": "general_assistant"}},
                {"event_type": "answer_composed", "payload": {"reply_length": 42}},
            ],
            forbidden_text=[],
        )


def test_assert_redacted_run_events_rejects_non_object_payload() -> None:
    with pytest.raises(SmokeFailure, match="payload is not an object"):
        assert_redacted_run_events(
            [
                {"event_type": "user_message_stored", "payload": {"content_length": 18}},
                {"event_type": "retrieval_completed", "payload": []},
                {"event_type": "graph_invoked", "payload": {"route_label": "general_assistant"}},
                {"event_type": "answer_composed", "payload": {"reply_length": 42}},
            ],
            forbidden_text=[],
        )
