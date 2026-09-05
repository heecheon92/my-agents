"""A stale preflight check still produces HTTP 409 before opening SSE."""

import pytest

from .test_conversations_api import SpyGraph, _client, _create_running_run, _signup_login


@pytest.mark.parametrize("streaming", [False, True])
def test_database_conflict_rejects_before_provider_or_stream_headers(monkeypatch, streaming):
    from my_agents.api.conversations.endpoints import runs, stream

    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    user_id = _signup_login(client, "admission-http@example.com")
    conversation_id = client.post("/conversations", json={"title": "Race"}).json()["id"]
    _create_running_run(conversation_id=conversation_id, user_id=user_id)
    monkeypatch.setattr(stream if streaming else runs, "assert_no_active_run", lambda *_: None)
    suffix = "/stream" if streaming else ""
    response = client.post(
        f"/conversations/{conversation_id}/runs{suffix}", json={"message": "Rejected"}
    )
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/json"
    assert response.json()["code"] == "conversation_run_already_active"
    assert graph.calls == []
    assert client.get(f"/conversations/{conversation_id}/messages").json() == []
