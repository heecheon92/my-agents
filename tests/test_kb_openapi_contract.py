"""OpenAPI contract coverage for the KB-first document/chat path."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import load_app

REQUIRED_KB_PATHS = {
    "/knowledge-bases/team-upload-staging",
    "/knowledge-bases/{knowledge_base_id}",
    "/knowledge-bases/{knowledge_base_id}/documents",
    "/knowledge-bases/{knowledge_base_id}/documents/upload",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest/async",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}",
    "/groups/{group_id}/publish-requests",
    "/groups/{group_id}/publish-requests/{request_id}/source",
    "/groups/{group_id}/publish-requests/{request_id}/approve",
    "/groups/{group_id}/publish-requests/{request_id}/reject",
    "/conversations/{conversation_id}",
    "/conversations/{conversation_id}/messages/{message_id}/replay",
    "/conversations/{conversation_id}/messages/{message_id}/replay/stream",
}


def test_openapi_exposes_kb_first_document_and_chat_selection_contract() -> None:
    schema = TestClient(load_app()).get("/openapi.json").json()

    assert REQUIRED_KB_PATHS.issubset(schema["paths"])
    assert "delete" in schema["paths"]["/conversations/{conversation_id}"]

    request_schema = schema["components"]["schemas"]["ConversationRunRequest"]
    run_response_schema = schema["components"]["schemas"]["ConversationRunResponse"]
    replay_request_schema = schema["components"]["schemas"]["ConversationReplayRequest"]
    run_summary_schema = schema["components"]["schemas"]["AgentRunSummaryResponse"]
    selection_schema = schema["components"]["schemas"]["KnowledgeBaseSelection"]
    kb_response_schema = schema["components"]["schemas"]["KnowledgeBaseResponse"]
    publish_request_schema = schema["components"]["schemas"]["KnowledgePublishRequestResponse"]
    publish_request_create_schema = schema["components"]["schemas"][
        "KnowledgePublishRequestCreateRequest"
    ]

    assert request_schema["properties"]["knowledge_base_selection"] == {
        "$ref": "#/components/schemas/KnowledgeBaseSelection"
    }
    assert run_response_schema["properties"]["knowledge_base_selection"] == {
        "$ref": "#/components/schemas/KnowledgeBaseSelection"
    }
    assert run_summary_schema["properties"]["knowledge_base_selection"] == {
        "$ref": "#/components/schemas/KnowledgeBaseSelection"
    }
    assert replay_request_schema["properties"]["knowledge_base_selection"]["anyOf"][0] == {
        "$ref": "#/components/schemas/KnowledgeBaseSelection"
    }
    assert run_response_schema["properties"]["resolved_knowledge_base_count"]["type"] == "integer"
    assert run_response_schema["properties"]["resolved_knowledge_base_ids"]["type"] == "array"
    assert run_summary_schema["properties"]["resolved_knowledge_base_count"]["type"] == "integer"
    assert selection_schema["properties"]["mode"]["enum"] == ["all", "selected"]
    assert selection_schema["properties"]["knowledge_base_ids"]["type"] == "array"
    assert kb_response_schema["properties"]["purpose"]["$ref"] == (
        "#/components/schemas/KnowledgeBasePurpose"
    )
    assert kb_response_schema["properties"]["published_group_ids"]["type"] == "array"
    assert publish_request_schema["properties"]["status"]["$ref"] == (
        "#/components/schemas/KnowledgePublishRequestStatus"
    )
    source_schema = schema["components"]["schemas"]["KnowledgePublishRequestSourceResponse"]
    source_document_schema = schema["components"]["schemas"][
        "KnowledgePublishRequestSourceDocumentResponse"
    ]
    assert source_schema["properties"]["source_kind"]["enum"] == ["document", "knowledge_base"]
    assert source_document_schema["properties"]["content"]["type"] == "string"
    assert (
        publish_request_create_schema["properties"]["target_knowledge_base_id"]["anyOf"][0]["type"]
        == "string"
    )

    upload_body_ref = schema["paths"]["/documents/upload"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]["$ref"]
    kb_upload_body_ref = schema["paths"]["/knowledge-bases/{knowledge_base_id}/documents/upload"][
        "post"
    ]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    upload_contract = str(schema["components"]["schemas"][upload_body_ref.rsplit("/", 1)[-1]])
    kb_upload_contract = str(schema["components"]["schemas"][kb_upload_body_ref.rsplit("/", 1)[-1]])
    assert ".xlsx" in upload_contract
    assert ".pptx" in upload_contract
    assert ".xlsx" in kb_upload_contract
    assert ".pptx" in kb_upload_contract
