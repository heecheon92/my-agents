"""Ephemeral OpenAI-hosted document workspace capability."""

from my_agents.document_workspace.provider import (
    DocumentWorkspaceProvider,
    OpenAIDocumentWorkspaceProvider,
)

__all__ = ["DocumentWorkspaceProvider", "OpenAIDocumentWorkspaceProvider"]
