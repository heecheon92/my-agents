"""Turn provenance, exact delivered excerpts, and recall boundaries stay offline."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime
from sqlalchemy import select

from my_agents.agents.general_assistant import graph
from my_agents.agents.general_assistant.responders import (
    OpenAIResponseProvider,
    _build_input_messages,
)
from my_agents.conversations.continuity import prepared_messages
from my_agents.conversations.models import AgentRunModel, MessageModel
from my_agents.conversations.session_evidence import (
    MAX_RECORD_BYTES,
    AnswerEvidence,
    SessionEvidenceRuntime,
    capture_answer_input,
)
from my_agents.knowledge.models import DocumentModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision
from my_agents.settings import Settings, get_settings
from tests.test_conversations_api import SpyGraph, _client, _create_document, _signup_login
from tests.test_responders import FakeChatModel


@pytest.fixture
def session_case(monkeypatch):
    client = _client(monkeypatch, SpyGraph())
    user_id = _signup_login(client, "evidence@example.com")
    cid = client.post("/conversations", json={"title": "Evidence"}).json()["id"]
    doc = _create_document(
        client, json={"title": "LangGraph basics", "content": "Original source text."}
    ).json()
    gen = get_database_session()
    db = next(gen)
    now = datetime.now(UTC)
    question = MessageModel(
        conversation_id=cid, role="user", content="Explain state design", created_at=now
    )
    answer = MessageModel(
        conversation_id=cid,
        role="assistant",
        content="Use messages because nodes share it.",
        created_at=now + timedelta(seconds=1),
    )
    followup = MessageModel(
        conversation_id=cid,
        role="user",
        content="Did you receive the four state principles?",
        created_at=now + timedelta(seconds=2),
    )
    db.add_all([question, answer, followup])
    db.flush()
    prior = AgentRunModel(
        conversation_id=cid,
        user_id=user_id,
        status="completed",
        user_message_id=question.id,
        assistant_message_id=answer.id,
    )
    current = AgentRunModel(
        conversation_id=cid, user_id=user_id, status="running", user_message_id=followup.id
    )
    db.add_all([prior, current])
    db.commit()
    yield SimpleNamespace(
        db=db,
        client=client,
        user_id=user_id,
        cid=cid,
        doc_id=doc["id"],
        prior=prior,
        current=current,
        question=question,
        answer=answer,
        followup=followup,
    )
    gen.close()


def _capture(doc_id, text="messages + add_messages; tool routing example"):
    return capture_answer_input(
        messages=[],
        documents=[{"document_id": doc_id, "title": "LangGraph basics", "snippet": text}],
        memory_count=0,
        input_truncated=False,
    )


def _recall(case):
    messages = SessionEvidenceRuntime(case.db, case.user_id, case.current.id).messages_for_answer(
        [HumanMessage(content=case.followup.content, id=case.followup.id)]
    )
    return next((m.content for m in messages if m.additional_kwargs.get("session_evidence")), "")


def _turns(payload):
    return json.loads(payload.split("\n", 1)[1])["turns"]


def test_exact_prior_input_survives_new_source_and_user_correction(session_case):
    c = session_case
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(_capture(c.doc_id))
    c.db.commit()
    # Re-ingestion and a later user correction cannot rewrite what was supplied earlier.
    document = c.db.get(DocumentModel, c.doc_id)
    document.content = "NEW: downstream, audit, recovery, explicit next_step"
    c.followup.content += " NEW: downstream, audit, recovery, explicit next_step"
    c.db.commit()
    c.db.expire_all()  # Read persisted data, not a collector retained in process memory.
    turn = _turns(_recall(c))[0]
    assert turn["evidence_status"] == "recorded_successful_answer_boundary"
    assert turn["documents"][0]["excerpt"] == "messages + add_messages; tool routing example"
    assert "NEW:" not in json.dumps(turn)
    assert turn["assistant_claim"] == c.answer.content
    assert turn["user_statement"] == c.question.content
    assert turn["documents"][0]["excerpt_complete"] is True


@pytest.mark.parametrize("status", ["failed", "cancelled", "waiting_for_input"])
def test_only_committed_completed_answers_supply_evidence(session_case, status):
    c = session_case
    if status == "waiting_for_input":
        c.current.status = "failed"
        c.db.flush()
    c.prior.status = status
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(_capture(c.doc_id))
    c.db.commit()
    assert _recall(c) == ""


def test_replay_excludes_replaced_answer_and_future_branch(session_case):
    c = session_case
    c.current.user_message_id = c.question.id
    c.db.commit()
    assert _recall(c) == ""


def test_unknown_legacy_and_corrupt_evidence_are_not_no_retrieval(session_case):
    c = session_case
    turn = _turns(_recall(c))[0]
    assert turn["evidence_status"] == "unknown"
    assert "documents_omitted" not in turn
    for value in ("broken", '{"version":999}'):
        c.prior.session_evidence_json = value
        c.db.commit()
        assert _recall(c) == ""


def test_permission_revocation_excludes_evidence_and_linked_claim(session_case):
    c = session_case
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(
        _capture(c.doc_id, "PRIVATE ORIGINAL")
    )
    c.db.commit()
    c.db.delete(c.db.get(DocumentModel, c.doc_id))
    c.db.commit()
    assert _recall(c) == ""
    history = prepared_messages(c.db, c.cid)
    assert not any(m.content == c.answer.content for m in history)


def test_identity_and_conversation_boundaries(session_case):
    c = session_case
    assert (
        SessionEvidenceRuntime(c.db, "different-user", c.current.id).messages_for_answer([]) == []
    )
    other_cid = c.client.post("/conversations", json={"title": "Other"}).json()["id"]
    c.current.conversation_id = other_cid
    c.db.commit()
    assert _recall(c) == ""


def test_record_is_bounded_and_hidden_provenance_never_saved(session_case):
    c = session_case
    evidence = capture_answer_input(
        messages=[],
        documents=[
            {"snippet": "HIDDEN SYSTEM DATA"},
            *[{"document_id": c.doc_id, "snippet": "한" * 65536} for _ in range(8)],
        ],
        memory_count=2,
        input_truncated=False,
    )
    assert len(evidence.model_dump_json().encode()) <= MAX_RECORD_BYTES
    assert evidence.documents_omitted > 0
    assert "HIDDEN SYSTEM DATA" not in evidence.model_dump_json()
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(evidence)
    assert "HIDDEN SYSTEM DATA" not in c.prior.session_evidence_json


def test_capture_follows_final_input_budget_not_retrieval_shortlist(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_CONTEXT_INPUT_TOKENS", "4096")
    get_settings.cache_clear()
    captured = []
    rendered = _build_input_messages(
        model_name="gpt-6-luna",
        messages=[HumanMessage(content="Explain state")],
        route=RouteDecision(label="general_assistant", explanation="question"),
        guidance="Explain",
        retrieved_context=[
            {"document_id": "a", "snippet": "A" * 100000},
            {"document_id": "b", "snippet": "B" * 100000},
        ],
        capture=captured,
    )
    assert captured[0].documents == []
    assert captured[0].input_truncated is True
    assert "A" * 100 not in str(rendered)
    assert all("session_evidence" not in m.additional_kwargs for m in rendered)


def test_runtime_injection_never_enters_graph_state_or_public_delivery_manifest(
    session_case, monkeypatch
):
    c = session_case
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(
        _capture(c.doc_id, "EXACT HISTORICAL EVIDENCE")
    )
    c.db.commit()
    fake = FakeChatModel()
    provider = OpenAIResponseProvider(Settings(_env_file=None), chat_model=fake)
    monkeypatch.setattr(graph, "get_response_provider", lambda *args: provider)
    state = {
        "messages": [HumanMessage(content=c.followup.content, id=c.followup.id)],
        "route": RouteDecision(label="general_assistant", explanation="follow-up"),
        "capability": None,
    }
    result = graph._compose_reply(
        state,
        Runtime(
            context={
                "session_evidence_runtime": SessionEvidenceRuntime(c.db, c.user_id, c.current.id)
            }
        ),
        "Answer from evidence",
    )
    assert "EXACT HISTORICAL EVIDENCE" in str(fake.calls[0])
    assert "EXACT HISTORICAL EVIDENCE" not in str(state)
    assert "EXACT HISTORICAL EVIDENCE" not in str(result)
    assert c.current.session_evidence_json
    assert AnswerEvidence.model_validate_json(c.current.session_evidence_json).documents == []
    # The graph still receives the original transcript shape; recall is response-only.
    history = prepared_messages(c.db, c.cid)
    assert not any(m.additional_kwargs.get("session_evidence") for m in history)


def test_recall_budget_is_explicitly_partial(session_case):
    c = session_case
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(
        _capture(c.doc_id, "prefix " * 1000 + "four state principles")
    )
    c.db.commit()
    turn = _turns(_recall(c))[0]
    assert turn["documents"][0]["excerpt_complete"] is False
    assert len(turn["documents"][0]["excerpt"]) <= 1600


def test_conversation_deletion_removes_evidence_with_runs(session_case):
    c = session_case
    c.current.status = "failed"
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(_capture(c.doc_id))
    c.db.commit()
    response = c.client.delete(f"/conversations/{c.cid}")
    assert response.status_code == 204
    c.db.expire_all()
    assert (
        c.db.scalar(select(AgentRunModel.id).where(AgentRunModel.conversation_id == c.cid)) is None
    )


def test_provider_failure_cannot_create_successful_input_record():
    class FailingModel(FakeChatModel):
        def stream(self, *args, **kwargs):
            raise RuntimeError("provider failed")
            yield AIMessage(content="unreachable")

    provider = OpenAIResponseProvider(Settings(_env_file=None), chat_model=FailingModel())
    with pytest.raises(RuntimeError, match="provider failed"):
        provider.compose_result(
            messages=[HumanMessage(content="Question")],
            route=RouteDecision(label="general_assistant", explanation="question"),
            guidance="answer",
        )


@pytest.mark.parametrize("transport", ["runs", "runs/stream"])
def test_real_graph_followup_receives_prior_evidence_without_public_contract_change(
    monkeypatch, transport
):
    client = _client(monkeypatch)
    user = _signup_login(client, "graph-evidence@example.com")
    cid = client.post("/conversations", json={"title": "Decision continuity"}).json()["id"]
    fake = FakeChatModel()
    provider = OpenAIResponseProvider(Settings(_env_file=None), chat_model=fake)
    monkeypatch.setattr(graph, "get_response_provider", lambda *args: provider)
    for message in (
        "Do not use documents. Help me plan a small application.",
        "Why did you choose that approach?",
    ):
        response = client.post(f"/conversations/{cid}/{transport}", json={"message": message})
        assert response.status_code == 200
        assert "session_evidence" not in response.text
    assert len(fake.calls) == 2
    assert "reported_source_selection_reason" in str(fake.calls[-1])
    assert "LangChain OpenAI drafted reply." in str(fake.calls[-1])
    gen = get_database_session()
    db = next(gen)
    rows = db.scalars(select(AgentRunModel).where(AgentRunModel.conversation_id == cid)).all()
    assert len(rows) == 2
    assert all(
        row.status == "completed" and row.user_id == user and row.session_evidence_json
        for row in rows
    )
    assert all("snippet" not in (row.context_delivery_json or "") for row in rows)
    assert "session_evidence" not in json.dumps(client.get("/openapi.json").json())
    gen.close()


def test_continuity_off_disables_capture_and_recall(session_case, monkeypatch):
    c = session_case
    monkeypatch.setenv("MY_AGENTS_CONTEXT_CONTINUITY_ENABLED", "false")
    get_settings.cache_clear()
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(_capture(c.doc_id))
    assert c.prior.session_evidence_json is None
    assert _recall(c) == ""


def test_authorization_recheck_preserves_uncommitted_transaction(session_case):
    c = session_case
    c.current.document_retrieval_priority = "always"
    c.db.flush()
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(_capture(c.doc_id))
    c.db.commit()
    c.db.expire_all()
    assert c.current.document_retrieval_priority == "always"
    assert c.db.get_bind().hide_parameters is True


def test_real_document_excerpt_is_recorded_at_answer_boundary(monkeypatch):
    client = _client(monkeypatch)
    _signup_login(client, "document-evidence@example.com")
    cid = client.post("/conversations", json={"title": "Document history"}).json()["id"]
    document = _create_document(
        client,
        json={
            "title": "LangGraph Agent Basics",
            "content": (
                "State design principles: store downstream, audit and recovery values. "
                "Use messages with add_messages. Use explicit next_step for routing."
            ),
        },
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
    fake = FakeChatModel()
    provider = OpenAIResponseProvider(Settings(_env_file=None), chat_model=fake)
    monkeypatch.setattr(graph, "get_response_provider", lambda *args: provider)
    response = client.post(
        f"/conversations/{cid}/runs",
        json={"message": "내 문서 LangGraph Agent Basics에서 state design principles를 찾아줘"},
    )
    assert response.status_code == 200
    gen = get_database_session()
    db = next(gen)
    run = db.scalar(select(AgentRunModel).where(AgentRunModel.conversation_id == cid))
    evidence = AnswerEvidence.model_validate_json(run.session_evidence_json)
    assert evidence.documents and evidence.documents[0].document_id == document["id"]
    assert "explicit next_step" in evidence.documents[0].snippet
    assert evidence.source_selection == "knowledge_base"
    assert evidence.source_selection_reason
    gen.close()


def test_durable_worker_records_and_recalls_across_database_sessions(monkeypatch, tmp_path):
    from uuid import uuid4

    from my_agents.answer_worker import process_once
    from my_agents.conversations.durable_store import record_worker_heartbeat

    monkeypatch.setenv(
        "MY_AGENTS_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'evidence.sqlite'}"
    )
    monkeypatch.setenv("MY_AGENTS_DURABLE_RUNS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    client = _client(monkeypatch)
    _signup_login(client, "durable-evidence@example.com")
    cid = client.post("/conversations", json={"title": "Durable evidence"}).json()["id"]
    settings = get_settings()
    record_worker_heartbeat(settings.database_url, "evidence-test-worker")
    fake = FakeChatModel()
    provider = OpenAIResponseProvider(Settings(_env_file=None), chat_model=fake)
    monkeypatch.setattr(graph, "get_response_provider", lambda *args: provider)
    for message in ("Do not use documents. Help me plan.", "Why did you choose that approach?"):
        admitted = client.post(
            f"/conversations/{cid}/runs/async",
            json={"message": message, "client_request_id": str(uuid4())},
        )
        assert admitted.status_code == 202
        assert process_once(settings, graph=graph.build_graph())
        output = client.get(f"/conversations/{cid}/runs/{admitted.json()['run_id']}/output")
        assert output.status_code == 200 and output.json()["status"] == "completed"
        assert "session_evidence" not in output.text
    assert len(fake.calls) == 2
    assert "reported_source_selection_reason" in str(fake.calls[-1])


def test_workspace_capture_uses_packed_text_and_preserves_whitespace():
    from my_agents.document_workspace.service import _document_workspace_prompt

    captured = []
    _document_workspace_prompt(
        messages=[HumanMessage(content="Read the document")],
        route=RouteDecision(label="general_assistant", explanation="read"),
        guidance="Read",
        capability=None,
        retrieved_context=[{"document_id": "a", "snippet": "  literal\n"}],
        memory_context=[],
        source_conflicts=[],
        answer_mode="document_grounded",
        attachments=[],
        capture=captured,
    )
    assert captured[0].boundary == "workspace"
    assert captured[0].documents[0].snippet == "  literal\n"


@pytest.mark.parametrize("corruption", ["source_link", "snippet"])
def test_inconsistent_evidence_is_withheld(session_case, corruption):
    c = session_case
    data = _capture(c.doc_id).model_dump()
    if corruption == "source_link":
        data["source_document_ids"] = []
    else:
        data["documents"][0]["snippet"] = "later fabricated content"
    c.prior.session_evidence_json = json.dumps(data)
    c.db.commit()
    assert _recall(c) == ""


def test_document_revocation_also_masks_answers_derived_from_historical_evidence(session_case):
    from my_agents.conversations.continuity import hidden_source_message_ids

    c = session_case
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(_capture(c.doc_id))
    c.db.commit()
    runtime = SessionEvidenceRuntime(c.db, c.user_id, c.current.id)
    messages = runtime.messages_for_answer([HumanMessage(content=c.followup.content)])
    evidence = capture_answer_input(
        messages=messages, documents=[], memory_count=0, input_truncated=False
    )
    assert evidence.documents == []  # No new retrieval; dependencies still follow the prior turn.
    assert evidence.source_document_ids == [c.doc_id]
    assert evidence.historical_run_ids == [c.prior.id]
    runtime.record(evidence)
    derived = MessageModel(
        conversation_id=c.cid,
        role="assistant",
        content="A claim based on the previous document excerpt.",
    )
    c.db.add(derived)
    c.db.flush()
    c.current.assistant_message_id = derived.id
    c.current.status = "completed"
    c.db.commit()
    c.db.delete(c.db.get(DocumentModel, c.doc_id))
    c.db.commit()
    hidden = hidden_source_message_ids(c.db, c.cid)
    assert {c.answer.id, derived.id} <= hidden


def test_removed_attachment_dependency_survives_a_text_only_followup(session_case):
    from my_agents.conversations.continuity import hidden_source_message_ids

    c = session_case
    evidence = capture_answer_input(
        messages=[], documents=[], memory_count=0, input_truncated=False
    )
    evidence.source_attachment_ids = ["removed-attachment"]
    SessionEvidenceRuntime(c.db, c.user_id, c.prior.id).record(evidence)
    c.db.commit()
    assert c.answer.id in hidden_source_message_ids(c.db, c.cid)
