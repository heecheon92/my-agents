"""Local-dev timing traces for document upload and ingestion runs."""

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
class IngestionPhaseTiming:
    """One redacted phase row in a local ingestion timing panel."""

    phase: str
    elapsed_ms: float
    calls: int = 1


@dataclass
class IngestionTimingTrace:
    """Collect one redacted upload or ingestion timing timeline."""

    enabled: bool
    trace: str
    _started: float = field(default_factory=perf_counter)
    _phases: list[IngestionPhaseTiming] = field(default_factory=list)
    _summary: dict[str, object] = field(default_factory=dict)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Record one named ingestion phase when timing trace output is enabled."""
        if not self.enabled:
            yield
            return
        started = perf_counter()
        try:
            yield
        finally:
            self.record_phase(name, (perf_counter() - started) * 1000)

    def record_phase(self, name: str, elapsed_ms: float) -> None:
        """Record or aggregate one redacted phase by display name."""
        if not self.enabled:
            return
        rounded_ms = round(elapsed_ms, 3)
        for phase in self._phases:
            if phase.phase == name:
                phase.elapsed_ms = round(phase.elapsed_ms + rounded_ms, 3)
                phase.calls += 1
                return
        self._phases.append(IngestionPhaseTiming(phase=name, elapsed_ms=rounded_ms))

    def update(self, **values: object) -> None:
        """Attach redacted counts, source metadata, and outcome fields."""
        if not self.enabled:
            return
        self._summary.update({key: value for key, value in values.items() if value is not None})

    def emit(self, *, outcome: str = "completed", **values: object) -> None:
        """Print one compact Rich trace for local ingestion profiling."""
        if not self.enabled:
            return
        self.update(**values)
        self._summary["outcome"] = outcome
        self._summary["trace"] = self.trace
        self._summary["total_ms"] = round((perf_counter() - self._started) * 1000, 3)
        rich_print(
            Panel(
                Group(
                    _summary_table(self._summary),
                    _phase_table(self._phases),
                ),
                title="[bold yellow]Knowledge ingestion timing[/bold yellow]",
                border_style="yellow",
            )
        )


def _summary_table(summary: dict[str, object]) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    for key in (
        "outcome",
        "trace",
        "total_ms",
        "filename_suffix",
        "content_type",
        "source_type",
        "parser",
        "source_bytes",
        "content_chars",
        "page_count",
        "pdf_doc_type",
        "pdf_native_page_count",
        "pdf_empty_page_count",
        "pdf_warning_count",
        "chunk_count",
        "embedding_count",
        "embedding_provider",
        "metadata_generation",
        "entity_count",
        "relationship_count",
        "structured_entity_count",
        "metadata_profile_count",
        "error_type",
    ):
        if key in summary:
            table.add_row(key, str(summary[key]))
    return table


def _phase_table(phases: list[IngestionPhaseTiming]) -> Table:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Phase")
    table.add_column("Calls", justify="right")
    table.add_column("Elapsed ms", justify="right")
    if not phases:
        table.add_row("none", "0", "0")
        return table
    for phase in phases:
        table.add_row(phase.phase, str(phase.calls), str(phase.elapsed_ms))
    return table
