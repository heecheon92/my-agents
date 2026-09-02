"""OpenAPI contract tests for named conversation SSE events."""

from my_agents.api import create_app


def test_reasoning_summary_delta_schema_is_published_for_every_stream_route() -> None:
    openapi = create_app().openapi()
    paths = (
        "/conversations/{conversation_id}/runs/stream",
        "/conversations/{conversation_id}/runs/{run_id}/resume/stream",
        "/conversations/{conversation_id}/messages/{message_id}/replay/stream",
    )

    for path in paths:
        media = openapi["paths"][path]["post"]["responses"]["200"]["content"]["text/event-stream"]
        assert media["schema"] == {"type": "string"}
        event = media["x-sse-events"]["reasoning_summary_delta"]
        schema = event["schema"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["stage", "delta", "sequence"]
        assert schema["properties"]["stage"]["enum"] == [
            "retrieval_planning",
            "answer_synthesis",
        ]
        assert schema["properties"]["delta"] == {
            "minLength": 1,
            "title": "Delta",
            "type": "string",
        }
        assert schema["properties"]["sequence"]["minimum"] == 1
