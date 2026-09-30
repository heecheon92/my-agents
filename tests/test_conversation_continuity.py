"""Budget, source provenance, retention and guest regression checks remain offline."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select

from my_agents.api.conversations.run_lifecycle import admit_run
from my_agents.conversations.continuity import context_events, prepared_messages, token_count
from my_agents.conversations.continuity_models import (
    AttachmentNoteModel,
    ConversationSummaryModel,
    MessageAttachmentModel,
    ProviderCleanupModel,
)
from my_agents.conversations.models import AgentEventModel, ConversationModel, MessageModel
from my_agents.document_workspace.retention import cleanup_once
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.model_defaults import SUPPORTED_ASSISTANT_MODELS
from my_agents.persistence.database import get_database_session
from my_agents.settings import get_settings
from tests.test_conversations_api import SpyGraph, _client, _signup_login
from tests.test_document_workspace import _client as workspace_client
from tests.test_document_workspace import _signup_login as workspace_login
from tests.test_guest_access_api import _client as guest_client
from tests.test_guest_access_api import _guest_login


@pytest.mark.parametrize("model", SUPPORTED_ASSISTANT_MODELS)
def test_summarization_preference_catalog_and_reset(monkeypatch, model):
    client = _client(monkeypatch, SpyGraph())
    _signup_login(client, "summary@example.com")
    catalog = client.get("/capabilities/summarization-models").json()
    assert [item["id"] for item in catalog["models"]] == [
        "gpt-6.1-sol",
        "gpt-6-luna",
        "gpt-6-astra",
    ]
    assert catalog["recommended_model"] == catalog["default_model"] == "gpt-6-luna"
    selected = client.patch("/summarization/preferences", json={"summarization_model": model})
    assert selected.status_code == 200
    assert selected.json()["effective_model"] == model
    reset = client.patch("/summarization/preferences", json={"summarization_model": None})
    assert reset.json()["selected_model"] is None
    assert reset.json()["effective_model"] == "gpt-6-luna"


def test_summarization_preference_requires_auth_and_closed_request(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/summarization/preferences").status_code == 401
    _signup_login(client, "summary-contract@example.com")
    for body in (
        {},
        {"summarization_model": "unknown"},
        {"summarization_model": None, "extra": True},
    ):
        assert client.patch("/summarization/preferences", json=body).status_code == 422


def test_guest_summary_preference_locked(monkeypatch):
    client = guest_client(monkeypatch)
    _guest_login(client)
    assert client.get("/summarization/preferences").json()["customizable"] is False
    assert client.get("/capabilities/summarization-models").json()["customizable"] is False
    assert (
        client.patch(
            "/summarization/preferences", json={"summarization_model": "gpt-6-astra"}
        ).status_code
        == 403
    )


def _history_run(monkeypatch):
    client = _client(monkeypatch, SpyGraph())
    user_id = _signup_login(client, "history@example.com")
    conversation_id = client.post("/conversations", json={"title": "Continuity"}).json()["id"]
    session = next(get_database_session())
    for index in range(34):
        session.add(
            MessageModel(
                conversation_id=conversation_id,
                role="user",
                content=f"Decision {index}: use metric units.",
            )
        )
        session.add(
            MessageModel(
                conversation_id=conversation_id,
                role="assistant",
                content=f"Unverified claim {index}.",
            )
        )
        session.flush()
    session.commit()
    run = admit_run(
        db=session,
        conversation_id=conversation_id,
        user_id=user_id,
        message="Remember the earlier constraints",
        selection_context=KnowledgeBaseSelectionContext(
            mode="all", knowledge_base_ids=(), resolved_count=0
        ),
        reasoning_mode="standard",
        reasoning_effort="medium",
    ).run
    return session, conversation_id, run


def test_compaction_source_boundary_and_reuse(monkeypatch):
    db, conversation_id, run = _history_run(monkeypatch)
    events = context_events(db, conversation_id, run.id)
    emitted = []
    while True:
        try:
            emitted.append(next(events))
        except StopIteration as result:
            messages = result.value
            break
    assert "context_compaction_started" in emitted[0]
    assert "context_compaction_completed" in emitted[-1]
    summary = db.get(ConversationSummaryModel, conversation_id)
    assert summary is not None
    assert summary.owner_user_id == run.user_id
    assert summary.model == "gpt-6-luna"
    assert summary.covered_message_id != run.user_message_id
    assert isinstance(messages[0], SystemMessage)
    assert messages[-1].content == "Remember the earlier constraints"
    event_count = len(db.scalars(select(AgentEventModel)).all())
    prepared_messages(db, conversation_id, run.id)
    assert len(db.scalars(select(AgentEventModel)).all()) == event_count
    assert token_count(summary.body_json) <= 3000
    db.close()


def test_compaction_failure_keeps_latest_and_records_incomplete_context(monkeypatch):
    from my_agents.conversations import continuity

    db, conversation_id, run = _history_run(monkeypatch)

    def fail(**kwargs):
        raise RuntimeError("simulated summary failure")

    monkeypatch.setattr(continuity, "generate_summary", fail)
    messages = prepared_messages(db, conversation_id, run.id)
    assert messages[-1].content == "Remember the earlier constraints"
    assert db.get(ConversationSummaryModel, conversation_id) is None
    assert "history_incomplete" in messages[0].content
    assert "simulated summary failure" not in " ".join(messages[0].content)
    assert db.scalar(
        select(AgentEventModel).where(AgentEventModel.event_type == "context_compaction_failed")
    )
    db.close()


def test_current_oversized_prompt_is_refused_before_admission(monkeypatch):
    client = _client(monkeypatch, SpyGraph())
    _signup_login(client, "oversized@example.com")
    conversation_id = client.post("/conversations", json={"title": "Budget"}).json()["id"]
    response = client.post(f"/conversations/{conversation_id}/runs", json={"message": "x " * 40000})
    assert response.status_code == 413
    assert client.get(f"/conversations/{conversation_id}/messages").json() == []


def test_explicit_attachment_belongs_to_message_and_conversation_delete_enqueues_cleanup(
    monkeypatch,
):
    client, provider = workspace_client(monkeypatch)
    workspace_login(client)
    conversation_id = client.post("/conversations", json={"title": "Files"}).json()["id"]
    upload = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": ("table.csv", b"a,b\n1,2", "text/csv")},
        data={"provider_consent": "true"},
    ).json()
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Analyze spreadsheet", "attachment_ids": [upload["id"]]},
    )
    assert response.status_code == 200
    messages = client.get(f"/conversations/{conversation_id}/messages").json()
    assert messages[0]["attachments"][0]["id"] == upload["id"]
    assert messages[1]["attachments"] == []
    db = next(get_database_session())
    assert db.scalar(select(MessageAttachmentModel)) is not None
    db.close()
    assert client.delete(f"/conversations/{conversation_id}").status_code == 204
    db = next(get_database_session())
    assert db.scalar(select(MessageAttachmentModel)) is None
    assert db.scalar(select(AttachmentNoteModel)) is None
    assert db.scalar(select(ProviderCleanupModel)) is not None
    cleanup_once(db, provider)
    assert provider.deleted_files
    assert provider.deleted_containers
    db.close()


def test_new_original_retention_is_seven_days(monkeypatch):
    client, _ = workspace_client(monkeypatch)
    workspace_login(client)
    capabilities = client.get("/capabilities/document-workspace").json()
    assert capabilities["original_file_ttl_seconds"] == 604800
    assert capabilities["notes_retention"] == "conversation"
    assert capabilities["automatic_recall_supported"] is True
    conversation_id = client.post("/conversations", json={"title": "Expiry"}).json()["id"]
    attachment = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"provider_consent": "true"},
    ).json()
    expires = datetime.fromisoformat(attachment["expires_at"]).replace(tzinfo=UTC)
    assert expires > datetime.now(UTC) + timedelta(days=6)


def test_automatic_original_recall_without_resubmitting_ids(monkeypatch):
    client, provider = workspace_client(monkeypatch)
    workspace_login(client)
    client.patch("/assistant/preferences", json={"assistant_model": "gpt-6.1-sol"})
    conversation_id = client.post("/conversations", json={"title": "Recall"}).json()["id"]
    attachment = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": ("table.csv", b"a,b\n1,2", "text/csv")},
        data={"provider_consent": "true"},
    ).json()
    first = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Analyze table.csv", "attachment_ids": [attachment["id"]]},
    )
    assert first.status_code == 200
    second = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Check the column in table.csv", "reasoning_effort": "none"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["attachments"][0]["id"] == attachment["id"]
    assert second.json()["assistant_model"] == get_settings().document_workspace_model
    assert provider.reasoning == ("standard", "none")
    events = client.get(
        f"/conversations/{conversation_id}/runs/{second.json()['run_id']}/events"
    ).json()
    assert events[0]["payload"].get("assistant_model") is None
    assert any(event["event_type"] == "run_model_resolved" for event in events)
    third = client.post(
        f"/conversations/{conversation_id}/runs", json={"message": "Thanks, good answer"}
    )
    assert third.status_code == 200
    assert third.json()["attachments"] == []
    assert third.json()["assistant_model"] == "gpt-6.1-sol"
    messages = client.get(f"/conversations/{conversation_id}/messages").json()
    assert messages[2]["attachments"] == []


def test_attachment_selection_is_durable_and_resumes_with_authorized_id(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from my_agents.agents.general_assistant.graph import build_graph
    from my_agents.api.assistant import get_graph_runner

    client, provider = workspace_client(monkeypatch)
    client.app.dependency_overrides[get_graph_runner] = lambda: graph
    graph = build_graph(checkpointer=InMemorySaver(), document_selection_hitl_enabled=True)
    workspace_login(client)
    conversation_id = client.post("/conversations", json={"title": "Selection"}).json()["id"]
    ids = []
    for filename in ("one.csv", "two.csv"):
        attachment = client.post(
            f"/conversations/{conversation_id}/attachments",
            files={"file": (filename, b"a,b\n1,2", "text/csv")},
            data={"provider_consent": "true"},
        ).json()
        ids.append(attachment["id"])
    first = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Analyze these spreadsheets", "attachment_ids": ids},
    )
    assert first.status_code == 200
    waiting = client.post(
        f"/conversations/{conversation_id}/runs", json={"message": "그 파일 요약해 주세요"}
    )
    assert waiting.status_code == 202, waiting.text
    interaction = waiting.json()["interaction"]
    assert interaction["type"] == "attachment_selection"
    assert interaction["option_count"] == 2
    recovered = client.get(f"/conversations/{conversation_id}/runs/{waiting.json()['run_id']}")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["interaction"]["type"] == "attachment_selection"
    assert {option["attachment_id"] for option in interaction["options"]} == set(ids)
    body = {
        "schema_version": 2,
        "interaction_id": interaction["interaction_id"],
        "type": "attachment_selection",
        "kind": "select",
        "attachment_ids": [ids[0]],
    }
    resumed = client.post(
        f"/conversations/{conversation_id}/runs/{waiting.json()['run_id']}/resume", json=body
    )
    assert resumed.status_code == 200, resumed.text
    assert [item["id"] for item in resumed.json()["attachments"]] == [ids[0]]
    assert provider.reasoning[0] == "standard"


def test_streamed_database_failure_persists_failed_run_and_terminal_error(monkeypatch):
    from .test_conversations_api import _parse_sse

    client, provider = workspace_client(monkeypatch)
    workspace_login(client)
    for index in range(2):
        conversation_id = client.post("/conversations", json={"title": f"Failure {index}"}).json()[
            "id"
        ]
        attachment = client.post(
            f"/conversations/{conversation_id}/attachments",
            files={"file": ("one.csv", b"a,b\n1,2", "text/csv")},
            data={"provider_consent": "true"},
        ).json()
        response = client.post(
            f"/conversations/{conversation_id}/runs/stream",
            json={"message": "Analyze spreadsheet", "attachment_ids": [attachment["id"]]},
        )
        events = _parse_sse(response.text)
        names = [event["event"] for event in events]
        assert names[0] == "run_started"
        assert names[-1] == ("run_completed" if index == 0 else "run_error")
        runs = client.get(f"/conversations/{conversation_id}/runs").json()
        assert runs[0]["status"] == ("completed" if index == 0 else "failed")


def test_request_id_correlates_admission_and_duplicate_is_refused(monkeypatch):
    from uuid import uuid4

    client = _client(monkeypatch, SpyGraph())
    _signup_login(client, "request-id@example.com")
    conversation_id = client.post("/conversations", json={"title": "Admission"}).json()["id"]
    request_id = str(uuid4())
    body = {"message": "Identical text", "client_request_id": request_id}
    response = client.post(f"/conversations/{conversation_id}/runs", json=body)
    assert response.status_code == 200
    runs = client.get(f"/conversations/{conversation_id}/runs").json()
    assert runs[0]["client_request_id"] == request_id
    events = client.get(f"/conversations/{conversation_id}/runs/{runs[0]['run_id']}/events").json()
    assert events[0]["payload"]["client_request_id"] == request_id
    assert client.post(f"/conversations/{conversation_id}/runs", json=body).status_code == 409
    assert len(client.get(f"/conversations/{conversation_id}/messages").json()) == 2


def test_stream_attachment_selection_before_rag_yields_interrupt(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from my_agents.agents.general_assistant.graph import build_graph
    from my_agents.api.assistant import get_graph_runner

    from .test_conversations_api import _parse_sse

    client, _ = workspace_client(monkeypatch)
    graph = build_graph(checkpointer=InMemorySaver(), document_selection_hitl_enabled=True)
    client.app.dependency_overrides[get_graph_runner] = lambda: graph
    workspace_login(client)
    conversation_id = client.post("/conversations", json={"title": "Stream choice"}).json()["id"]
    ids = []
    for filename in ("one.csv", "two.csv"):
        item = client.post(
            f"/conversations/{conversation_id}/attachments",
            files={"file": (filename, b"a,b\n1,2", "text/csv")},
            data={"provider_consent": "true"},
        ).json()
        ids.append(item["id"])
    assert (
        client.post(
            f"/conversations/{conversation_id}/runs",
            json={"message": "Analyze files", "attachment_ids": ids},
        ).status_code
        == 200
    )
    response = client.post(
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "첨부 파일 중 하나를 분석해 주세요"},
    )
    events = _parse_sse(response.text)
    assert events[-1]["event"] == "run_interrupted", response.text
    assert events[-1]["data"]["interaction"]["type"] == "attachment_selection"
    assert "retrieval_completed" not in [event["event"] for event in events]


def test_internal_continuity_never_expands_jev_data_scope():
    from my_agents.decisions import decision_state

    messages = [
        SystemMessage(content="private file notes", additional_kwargs={"continuity": True}),
        HumanMessage(content="Thanks"),
        AIMessage(content="Welcome"),
    ]
    assert "private file notes" not in json.dumps(decision_state(messages))


def test_removal_erases_notes_and_future_source_context(monkeypatch):
    from my_agents.conversations.file_context import file_context_notes

    client, provider = workspace_client(monkeypatch)
    workspace_login(client)
    conversation_id = client.post("/conversations", json={"title": "Remove"}).json()["id"]
    item = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": ("one.csv", b"a,b\n1,2", "text/csv")},
        data={"provider_consent": "true"},
    ).json()
    result = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Analyze file", "attachment_ids": [item["id"]]},
    ).json()
    db = next(get_database_session())
    db.add(
        AttachmentNoteModel(
            attachment_id=item["id"],
            run_id=result["run_id"],
            body_json=json.dumps({"finding": "sensitive finding"}),
        )
    )
    db.commit()
    db.close()
    assert (
        client.delete(f"/conversations/{conversation_id}/attachments/{item['id']}").status_code
        == 204
    )
    db = next(get_database_session())
    assert db.scalar(select(AttachmentNoteModel)) is None
    messages = prepared_messages(db, conversation_id)
    assert "sensitive finding" not in " ".join(str(message.content) for message in messages)
    assert "Historical source-derived reply omitted" in " ".join(
        str(message.content) for message in messages
    )
    assert file_context_notes(db, db.get(ConversationModel, conversation_id)) == []
    db.close()


def test_expired_original_preserves_notes_but_cleanup_is_not_skipped_after_listing(monkeypatch):
    from my_agents.conversations.file_context import file_context_notes
    from my_agents.document_workspace.models import ConversationAttachmentModel

    client, provider = workspace_client(monkeypatch)
    workspace_login(client)
    conversation_id = client.post("/conversations", json={"title": "Expired notes"}).json()["id"]
    item = client.post(
        f"/conversations/{conversation_id}/attachments",
        files={"file": ("one.csv", b"a,b\n1,2", "text/csv")},
        data={"provider_consent": "true"},
    ).json()
    result = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Analyze file", "attachment_ids": [item["id"]]},
    ).json()
    db = next(get_database_session())
    row = db.get(ConversationAttachmentModel, item["id"])
    row.provider_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.add(
        AttachmentNoteModel(
            attachment_id=row.id,
            run_id=result["run_id"],
            body_json=json.dumps({"finding": "reported earlier", "coverage": "partial"}),
        )
    )
    db.commit()
    provider_id = row.provider_file_id
    db.close()
    listed = client.get(f"/conversations/{conversation_id}/attachments").json()
    assert listed[0]["status"] == "expired"
    db = next(get_database_session())
    notes = file_context_notes(db, db.get(ConversationModel, conversation_id))
    assert notes[0]["original_available"] is False
    assert notes[0]["observations"][0]["finding"] == "reported earlier"
    cleanup_once(db, provider)
    assert provider_id in provider.deleted_files
    assert db.scalar(select(AttachmentNoteModel)) is not None
    assert db.get(ConversationAttachmentModel, item["id"]).cleanup_scheduled_at is not None
    db.close()
