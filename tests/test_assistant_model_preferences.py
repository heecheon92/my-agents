"""Persisted model selection, guest enforcement, and pinned execution stay offline."""

import pytest

from my_agents.auth.models import UserModel
from my_agents.model_defaults import (
    EXPOSED_ASSISTANT_MODELS,
    MODEL_DEFAULT_REASONING_EFFORT,
    SUPPORTED_ASSISTANT_MODELS,
    validate_exposed_assistant_models,
)
from my_agents.persistence.database import get_database_session

from .test_conversations_api import (
    SpyGraph,
    StreamingSpyGraph,
    _assistant_message_id,
    _client,
    _parse_sse,
    _signup_login,
)
from .test_guest_access_api import _client as guest_client
from .test_guest_access_api import _guest_login


class ModelSpyGraph(SpyGraph):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def invoke(self, input, **kwargs):
        self.contexts.append(kwargs.get("context", {}))
        return super().invoke(input, **kwargs)


def test_exposed_assistant_models_are_a_subset_of_supported_models():
    assert set(EXPOSED_ASSISTANT_MODELS) <= set(SUPPORTED_ASSISTANT_MODELS)
    validate_exposed_assistant_models(SUPPORTED_ASSISTANT_MODELS, EXPOSED_ASSISTANT_MODELS)
    with pytest.raises(ValueError, match="Exposed assistant models must be supported"):
        validate_exposed_assistant_models(SUPPORTED_ASSISTANT_MODELS, ("gpt-unknown",))


class ModelStreamingSpyGraph(StreamingSpyGraph):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def stream(self, input, **kwargs):
        self.contexts.append(kwargs.get("context", {}))
        yield from super().stream(input, **kwargs)


def test_preference_endpoints_require_authentication(monkeypatch):
    client = _client(monkeypatch)
    for path in ("/assistant/preferences", "/capabilities/assistant-models"):
        assert client.get(path).status_code == 401
    assert (
        client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-astra"}).status_code
        == 401
    )


@pytest.mark.parametrize("model", SUPPORTED_ASSISTANT_MODELS)
def test_registered_model_preference_persists_and_drives_run(monkeypatch, model):
    graph = ModelSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "models@example.com")
    catalog = client.get("/capabilities/assistant-models").json()
    assert catalog["customizable"]
    assert [item["id"] for item in catalog["models"]] == list(EXPOSED_ASSISTANT_MODELS)
    assert all(item["pro_supported"] for item in catalog["models"])
    response = client.patch("/assistant/preferences", json={"assistant_model": model})
    assert response.status_code == 200
    assert response.json()["selected_model"] == response.json()["effective_model"] == model
    assert client.get("/assistant/preferences").json() == response.json()
    conversation_id = client.post("/conversations", json={"title": "Model"}).json()["id"]
    run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Hello", "reasoning_mode": "pro", "reasoning_effort": "none"},
    )
    assert run.status_code == 200
    data = run.json()
    effective = "low" if model in {"gpt-6-astra", "gpt-6.1-sol"} else "none"
    assert data["assistant_model"] == model
    assert data["reasoning_effort"] == effective
    assert graph.contexts[-1]["assistant_model"] == model
    assert graph.contexts[-1]["reasoning_mode"] == "pro"
    events = client.get(f"/conversations/{conversation_id}/runs/{data['run_id']}/events").json()
    started = next(event["payload"] for event in events if event["event_type"] == "run_started")
    assert started["assistant_model"] == model
    assert started["reasoning_effort"] == effective
    assert (
        client.get(f"/conversations/{conversation_id}/runs").json()[0]["assistant_model"] == model
    )
    assert (
        client.get(f"/conversations/{conversation_id}/runs/{data['run_id']}").json()[
            "assistant_model"
        ]
        == model
    )


def test_preference_reset_and_user_isolation(monkeypatch):
    client = _client(monkeypatch, ModelSpyGraph())
    _signup_login(client, "model-one@example.com")
    assert (
        client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-astra"}).status_code
        == 200
    )
    other = _client(monkeypatch, ModelSpyGraph())
    _signup_login(other, "model-two@example.com")
    assert other.get("/assistant/preferences").json()["selected_model"] is None
    assert client.get("/assistant/preferences").json()["selected_model"] == "gpt-6-astra"
    reset = client.patch("/assistant/preferences", json={"assistant_model": None})
    assert reset.status_code == 200
    assert reset.json()["selected_model"] is None
    assert reset.json()["effective_model"] == reset.json()["default_model"]


def test_frontend_catalog_is_curated_without_rejecting_or_resetting_api_models(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_OPENAI_MODEL", "gpt-6-sol")
    client = _client(monkeypatch, ModelSpyGraph())
    _signup_login(client, "curated-models@example.com")
    catalog = client.get("/capabilities/assistant-models").json()
    assert [item["id"] for item in catalog["models"]] == [
        "gpt-6.1-sol",
        "gpt-6-luna",
        "gpt-6-astra",
    ]
    assert catalog["default_model"] == "gpt-6-sol"
    before = client.get("/assistant/preferences").json()
    assert before["selected_model"] is None and before["effective_model"] == "gpt-6-sol"
    saved = client.patch("/assistant/preferences", json={"assistant_model": "gpt-5.6-luna"})
    assert saved.status_code == 200
    assert saved.json()["effective_model"] == "gpt-5.6-luna"
    assert client.get("/capabilities/assistant-models").json()["models"] == catalog["models"]
    assert client.get("/assistant/preferences").json()["selected_model"] == "gpt-5.6-luna"
    assert client.get("/capabilities/reasoning").status_code == 200
    reset = client.patch("/assistant/preferences", json={"assistant_model": None}).json()
    assert reset["effective_model"] == "gpt-6-sol" and reset["selected_model"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"assistant_model": "gpt-unknown"},
        {"assistant_model": "gpt-6-astra", "user_id": "other"},
        {},
    ],
)
def test_invalid_preference_cannot_change_stored_model(monkeypatch, body):
    client = _client(monkeypatch)
    _signup_login(client, "invalid-model@example.com")
    assert client.patch("/assistant/preferences", json=body).status_code == 422
    assert client.get("/assistant/preferences").json()["selected_model"] is None


@pytest.mark.parametrize("deployment_model", ["gpt-5.6-sol", "gpt-6-astra"])
def test_guest_model_is_locked_even_if_a_preference_is_present_in_storage(
    monkeypatch, deployment_model
):
    monkeypatch.setenv("MY_AGENTS_OPENAI_MODEL", deployment_model)
    graph = ModelSpyGraph()
    client = guest_client(monkeypatch, graph=graph)
    login = _guest_login(client)
    sessions = get_database_session()
    db = next(sessions)
    try:
        user = db.get(UserModel, login["user"]["id"])
        user.assistant_model_preference = "gpt-6-astra"
        db.commit()
    finally:
        sessions.close()
    prefs = client.get("/assistant/preferences").json()
    assert prefs["customizable"] is False and prefs["selected_model"] is None
    assert prefs["effective_model"] == prefs["default_model"] == "gpt-6-luna"
    assert client.get("/capabilities/assistant-models").json()["default_model"] == "gpt-6-luna"
    assert (
        client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-astra"}).status_code
        == 403
    )
    conversation_id = client.post("/conversations", json={"title": "Guest"}).json()["id"]
    run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Hello", "reasoning_mode": "pro", "reasoning_effort": "max"},
    )
    assert run.status_code == 200
    assert run.json()["assistant_model"] == "gpt-6-luna"
    assert graph.contexts[-1]["assistant_model"] == "gpt-6-luna"
    assert run.json()["reasoning_mode"] == "standard"
    assert run.json()["reasoning_effort"] == "medium"


def test_selected_model_default_is_application_policy_and_is_reported_in_capabilities(monkeypatch):
    monkeypatch.setitem(MODEL_DEFAULT_REASONING_EFFORT, "gpt-6-astra", "high")
    client = _client(monkeypatch, ModelSpyGraph())
    _signup_login(client, "model-default@example.com")
    client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-astra"})
    assert client.get("/capabilities/reasoning").json()["default_effort"] == "high"
    catalog = client.get("/capabilities/assistant-models").json()
    assert (
        next(item for item in catalog["models"] if item["id"] == "gpt-6-astra")[
            "default_reasoning_effort"
        ]
        == "high"
    )
    conversation_id = client.post("/conversations", json={"title": "Default"}).json()["id"]
    run = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Hello"})
    assert run.json()["reasoning_effort"] == "high"


def test_preference_changes_keep_existing_run_pinned_and_replay_uses_current_model(monkeypatch):
    graph = ModelSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "model-replay@example.com")
    client.patch("/assistant/preferences", json={"assistant_model": "gpt-5.6-sol"})
    conversation_id = client.post("/conversations", json={"title": "Replay"}).json()["id"]
    original = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Hello", "reasoning_effort": "none"},
    ).json()
    client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-astra"})
    assert (
        client.get(f"/conversations/{conversation_id}/runs/{original['run_id']}").json()[
            "assistant_model"
        ]
        == "gpt-5.6-sol"
    )
    replay = client.post(
        f"/conversations/{conversation_id}/messages/{_assistant_message_id(original['run_id'])}/replay",
        json={},
    )
    assert replay.status_code == 200
    assert replay.json()["assistant_model"] == "gpt-6-astra"
    assert replay.json()["reasoning_effort"] == "low"
    assert graph.contexts[-1]["assistant_model"] == "gpt-6-astra"


def test_stream_and_replay_stream_use_selected_model_and_report_pinned_model(monkeypatch):
    graph = ModelStreamingSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "model-stream@example.com")
    client.patch("/assistant/preferences", json={"assistant_model": "gpt-6.1-sol"})
    conversation_id = client.post("/conversations", json={"title": "Stream"}).json()["id"]
    stream = client.post(f"/conversations/{conversation_id}/runs/stream", json={"message": "Hello"})
    assert stream.status_code == 200
    events = _parse_sse(stream.text)
    started = next(event["data"] for event in events if event["event"] == "run_started")
    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    assert started["assistant_model"] == completed["assistant_model"] == "gpt-6.1-sol"
    assert graph.contexts[-1]["assistant_model"] == "gpt-6.1-sol"
    client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-luna"})
    message_id = _assistant_message_id(completed["run_id"])
    replay = client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/replay/stream", json={}
    )
    assert replay.status_code == 200
    events = _parse_sse(replay.text)
    started = next(event["data"] for event in events if event["event"] == "run_started")
    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    assert started["assistant_model"] == completed["assistant_model"] == "gpt-6-luna"
    assert graph.contexts[-1]["assistant_model"] == "gpt-6-luna"


def test_guest_stream_and_replay_stay_on_luna_after_deployment_model_changes(monkeypatch):
    from my_agents.settings import get_settings

    monkeypatch.setenv("MY_AGENTS_OPENAI_MODEL", "gpt-5.6-sol")
    graph = ModelStreamingSpyGraph()
    client = guest_client(monkeypatch, graph=graph)
    _guest_login(client)
    conversation_id = client.post("/conversations", json={"title": "Guest stream"}).json()["id"]
    response = client.post(
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "Hello", "reasoning_mode": "pro", "reasoning_effort": "max"},
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    started = next(event["data"] for event in events if event["event"] == "run_started")
    assert started["assistant_model"] == completed["assistant_model"] == "gpt-6-luna"
    assert graph.contexts[-1]["assistant_model"] == "gpt-6-luna"
    monkeypatch.setenv("MY_AGENTS_OPENAI_MODEL", "gpt-6-astra")
    get_settings.cache_clear()
    message_id = _assistant_message_id(completed["run_id"])
    replay = client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/replay/stream", json={}
    )
    assert replay.status_code == 200
    replayed = next(
        event["data"] for event in _parse_sse(replay.text) if event["event"] == "run_completed"
    )
    assert replayed["assistant_model"] == "gpt-6-luna"
    assert graph.contexts[-1]["assistant_model"] == "gpt-6-luna"
    assert graph.contexts[-1]["reasoning_mode"] == "standard"


def test_guest_reasoning_without_db_uses_fixed_model_and_its_default(monkeypatch):
    from my_agents.api.reasoning import resolve_reasoning_preferences
    from my_agents.auth.contracts import Principal
    from my_agents.settings import Settings

    monkeypatch.setitem(MODEL_DEFAULT_REASONING_EFFORT, "gpt-6-astra", "high")
    settings = Settings(_env_file=None, MY_AGENTS_OPENAI_MODEL="gpt-6-astra")
    result = resolve_reasoning_preferences(
        settings=settings,
        principal=Principal(user_id="guest", session_id="session", is_guest=True),
        requested_mode="pro",
        requested_effort="max",
        uses_document_workspace=False,
    )
    assert result.model == "gpt-6-luna"
    assert result.mode == "standard"
    assert result.effort == "medium"


@pytest.mark.parametrize("guest_model", ["gpt-5.6-luna", "gpt-6.1-sol"])
def test_operator_guest_model_override_is_reported_and_does_not_change_registered_default(
    monkeypatch, guest_model
):
    monkeypatch.setenv("MY_AGENTS_OPENAI_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("MY_AGENTS_GUEST_ASSISTANT_MODEL", guest_model)
    graph = ModelSpyGraph()
    client = guest_client(monkeypatch, graph=graph)
    _guest_login(client)
    preferences = client.get("/assistant/preferences").json()
    assert preferences["default_model"] == preferences["effective_model"] == guest_model
    assert preferences["customizable"] is False
    assert client.get("/capabilities/assistant-models").json()["default_model"] == guest_model
    assert (
        client.patch("/assistant/preferences", json={"assistant_model": "gpt-6-astra"}).status_code
        == 403
    )
    conversation = client.post("/conversations", json={"title": "Guest override"}).json()["id"]
    response = client.post(
        f"/conversations/{conversation}/runs",
        json={"message": "Hello", "reasoning_mode": "pro", "reasoning_effort": "max"},
    )
    assert response.status_code == 200
    assert response.json()["assistant_model"] == guest_model
    assert response.json()["reasoning_mode"] == "standard"
    assert graph.contexts[-1]["assistant_model"] == guest_model
    registered = _client(monkeypatch, ModelSpyGraph())
    _signup_login(registered, "guest-model-operator-test@example.com")
    assert registered.get("/assistant/preferences").json()["effective_model"] == "gpt-5.6-sol"


@pytest.mark.parametrize("model", ["", "gpt-unknown"])
def test_invalid_guest_model_configuration_is_rejected(model):
    from pydantic import ValidationError

    from my_agents.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, MY_AGENTS_GUEST_ASSISTANT_MODEL=model)
