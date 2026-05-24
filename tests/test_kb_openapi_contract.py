"""OpenAPI contract coverage for the KB-first document/chat path."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import load_app

REQUIRED_KB_PATHS = {
    "/knowledge-bases/{knowledge_base_id}",
    "/knowledge-bases/{knowledge_base_id}/documents",
    "/knowledge-bases/{knowledge_base_id}/documents/upload",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest/async",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs",
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}",
    "/conversations/{conversation_id}/messages/{message_id}/replay",
}


def test_openapi_exposes_kb_first_document_and_chat_selection_contract() -> None:
    schema = TestClient(load_app()).get("/openapi.json").json()

    assert REQUIRED_KB_PATHS.issubset(schema["paths"])

    request_schema = schema["components"]["schemas"]["ConversationRunRequest"]
    run_response_schema = schema["components"]["schemas"]["ConversationRunResponse"]
    replay_request_schema = schema["components"]["schemas"]["ConversationReplayRequest"]
    run_summary_schema = schema["components"]["schemas"]["AgentRunSummaryResponse"]
    selection_schema = schema["components"]["schemas"]["KnowledgeBaseSelection"]

    assert request_schema["properties"]["knowledge_base_selection"] == {
        "$ref": "#/components/schemas/KnowledgeBaseSelection"
    }
    assert request_schema["properties"]["optional_personal_knowledge_base_ids"]["type"] == "array"
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
    assert (
        run_response_schema["properties"]["source_context_group_id"]["anyOf"][0]["type"] == "string"
    )
    assert (
        run_response_schema["properties"]["mandatory_group_knowledge_base_ids"]["type"] == "array"
    )
    assert (
        run_response_schema["properties"]["optional_personal_knowledge_base_ids"]["type"] == "array"
    )
    assert run_response_schema["properties"]["resolved_knowledge_base_ids"]["type"] == "array"
    assert run_summary_schema["properties"]["resolved_knowledge_base_count"]["type"] == "integer"
    assert selection_schema["properties"]["mode"]["enum"] == ["all", "selected"]
    assert selection_schema["properties"]["knowledge_base_ids"]["type"] == "array"
