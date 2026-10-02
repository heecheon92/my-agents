"""Debug-only Rich traces for ContextForge role handoffs."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from rich import print as rich_print
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from my_agents.agents.context_forge.contracts import RetrievalCandidate
from my_agents.settings import DeploymentEnvironment

logger = logging.getLogger(__name__)


def debug_agent_turn(
    *,
    sender: str,
    receiver: str,
    message: str,
    payload: Mapping[str, object],
) -> None:
    """Print a sensitive debug trace for one ContextForge role handoff when enabled."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    rich_print(
        f"[bold magenta]ContextForge turn[/bold magenta] "
        f"[cyan]{sender}[/cyan] -> [green]{receiver}[/green]\n"
        f"[bold]message:[/bold] {message}",
        dict(payload),
    )


def debug_reranking_comparison(
    *,
    environment: DeploymentEnvironment,
    enabled: bool,
    query: str,
    reranker: str,
    before: Sequence[RetrievalCandidate],
    after: Sequence[RetrievalCandidate],
) -> None:
    """Print bounded sensitive ranking details only in opted-in local development."""
    if environment != "local" or not enabled or not before:
        return
    before_ranks = {item.chunk.chunk.id: rank for rank, item in enumerate(before, 1)}
    after_ranks = {item.chunk.chunk.id: rank for rank, item in enumerate(after, 1)}
    # Show promotions and demotions without dumping the entire retrieved corpus.
    visible_ids = {item.chunk.chunk.id for item in (*before[:10], *after[:10])}
    table = Table(expand=True)
    for column in ("Before", "After", "Document", "Chunk", "RRF", "Rerank", "Excerpt"):
        table.add_column(column)
    for candidate in after:
        chunk = candidate.chunk.chunk
        if chunk.id not in visible_ids:
            continue
        document = candidate.chunk.document
        excerpt = " ".join(chunk.content.split())
        if len(excerpt) > 160:
            excerpt = excerpt[:160] + "…"
        location = f"{chunk.id[:8]} / #{chunk.ordinal}"
        if chunk.source_page is not None:
            location += f" / p{chunk.source_page}"
        table.add_row(
            str(before_ranks[chunk.id]),
            str(after_ranks[chunk.id]),
            Text((document.source_filename or document.title)[:80]),
            Text(location),
            f"{candidate.score:.6f}",
            "—" if candidate.rerank_score is None else f"{candidate.rerank_score:.3f}",
            Text(excerpt),
        )
    rich_print(
        Panel(
            Group(
                Text(
                    f"reranker={reranker} · candidates={len(before)} · showing top 10 of each order"
                ),
                Text("query: " + query[:240]),
                table,
            ),
            title="ContextForge reranking comparison (local dev)",
            border_style="cyan",
        )
    )
