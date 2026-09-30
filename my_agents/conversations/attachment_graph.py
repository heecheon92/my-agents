"""Run-scoped semantic attachment selection with no runtime objects in state."""

from uuid import uuid4

from langgraph.types import interrupt


def resolve_attachments(state, runtime):  # noqa: ANN001, ANN201
    context = runtime.context or {}
    coordinator = context.get("attachment_recall_runtime")
    if coordinator is None:
        return {}
    return coordinator.resolve(state, context)


def request_attachments(state, runtime):  # noqa: ANN001, ANN201
    context = runtime.context or {}
    options = state.get("attachment_selection_options", [])
    answer = interrupt(
        {
            "schema_version": 2,
            "type": "attachment_selection",
            "interaction_id": str(uuid4()),
            "reason_code": "ambiguous_attachment_reference",
            "message_key": "clarification.attachment_scope.select_source",
            "option_count": len(options),
            "options": options,
            "access": state.get("attachment_selection_access", "original"),
        }
    )
    return context["attachment_recall_runtime"].resolve(state, context, answer["attachment_ids"])
