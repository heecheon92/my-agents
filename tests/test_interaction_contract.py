"""Contract tests for protocol-neutral, versioned agent interactions."""

import pytest
from pydantic import ValidationError

from my_agents.api import create_app
from my_agents.interactions.schemas import (
    ConversationRunRefineRequestV2,
    ConversationRunResumeRequest,
    ConversationRunSelectRequestV2,
    PendingDocumentSelection,
    PendingDocumentSelectionV2,
    conversation_run_resume_request_adapter,
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

    selected = conversation_run_resume_request_adapter.validate_python(
        {
            "schema_version": 2,
            "interaction_id": "4a0b7c65-7c47-4bb3-9618-51ec95291843",
            "type": "document_selection",
            "kind": "select",
            "document_id": "document-id",
        }
    )
    refined = conversation_run_resume_request_adapter.validate_python(
        {
            "schema_version": 2,
            "interaction_id": "4a0b7c65-7c47-4bb3-9618-51ec95291843",
            "type": "document_selection",
            "kind": "refine",
            "text": "  Pydantic Annotated Literal.md  ",
        }
    )
    assert isinstance(selected, ConversationRunSelectRequestV2)
    assert isinstance(refined, ConversationRunRefineRequestV2)
    assert refined.text == "Pydantic Annotated Literal.md"


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


def test_v2_interaction_is_bounded_and_ui_neutral() -> None:
    payload = {
        "schema_version": 2,
        "interaction_id": "4a0b7c65-7c47-4bb3-9618-51ec95291843",
        "type": "document_selection",
        "reason_code": "unresolved_document_reference",
        "message_key": "clarification.document_scope.select_source",
        "expires_at": "2026-08-18T00:00:00Z",
        "option_count": 0,
        "library_count": 4000,
        "options": [],
        "next_cursor": None,
        "refinement": {
            "allowed": True,
            "attempts_used": 0,
            "attempts_max": 2,
            "max_length": 120,
        },
        "browse": {"allowed": False, "cursor": None},
    }
    parsed = PendingDocumentSelectionV2.model_validate(payload)
    assert parsed.library_count == 4000
    for forbidden_field in ("component", "layout", "prompt", "provider_trace"):
        with pytest.raises(ValidationError):
            PendingDocumentSelectionV2.model_validate({**payload, forbidden_field: "forbidden"})

    with pytest.raises(ValidationError):
        conversation_run_resume_request_adapter.validate_python(
            {
                "schema_version": 2,
                "interaction_id": payload["interaction_id"],
                "type": "document_selection",
                "kind": "refine",
                "text": "x" * 121,
            }
        )


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

    for schema_name in (
        "PendingDocumentSelectionV2",
        "ConversationRunSelectRequestV2",
        "ConversationRunRefineRequestV2",
        "DocumentSelectionOptionsResponseV2",
    ):
        schema = components[schema_name]
        assert schema["properties"]["schema_version"]["const"] == 2
        assert schema["properties"]["type"]["const"] == "document_selection"

    interrupt_event = components["RunInterruptedEventPayload"]
    resumed_event = components["RunResumedEventPayload"]
    assert "interaction_schema_version" in interrupt_event["required"]
    assert "interaction_schema_version" in resumed_event["required"]
