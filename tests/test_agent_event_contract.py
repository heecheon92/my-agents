"""Display-safe persisted conversation-event contract tests."""

import json

from my_agents.api.conversations.run_events import event_response
from my_agents.conversations.models import AgentEventModel, AgentEventType


def test_event_response_strips_uncontracted_and_nested_unsafe_fields() -> None:
    event = AgentEventModel(
        id="event-1",
        run_id="run-1",
        sequence=1,
        event_type=AgentEventType.GRAPH_INVOKED.value,
        payload_json=json.dumps(
            {
                "route_label": "general_assistant",
                "message_count": 1,
                "retrieved_chunk_count": 0,
                "prompt": "must not escape storage",
                "provider_trace": {"secret": "must not escape storage"},
                "agent_trace": [
                    {
                        "id": "assistant_graph",
                        "event_type": "graph_invoked",
                        "status": "completed",
                        "title": {"en": "Assistant Graph", "ko": "어시스턴트 그래프"},
                        "description": {"en": "Safe", "ko": "안전"},
                        "evidence": {
                            "route_label": "general_assistant",
                            "prompt": "must not escape nested evidence",
                            "credentials": "must not escape nested evidence",
                        },
                    }
                ],
            }
        ),
    )

    payload = event_response(event).model_dump(mode="json", exclude_none=True)["payload"]

    assert payload["route_label"] == "general_assistant"
    assert payload["agent_trace"][0]["evidence"] == {"route_label": "general_assistant"}
    serialized = json.dumps(payload)
    assert "must not escape" not in serialized
    assert "provider_trace" not in serialized
