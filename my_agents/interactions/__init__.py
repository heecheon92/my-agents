"""Protocol-neutral contracts for agent-requested user interactions."""

from my_agents.interactions.schemas import (
    INTERACTION_SCHEMA_VERSION,
    ConversationRunResumeRequest,
    DocumentSelectionOption,
    DocumentSelectionOptionsResponse,
    InteractionReference,
    InteractionSchemaVersion,
    InteractionType,
    PendingDocumentSelection,
    PendingInteraction,
)

__all__ = [
    "INTERACTION_SCHEMA_VERSION",
    "ConversationRunResumeRequest",
    "DocumentSelectionOption",
    "DocumentSelectionOptionsResponse",
    "InteractionReference",
    "InteractionSchemaVersion",
    "InteractionType",
    "PendingDocumentSelection",
    "PendingInteraction",
]
