"""Contract tests for stable machine-readable API error codes."""

from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from my_agents.api import create_app
from my_agents.api.errors import (
    APIErrorCode,
    APIHTTPException,
    document_upload_error_code,
    error_code_for_http_exception,
)


def test_validation_and_unmatched_route_errors_include_stable_codes() -> None:
    client = TestClient(create_app())

    invalid = client.post("/assistant/chat", json={"message": "", "history": []})
    missing = client.get("/route-that-does-not-exist")

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_request"
    assert isinstance(invalid.json()["detail"], list)
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found", "code": "resource_not_found"}


def test_high_value_error_codes_are_stable() -> None:
    cases = (
        (401, "invalid email or password", APIErrorCode.INVALID_CREDENTIALS),
        (429, "guest prompt limit reached", APIErrorCode.GUEST_PROMPT_LIMIT_REACHED),
        (400, "uploaded PDF exceeds the 5 MiB V1 limit", APIErrorCode.UPLOAD_TOO_LARGE),
        (
            409,
            "publish request already reviewed",
            APIErrorCode.PUBLISH_REQUEST_ALREADY_REVIEWED,
        ),
    )

    for status_code, detail, expected in cases:
        error = APIHTTPException(status_code=status_code, detail=detail, code=expected)
        assert error_code_for_http_exception(error) is expected

    assert (
        document_upload_error_code(
            "uploaded PDF exceeds the 5 MiB V1 limit",
            unsupported_media_type=False,
        )
        is APIErrorCode.UPLOAD_TOO_LARGE
    )


def test_uncoded_legacy_errors_use_stable_status_fallbacks() -> None:
    error = StarletteHTTPException(status_code=409, detail="changeable diagnostic prose")

    assert error_code_for_http_exception(error) is APIErrorCode.CONFLICT


def test_openapi_documents_error_and_discriminated_event_contracts() -> None:
    schema = create_app().openapi()
    components = schema["components"]["schemas"]

    assert components["APIErrorResponse"]["required"] == ["detail", "code"]
    event_contract = components["AgentEventResponse"]
    assert event_contract["discriminator"]["propertyName"] == "event_type"
    assert set(event_contract["discriminator"]["mapping"]) == {
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "full_document_read",
        "graph_invoked",
        "attachments_ready",
        "document_workspace_started",
        "artifact_created",
        "answer_composed",
        "run_cancel_requested",
        "run_interrupted",
        "run_resumed",
        "run_cancelled",
        "run_failed",
    }
    assert components["AgentTraceEvidence"]["additionalProperties"] is False
