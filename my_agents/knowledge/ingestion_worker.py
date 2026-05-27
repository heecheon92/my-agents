"""Durable worker loop for queued document ingestion runs."""

from __future__ import annotations

import argparse
import logging
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event

from sqlalchemy import select

from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import DocumentModel, ExtractionRunModel, ExtractionStatus
from my_agents.persistence.database import _sessionmaker_for_url, initialize_database
from my_agents.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionWorkerResult:
    """Outcome for one claimed extraction run."""

    run_id: str
    document_id: str | None
    status: str


def process_pending_extraction_runs_once(
    *,
    database_url: str,
    max_runs: int = 1,
) -> list[IngestionWorkerResult]:
    """Claim and process up to ``max_runs`` queued extraction runs."""
    results: list[IngestionWorkerResult] = []
    for _ in range(max_runs):
        run_id = claim_next_pending_extraction_run(database_url=database_url)
        if run_id is None:
            break
        results.append(execute_claimed_extraction_run(database_url=database_url, run_id=run_id))
    return results


def run_ingestion_worker(
    *,
    database_url: str,
    poll_interval_seconds: float,
    batch_size: int,
    stop_event: Event | None = None,
) -> None:
    """Continuously process queued extraction runs until stopped."""
    stop = stop_event or Event()
    logger.info(
        "ingestion_worker.started poll_interval_seconds=%s batch_size=%s",
        poll_interval_seconds,
        batch_size,
    )
    while not stop.is_set():
        results = process_pending_extraction_runs_once(
            database_url=database_url,
            max_runs=batch_size,
        )
        if not results:
            stop.wait(poll_interval_seconds)
    logger.info("ingestion_worker.stopped")


def claim_next_pending_extraction_run(*, database_url: str) -> str | None:
    """Atomically claim the oldest pending extraction run for this worker."""
    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        with db.begin():
            statement = (
                select(ExtractionRunModel)
                .where(ExtractionRunModel.status == ExtractionStatus.PENDING.value)
                .order_by(ExtractionRunModel.created_at, ExtractionRunModel.id)
                .limit(1)
            )
            if db.get_bind().dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            run = db.scalar(statement)
            if run is None:
                return None
            run.status = ExtractionStatus.RUNNING.value
            run.stage = "claimed"
            run.progress_percent = 1
            run.error = None
            run.started_at = run.started_at or datetime.now(UTC)
            run_id = run.id
        logger.info("ingestion_worker.claimed run_id=%s", run_id)
        return run_id


def execute_claimed_extraction_run(*, database_url: str, run_id: str) -> IngestionWorkerResult:
    """Execute one claimed extraction run with a fresh database session."""
    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        run = db.get(ExtractionRunModel, run_id)
        if run is None:
            logger.warning("ingestion_worker.missing_run run_id=%s", run_id)
            return IngestionWorkerResult(run_id=run_id, document_id=None, status="missing")
        document = db.get(DocumentModel, run.document_id)
        if document is None:
            run.status = ExtractionStatus.FAILED.value
            run.stage = "failed"
            run.error = "DocumentNotFound: document not found"
            run.completed_at = datetime.now(UTC)
            db.commit()
            logger.warning(
                "ingestion_worker.document_missing run_id=%s document_id=%s",
                run.id,
                run.document_id,
            )
            return IngestionWorkerResult(
                run_id=run.id,
                document_id=run.document_id,
                status=ExtractionStatus.FAILED.value,
            )
        try:
            KnowledgeExtractionService(db).ingest_document(document, run=run)
        except Exception:
            # KnowledgeExtractionService persists bounded failed status before re-raising.
            logger.exception(
                "ingestion_worker.failed run_id=%s document_id=%s",
                run.id,
                document.id,
            )
            return IngestionWorkerResult(
                run_id=run.id,
                document_id=document.id,
                status=ExtractionStatus.FAILED.value,
            )
        return IngestionWorkerResult(
            run_id=run.id,
            document_id=document.id,
            status=ExtractionStatus.COMPLETED.value,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ingestion worker CLI."""
    settings = get_settings()
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level))
    initialize_database(settings)
    if args.once:
        results = process_pending_extraction_runs_once(
            database_url=settings.database_url,
            max_runs=args.batch_size,
        )
        return 0 if results else 2
    stop_event = Event()

    def stop(_signum, _frame) -> None:  # noqa: ANN001
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run_ingestion_worker(
        database_url=settings.database_url,
        poll_interval_seconds=args.poll_interval_seconds,
        batch_size=args.batch_size,
        stop_event=stop_event,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process queued document ingestion runs.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="process one batch and exit with 2 when no queued runs exist",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=get_settings().ingestion_worker_poll_interval_seconds,
        help="seconds to wait between empty queue polls",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=get_settings().ingestion_worker_batch_size,
        help="maximum queued runs to process per poll",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
