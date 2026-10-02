"""Replay frozen authorized shortlists without generating final answers.

Private inputs/snapshots stay outside the repository. Results contain anonymous candidate
labels and aggregate metrics, never account identifiers, queries, or source text.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import platform
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import httpx
from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from my_agents.agents.context_forge import ContextForgeService
from my_agents.agents.context_forge.contracts import (
    CandidateLimits,
    ContextForgeRequest,
    RetrievalCandidate,
    RetrievalPlan,
)
from my_agents.agents.context_forge.packing import ContextCurator
from my_agents.agents.context_forge.reranking import (
    CrossEncoderReranker,
    DeterministicReranker,
    JevReranker,
)
from my_agents.auth.models import UserModel
from my_agents.decisions import JevScoreClient
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import DocumentChunkModel, DocumentModel
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import RetrievalRoutingDecision
from my_agents.persistence.models import import_all_models
from my_agents.settings import get_settings


def load_profile(path: Path, name: str) -> None:
    """Apply safe process-local overrides; Settings owns normal credential loading."""
    profile = next(
        item for item in json.loads(path.read_text())["configurations"] if item["name"] == name
    )
    os.environ.update(profile.get("env", {}))
    os.environ["MY_AGENTS_DEBUG_KNOWLEDGE_CONTEXT_LOGGING"] = "false"
    os.environ["MY_AGENTS_DEBUG_RETRIEVAL_TIMING_LOGGING"] = "false"
    get_settings.cache_clear()


def write_private_json(path: Path, value: object) -> None:
    """Create a private artifact without following existing paths or symlinks."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


class CaptureShortlist:
    name = "benchmark_snapshot"

    def rerank(self, *, plan, candidates):
        self.plan = plan
        self.candidates = list(candidates)
        return list(candidates)


def freeze_candidate(candidate: RetrievalCandidate, index: int) -> dict:
    source = candidate.chunk
    chunk = source.chunk
    document = source.document
    return {
        "label": f"c{index:02d}",
        "chunk_id": chunk.id,
        "document_id": document.id,
        "title": document.title,
        "filename": document.source_filename,
        "ordinal": chunk.ordinal,
        "page": chunk.source_page,
        "content": chunk.content,
        "retrieval_score": source.score,
        "source": source.source,
        "fused_score": candidate.score,
        "sources": list(candidate.sources),
        "reasons": list(candidate.reasons),
    }


def thaw_candidate(item: dict) -> RetrievalCandidate:
    document = DocumentModel(
        id=item["document_id"], title=item["title"], source_filename=item["filename"]
    )
    chunk = DocumentChunkModel(
        id=item["chunk_id"],
        document_id=item["document_id"],
        ordinal=item["ordinal"],
        source_page=item["page"],
        content=item["content"],
    )
    return RetrievalCandidate(
        chunk=RetrievedChunk(
            chunk=chunk, document=document, score=item["retrieval_score"], source=item["source"]
        ),
        score=item["fused_score"],
        sources=tuple(item["sources"]),
        reasons=tuple(item["reasons"]),
    )


def prepare(args) -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Benchmark preparation requires loopback PostgreSQL")
    import_all_models()
    cases = json.loads(args.cases.read_text())
    snapshots = []
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        with Session(bind=connection) as db:
            account = db.execute(
                select(UserModel.id, UserModel.approval_status, UserModel.account_type).where(
                    UserModel.email == args.email
                )
            ).first()
            if (
                account is None
                or account.approval_status != "approved"
                or account.account_type != "registered"
            ):
                raise ValueError("An approved registered benchmark account is required")
            for case in cases:
                capture = CaptureShortlist()
                service = ContextForgeService(db, reranker=capture)
                started = perf_counter()
                result = service.retrieve(
                    ContextForgeRequest(
                        user_id=account.id,
                        conversation_id="local-reranking-benchmark",
                        query=case["query"],
                        messages=[HumanMessage(content=case["query"])],
                        selection_context=KnowledgeBaseSelectionContext(
                            mode="all", knowledge_base_ids=(), resolved_count=0
                        ),
                    )
                )
                if not hasattr(capture, "candidates") or not capture.candidates:
                    raise ValueError("Benchmark case yielded no authorized shortlist")
                frozen = [
                    freeze_candidate(item, index)
                    for index, item in enumerate(capture.candidates, 1)
                ]
                snapshots.append(
                    {
                        "id": case["id"],
                        "query": case["query"],
                        "required_facets": case["required_facets"],
                        "plan": asdict(capture.plan),
                        "candidates": frozen,
                        "prepare_ms": round((perf_counter() - started) * 1000, 3),
                        "candidate_count": result.evidence.candidate_count,
                        "shortlist_digest": hashlib.sha256(
                            json.dumps(frozen, sort_keys=True).encode()
                        ).hexdigest(),
                    }
                )
                print(
                    json.dumps(
                        {
                            "prepared": case["id"],
                            "candidates": len(frozen),
                            "fused_count": result.evidence.candidate_count,
                        }
                    ),
                    flush=True,
                )
        connection.rollback()
    write_private_json(args.output, {"cases": snapshots})


def ndcg(grades: list[int], ideal: list[int], k: int) -> float:
    def dcg(values):
        return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(values[:k]))

    denominator = dcg(sorted(ideal, reverse=True))
    return dcg(grades) / denominator if denominator else 0.0


def ranking_metrics(
    order: list[str], labels: dict, contents: dict, required_facets: list[str]
) -> dict:
    grades = [labels[key]["grade"] for key in order]
    ideal = [item["grade"] for item in labels.values()]
    metrics = {"ndcg_at_5": ndcg(grades, ideal, 5), "ndcg_at_12": ndcg(grades, ideal, 12)}
    for k in (5, 12):
        found = {facet for key in order[:k] for facet in labels[key]["facets"]}
        metrics[f"facet_coverage_at_{k}"] = len(found & set(required_facets)) / len(required_facets)
    groups = {" ".join(contents[key].split()) for key in order[:12]}
    metrics["unique_content_rate_at_12"] = len(groups) / len(order[:12])
    available = {facet for label in labels.values() for facet in label["facets"]}
    metrics["shortlist_facet_coverage"] = len(available & set(required_facets)) / len(
        required_facets
    )
    metrics["first_grade_4_rank"] = next(
        (index for index, grade in enumerate(grades, 1) if grade == 4), None
    )
    metrics["zero_grade_count_at_12"] = grades[:12].count(0)
    return metrics


class MeasuredTransport(httpx.BaseTransport):
    """Capture numeric usage only; raw provider payloads are never persisted."""

    def __init__(self):
        self.calls = []

    def handle_request(self, request):
        started = perf_counter()
        # Match the production adapter's fresh HTTP transport per batch.
        with httpx.HTTPTransport(retries=0) as transport:
            response = transport.handle_request(request)
            response.read()
        record = {
            "ms": round((perf_counter() - started) * 1000, 3),
            "input_bytes": len(request.content),
            "status": response.status_code,
        }
        try:
            usage = response.json().get("usage", {})
            if isinstance(usage, dict):
                record["usage"] = {
                    key: value
                    for key, value in usage.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                }
        except ValueError, AttributeError:
            pass
        self.calls.append(record)
        return response


def validate_labels(case: dict, labels: dict) -> None:
    if set(labels) != {item["label"] for item in case["candidates"]}:
        raise ValueError("Every frozen candidate needs a relevance label before scoring")
    groups = {}
    for item in case["candidates"]:
        label = labels[item["label"]]
        if type(label["grade"]) is not int or not 0 <= label["grade"] <= 4:
            raise ValueError("Relevance grades must be integers from zero to four")
        if not set(label["facets"]) <= set(case["required_facets"]):
            raise ValueError("Labels contain an unknown target facet")
        normalized = " ".join(item["content"].split())
        if normalized in groups and groups[normalized] != label:
            raise ValueError("Identical content must receive identical independent labels")
        groups[normalized] = label


def run(args) -> None:
    settings = get_settings()
    if settings.response_mode != "openai" or not settings.openrouter_api_key:
        raise ValueError("Jev comparisons require OpenRouter credentials and OpenAI response mode")
    data = json.loads(args.snapshot.read_text())
    gold = json.loads(args.labels.read_text())
    for case in data["cases"]:
        validate_labels(case, gold[case["id"]])
    # Load once; separate first inference from scored warm iterations.
    cross = CrossEncoderReranker(
        model_name=settings.cross_encoder_model,
        batch_size=settings.cross_encoder_batch_size,
        device=settings.cross_encoder_device,
    )
    first = data["cases"][0]
    first_plan = thaw_plan(first["plan"])
    started = perf_counter()
    cross.rerank(plan=first_plan, candidates=[thaw_candidate(item) for item in first["candidates"]])
    cold_ms = round((perf_counter() - started) * 1000, 3)
    rows = []
    for case_index, case in enumerate(data["cases"]):
        plan = thaw_plan(case["plan"])
        candidates = [thaw_candidate(item) for item in case["candidates"]]
        ids = {item["chunk_id"]: item["label"] for item in case["candidates"]}
        contents = {item["label"]: item["content"] for item in case["candidates"]}
        for trial in range(args.repeats):
            # Rotate execution order to reduce consistent order/thermal bias.
            modes = ["deterministic", "jev", "cross_encoder"]
            offset = (case_index + trial) % len(modes)
            for mode in modes[offset:] + modes[:offset]:
                transport = MeasuredTransport() if mode == "jev" else None
                reranker = (
                    JevReranker(settings, client=JevScoreClient(settings, transport=transport))
                    if mode == "jev"
                    else cross
                    if mode == "cross_encoder"
                    else DeterministicReranker()
                )
                started = perf_counter()
                ranked = reranker.rerank(plan=plan, candidates=candidates)
                elapsed = (perf_counter() - started) * 1000
                order = [ids[item.chunk.chunk.id] for item in ranked]
                if len(order) != len(candidates) or set(order) != set(contents):
                    raise ValueError("A reranker changed the authorized shortlist membership")
                packed, rejected, truncated = ContextCurator().pack(plan=plan, candidates=ranked)
                packed_labels = [ids[item.chunk.id] for item in packed]
                packed_facets = {
                    facet for key in packed_labels for facet in gold[case["id"]][key]["facets"]
                }
                row = {
                    "case": case["id"],
                    "trial": trial + 1,
                    "mode": mode,
                    "effective_mode": getattr(reranker, "effective_name", mode),
                    "rerank_ms": round(elapsed, 3),
                    "order": order,
                    "metrics": ranking_metrics(
                        order, gold[case["id"]], contents, case["required_facets"]
                    ),
                    "packed_count": len(packed),
                    "packed_facet_coverage": len(packed_facets) / len(case["required_facets"]),
                    "rejected_count": len(rejected),
                    "budget_truncated": truncated,
                    "calls": transport.calls if transport else [],
                }
                rows.append(row)
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in (
                                "case",
                                "trial",
                                "mode",
                                "effective_mode",
                                "rerank_ms",
                                "metrics",
                            )
                        }
                    ),
                    flush=True,
                )
    result = {
        "environment": {
            "profile": args.profile_name,
            "platform": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "embedding_mode": settings.embedding_mode,
            "embedding_model": settings.openai_embedding_model,
            "cross_encoder_model": settings.cross_encoder_model,
            "cross_encoder_batch_size": settings.cross_encoder_batch_size,
            "cross_encoder_device": settings.cross_encoder_device or "library_auto",
            "jev_batch_size": settings.jev_reranker_batch_size,
            "jev_excerpt_bytes": settings.jev_reranker_max_excerpt_bytes,
            "jev_input_bytes": settings.jev_reranker_max_input_bytes,
            "jev_timeout_seconds": settings.jev_timeout_seconds,
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("sentence-transformers", "torch", "transformers", "httpx")
            },
        },
        "repeats": args.repeats,
        "cross_encoder_cold_ms": cold_ms,
        "labels_digest": hashlib.sha256(args.labels.read_bytes()).hexdigest(),
        "cases": [
            {
                "id": case["id"],
                "shortlist_digest": case["shortlist_digest"],
                "shortlist_count": len(case["candidates"]),
                "candidate_count": case["candidate_count"],
                "prepare_ms": case["prepare_ms"],
            }
            for case in data["cases"]
        ],
        "runs": rows,
    }
    write_private_json(args.output, result)
    print("RERANKING_BENCHMARK_COMPLETE", flush=True)


def thaw_plan(item: dict) -> RetrievalPlan:
    return RetrievalPlan(
        **{
            **item,
            "limits": CandidateLimits(**item["limits"]),
            "route_decision": RetrievalRoutingDecision(**item["route_decision"]),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--profile", type=Path, default=Path(".vscode/launch.json"))
    parser.add_argument("--profile-name", default="FastAPI: uvicorn main:app (local pgvector)")
    parser.add_argument("--email")
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11))
    args = parser.parse_args()
    if args.action == "prepare" and (not args.email or not args.cases):
        parser.error("prepare requires --email and --cases")
    if args.action == "run" and (not args.snapshot or not args.labels):
        parser.error("run requires --snapshot and --labels")
    logging.basicConfig(level=logging.WARNING)
    try:
        load_profile(args.profile, args.profile_name)
        prepare(args) if args.action == "prepare" else run(args)
    except Exception as exc:
        # Avoid dumping settings, provider payloads, SQL parameters or private source data.
        print(json.dumps({"benchmark_error_type": type(exc).__name__}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
