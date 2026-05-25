"""Source Warden role for ContextForge."""

from __future__ import annotations

from my_agents.knowledge.auth import KnowledgeBaseSelectionContext


class SourceWarden:
    """Expose the resolved retrieval boundary without weakening authorization."""

    def knowledge_base_ids(
        self,
        selection_context: KnowledgeBaseSelectionContext,
    ) -> tuple[str, ...] | None:
        return selection_context.retrieval_knowledge_base_ids
