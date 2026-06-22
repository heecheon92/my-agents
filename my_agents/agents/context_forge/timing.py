"""Local-dev timing traces for ContextForge retrieval attempts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter

from rich import print as rich_print
from rich.console import Group
from rich.panel import Panel
from rich.table import Table


@dataclass
class RetrievalTimingTrace:
    """Collect one redacted per-attempt retrieval timing timeline."""

    enabled: bool
    _started: float = field(default_factory=perf_counter)
    _phases: list[dict[str, float | str]] = field(default_factory=list)
    _summary: dict[str, object] = field(default_factory=dict)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Record one named retrieval phase when timing trace output is enabled."""
        if not self.enabled:
            yield
            return
        started = perf_counter()
        try:
            yield
        finally:
            self._phases.append(
                {
                    "phase": name,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                }
            )

    def update(self, **values: object) -> None:
        """Attach redacted counts, route metadata, and outcome fields."""
        if not self.enabled:
            return
        self._summary.update(values)

    def emit(self, *, outcome: str = "completed", **values: object) -> None:
        """Print one compact Rich trace for local retrieval profiling."""
        if not self.enabled:
            return
        self.update(**values)
        self._summary["outcome"] = outcome
        self._summary["total_ms"] = round((perf_counter() - self._started) * 1000, 3)
        rich_print(
            Panel(
                Group(
                    _summary_table(self._summary),
                    _phase_table(self._phases),
                ),
                title="[bold yellow]ContextForge timing[/bold yellow]",
                border_style="yellow",
            )
        )


def _summary_table(summary: dict[str, object]) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    for key in (
        "outcome",
        "total_ms",
        "retrieval_latency_ms",
        "route",
        "answer_mode",
        "intent",
        "reranker",
        "authorized_document_count",
        "raw_candidate_count",
        "fused_candidate_count",
        "reranked_candidate_count",
        "injected_count",
        "rejected_count",
        "budget_truncated",
    ):
        if key in summary:
            table.add_row(key, str(summary[key]))
    return table


def _phase_table(phases: list[dict[str, float | str]]) -> Table:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Phase")
    table.add_column("Elapsed ms", justify="right")
    if not phases:
        table.add_row("none", "0")
        return table
    for phase in phases:
        table.add_row(str(phase["phase"]), str(phase["elapsed_ms"]))
    return table
