"""Contract tests for protocol-neutral, versioned agent interactions."""

import pytest
from pydantic import ValidationError

from my_agents.api import create_app
from my_agents.interactions.schemas import (
    ConversationRunResumeRequest,
    PendingDocumentSelection,
)


def test_interaction_models_require_version_and_semantic_type() -> None:
    with pytest.raises(ValidationError):
        ConversationRunResumeRequest.model_validate(
            {"interaction_id": "run:document_selection", "document_id": "document-id"}
        )

    request = ConversationRunResumeRequest.model_validate(
        {
            "schema_version": 1,
            "interaction_id": "run:document_selection",
            "type": "document_selection",
            "document_id": "document-id",
        }
    )

    assert request.schema_version == 1
    assert request.type == "document_selection"


def test_interaction_payload_rejects_ui_and_sensitive_extension_fields() -> None:
    payload = {
        "schema_version": 1,
        "interaction_id": "run:document_selection",
        "type": "document_selection",
        "reason_code": "ambiguous_document_reference",
        "message_key": "clarification.document_scope.select_source",
        "expires_at": "2026-08-18T00:00:00Z",
        "option_count": 0,
        "options": [],
        "next_cursor": None,
    }

    PendingDocumentSelection.model_validate(payload)
    for forbidden_field in ("component", "layout", "prompt", "provider_trace"):
        with pytest.raises(ValidationError):
            PendingDocumentSelection.model_validate({**payload, forbidden_field: "forbidden"})


def test_openapi_exposes_required_versioned_interaction_contract() -> None:
    components = create_app().openapi()["components"]["schemas"]

    for schema_name in (
        "PendingDocumentSelection",
        "ConversationRunResumeRequest",
        "DocumentSelectionOptionsResponse",
    ):
        schema = components[schema_name]
        assert "schema_version" in schema["required"]
        assert "type" in schema["required"]
        assert schema["properties"]["schema_version"]["const"] == 1
        assert schema["properties"]["type"]["const"] == "document_selection"

    interrupt_event = components["RunInterruptedEventPayload"]
    resumed_event = components["RunResumedEventPayload"]
    assert "interaction_schema_version" in interrupt_event["required"]
    assert "interaction_schema_version" in resumed_event["required"]
