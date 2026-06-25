"""Measure document ingestion performance without changing ingestion behavior.

The benchmark intentionally runs against the application ingestion services instead of a
reimplemented hot path. It creates an isolated SQLite database, ingests one synthetic
document, runs a tiny retrieval smoke, and prints redacted JSON suitable for before/after
optimization comparisons.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import textwrap
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import psutil
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from my_agents.auth.models import UserModel
from my_agents.knowledge.extraction import KnowledgeExtractionService
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    DocumentParseArtifactModel,
    EntityMentionModel,
    EntityModel,
    EntityRelationshipModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
    KnowledgeBasePurpose,
    StructuredKnowledgeEntityModel,
)
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.knowledge.uploads import ParsedDocumentUpload, parse_uploaded_document
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import get_settings


@dataclass(frozen=True)
class IngestionBenchmarkRun:
    scenario: str
    iteration: int
    parser_name: str | None
    source_type: str
    content_chars: int
    source_bytes: int
    page_count: int | None
    parse_ms: float
    persist_ms: float
    ingest_ms: float
    retrieval_ms: float
    total_ms: float
    rss_before_mb: float
    rss_after_mb: float
    rss_delta_mb: float
    chunk_count: int
    extraction_run_count: int
    entity_count: int
    entity_mention_count: int
    relationship_count: int
    structured_entity_count: int
    metadata_profile_count: int
    retrieval_hit_count: int
    retrieval_top_source: str | None
    retrieval_top_score: float | None
    quality_signature: dict[str, object]


def main() -> int:
    args = _build_parser().parse_args()
    runs = [
        _run_once(
            scenario=args.scenario,
            iteration=iteration,
            repeat_units=args.repeat_units,
            retrieval_query=args.retrieval_query,
        )
        for iteration in range(1, args.repeat + 1)
    ]
    payload = {
        "benchmark": "ingestion_performance_v1",
        "scenario": args.scenario,
        "repeat": args.repeat,
        "repeat_units": args.repeat_units,
        "retrieval_query": args.retrieval_query,
        "summary": _summary(runs),
        "runs": [asdict(run) for run in runs],
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


def _run_once(
    *,
    scenario: str,
    iteration: int,
    repeat_units: int,
    retrieval_query: str,
) -> IngestionBenchmarkRun:
    with tempfile.TemporaryDirectory(prefix="my-agents-ingestion-bench-") as tmpdir:
        database_url = f"sqlite+pysqlite:///{Path(tmpdir) / 'bench.sqlite3'}"
        _configure_isolated_environment(database_url)
        settings = get_settings()
        initialize_database(settings)
        session_factory = _sessionmaker_for_url(database_url)
        process = psutil.Process()
        with session_factory() as db:
            user = UserModel(
                id=str(uuid.uuid4()),
                email=f"bench-{uuid.uuid4().hex}@example.com",
                nickname="Benchmark",
                password_hash="not-used",
                email_verified_at=datetime.now(UTC),
                approval_status="approved",
                approved_at=datetime.now(UTC),
            )
            kb = KnowledgeBaseModel(
                name="Benchmark KB",
                scope="personal",
                owner_user_id=user.id,
                purpose=KnowledgeBasePurpose.STANDARD.value,
            )
            db.add_all([user, kb])
            db.commit()
            db.refresh(kb)

            rss_before_mb = _rss_mb(process)
            total_started = perf_counter()
            parse_started = perf_counter()
            parsed = _parsed_document_for_scenario(scenario=scenario, repeat_units=repeat_units)
            parse_ms = _elapsed_ms(parse_started)

            persist_started = perf_counter()
            document = _persist_document(
                db=db,
                user_id=user.id,
                knowledge_base_id=kb.id,
                parsed=parsed,
                title=f"{scenario.title()} Benchmark {iteration}",
            )
            persist_ms = _elapsed_ms(persist_started)

            ingest_started = perf_counter()
            summary = KnowledgeExtractionService(db).ingest_document(document)
            ingest_ms = _elapsed_ms(ingest_started)

            retrieval_started = perf_counter()
            retrieved = RetrievalService(db).retrieve_scoped(
                user_id=user.id,
                query=retrieval_query,
                limit=5,
                knowledge_base_ids=[kb.id],
            )
            retrieval_ms = _elapsed_ms(retrieval_started)
            total_ms = _elapsed_ms(total_started)
            rss_after_mb = _rss_mb(process)

            counts = _artifact_counts(db=db, document_id=document.id)
            top = retrieved[0] if retrieved else None
            return IngestionBenchmarkRun(
                scenario=scenario,
                iteration=iteration,
                parser_name=document.parser_name,
                source_type=document.source_type,
                content_chars=len(document.content),
                source_bytes=document.source_byte_size or 0,
                page_count=document.source_page_count,
                parse_ms=parse_ms,
                persist_ms=persist_ms,
                ingest_ms=ingest_ms,
                retrieval_ms=retrieval_ms,
                total_ms=total_ms,
                rss_before_mb=rss_before_mb,
                rss_after_mb=rss_after_mb,
                rss_delta_mb=round(rss_after_mb - rss_before_mb, 3),
                chunk_count=summary.chunk_count,
                extraction_run_count=counts["extraction_runs"],
                entity_count=counts["entities"],
                entity_mention_count=counts["entity_mentions"],
                relationship_count=summary.relationship_count,
                structured_entity_count=counts["structured_entities"],
                metadata_profile_count=counts["metadata_profiles"],
                retrieval_hit_count=len(retrieved),
                retrieval_top_source=top.source if top else None,
                retrieval_top_score=round(top.score, 6) if top else None,
                quality_signature={
                    "parser_name": document.parser_name,
                    "source_type": document.source_type,
                    "chunk_count": summary.chunk_count,
                    "entity_count": summary.entity_count,
                    "relationship_count": summary.relationship_count,
                    "metadata_profile_count": counts["metadata_profiles"],
                    "retrieval_hit_count": len(retrieved),
                    "retrieval_top_source": top.source if top else None,
                },
            )


def _configure_isolated_environment(database_url: str) -> None:
    os.environ["MY_AGENTS_DATABASE_URL"] = database_url
    os.environ["MY_AGENTS_AUTO_CREATE_TABLES"] = "true"
    os.environ["MY_AGENTS_RESPONSE_MODE"] = "deterministic"
    os.environ["MY_AGENTS_EMBEDDING_MODE"] = "deterministic"
    os.environ["MY_AGENTS_DOCUMENT_METADATA_ENRICHMENT_MODE"] = "deterministic"
    os.environ["MY_AGENTS_SESSION_COOKIE_SECURE"] = "false"
    get_settings.cache_clear()
    reset_database_caches()


def _parsed_document_for_scenario(*, scenario: str, repeat_units: int) -> ParsedDocumentUpload:
    content = _scenario_text(repeat_units).encode("utf-8")
    if scenario == "text":
        return parse_uploaded_document(
            filename="benchmark.txt",
            content_type="text/plain",
            content=content,
        )
    if scenario == "markdown":
        return parse_uploaded_document(
            filename="benchmark.md",
            content_type="text/markdown",
            content=_scenario_markdown(repeat_units).encode("utf-8"),
        )
    if scenario == "pdf":
        return parse_uploaded_document(
            filename="benchmark.pdf",
            content_type="application/pdf",
            content=_scenario_pdf_bytes(repeat_units),
        )
    raise ValueError(f"unsupported scenario: {scenario}")


def _scenario_text(repeat_units: int) -> str:
    unit = (
        "LangGraph coordinates FastAPI ingestion for Alpha Contract Review. "
        "The retrieval smoke should find LangGraph, FastAPI, OpenAI, SQLite, "
        "and Render worker constraints. Section A explains upload parsing. "
        "Section B explains chunking, embeddings, entity extraction, metadata profiles, "
        "and citation-ready retrieval evidence.\n\n"
    )
    return unit * repeat_units


def _scenario_markdown(repeat_units: int) -> str:
    return "# Benchmark Ingestion\n\n" + _scenario_text(repeat_units)


def _scenario_pdf_bytes(repeat_units: int) -> bytes:
    import pymupdf

    document = pymupdf.open()
    wrapped_lines = textwrap.wrap(_scenario_text(repeat_units).replace("\n", " "), width=88)
    lines_per_page = 46
    pages = [
        wrapped_lines[index : index + lines_per_page]
        for index in range(0, len(wrapped_lines), lines_per_page)
    ]
    for index, lines in enumerate(pages or [["Empty benchmark page"]], start=1):
        page = document.new_page()
        y = 42
        page.insert_text((42, y), f"Benchmark Page {index}", fontsize=11)
        y += 18
        for line in lines:
            page.insert_text((42, y), line, fontsize=9)
            y += 14
    return bytes(document.write())


def _persist_document(
    *,
    db,
    user_id: str,
    knowledge_base_id: str,
    parsed: ParsedDocumentUpload,
    title: str,
) -> DocumentModel:
    document = DocumentModel(
        title=title,
        content=parsed.content,
        source_type=parsed.source_type,
        source_filename=f"benchmark.{_extension_for_source_type(parsed.source_type)}",
        source_content_type=parsed.source_content_type,
        source_byte_size=parsed.byte_size,
        source_sha256=parsed.sha256,
        source_page_count=parsed.page_count,
        parser_name=parsed.parser_name,
        owner_user_id=user_id,
        knowledge_base_id=knowledge_base_id,
    )
    db.add(document)
    db.flush()
    artifact = parsed.parse_artifact
    if artifact is not None:
        db.add(
            DocumentParseArtifactModel(
                document_id=document.id,
                source_sha256=parsed.sha256,
                source_filename=document.source_filename,
                source_content_type=parsed.source_content_type,
                source_type=parsed.source_type,
                parser_provider=artifact.parser_provider,
                parser_name=artifact.parser_name,
                parser_version=artifact.parser_version,
                parser_mode=artifact.parser_mode,
                markdown_content=artifact.markdown_content,
                elements_json=json.dumps(artifact.elements, ensure_ascii=False, sort_keys=True),
                warnings_json=json.dumps(artifact.warnings, ensure_ascii=False, sort_keys=True),
            )
        )
    db.commit()
    db.refresh(document)
    return document


def _artifact_counts(*, db, document_id: str) -> dict[str, int]:
    return {
        "chunks": _count(db, DocumentChunkModel, DocumentChunkModel.document_id == document_id),
        "extraction_runs": _count(
            db, ExtractionRunModel, ExtractionRunModel.document_id == document_id
        ),
        "entities": _count(db, EntityModel),
        "entity_mentions": _count(
            db, EntityMentionModel, EntityMentionModel.document_id == document_id
        ),
        "relationships": _count(
            db, EntityRelationshipModel, EntityRelationshipModel.document_id == document_id
        ),
        "structured_entities": _count(
            db,
            StructuredKnowledgeEntityModel,
            StructuredKnowledgeEntityModel.document_id == document_id,
        ),
        "metadata_profiles": _count(
            db,
            DocumentMetadataProfileModel,
            DocumentMetadataProfileModel.document_id == document_id,
        ),
    }


def _count(db, model, *filters) -> int:  # noqa: ANN001
    statement = select(func.count()).select_from(model)
    if filters:
        statement = statement.where(*filters)
    return int(db.scalar(statement) or 0)


def _summary(runs: list[IngestionBenchmarkRun]) -> dict[str, object]:
    return {
        "parse_ms": _stats([run.parse_ms for run in runs]),
        "persist_ms": _stats([run.persist_ms for run in runs]),
        "ingest_ms": _stats([run.ingest_ms for run in runs]),
        "retrieval_ms": _stats([run.retrieval_ms for run in runs]),
        "total_ms": _stats([run.total_ms for run in runs]),
        "rss_delta_mb": _stats([run.rss_delta_mb for run in runs]),
        "quality_signatures": [run.quality_signature for run in runs],
    }


def _stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "min": round(min(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "max": round(max(ordered), 3),
    }


def _rss_mb(process: psutil.Process) -> float:
    return round(process.memory_info().rss / 1024 / 1024, 3)


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _extension_for_source_type(source_type: str) -> str:
    return {
        "markdown": "md",
        "pdf": "pdf",
        "presentation": "pptx",
        "spreadsheet": "xlsx",
        "word_document": "docx",
    }.get(source_type, "txt")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure my-agents ingestion performance.")
    parser.add_argument(
        "--scenario",
        choices=("text", "markdown", "pdf"),
        default="text",
        help="synthetic document type to ingest",
    )
    parser.add_argument("--repeat", type=int, default=3, help="number of isolated runs")
    parser.add_argument(
        "--repeat-units",
        type=int,
        default=80,
        help="size multiplier for the synthetic benchmark document",
    )
    parser.add_argument(
        "--retrieval-query",
        default="How does LangGraph FastAPI ingestion use embeddings and metadata profiles?",
    )
    parser.add_argument("--output", type=Path, default=None, help="optional JSON output path")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
