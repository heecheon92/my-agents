"""Debug-only Rich traces for ContextForge role handoffs."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from rich import print as rich_print

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
