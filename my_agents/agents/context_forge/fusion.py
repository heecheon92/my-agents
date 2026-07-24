"""Candidate fusion for ContextForge."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from my_agents.agents.context_forge.contracts import RetrievalCandidate
from my_agents.knowledge.retrieval import RetrievedChunk

RRF_RANK_CONSTANT = 60


def fuse_candidates(chunks: Sequence[RetrievedChunk]) -> list[RetrievalCandidate]:
    """Fuse independent source rankings with reciprocal rank fusion."""
    ranked_by_source: dict[str, dict[str, RetrievedChunk]] = defaultdict(dict)
    source_order: list[str] = []
    for chunk in chunks:
        if chunk.source not in ranked_by_source:
            source_order.append(chunk.source)
        existing = ranked_by_source[chunk.source].get(chunk.chunk.id)
        if existing is None or chunk.score > existing.score:
            ranked_by_source[chunk.source][chunk.chunk.id] = chunk

    by_chunk_id: dict[str, RetrievalCandidate] = {}
    best_original_score: dict[str, float] = {}
    for source in source_order:
        ranked = sorted(
            ranked_by_source[source].values(),
            key=lambda item: (-item.score, item.chunk.ordinal, item.chunk.id),
        )
        for rank, chunk in enumerate(ranked, start=1):
            rrf_score = 1 / (RRF_RANK_CONSTANT + rank)
            existing = by_chunk_id.get(chunk.chunk.id)
            if existing is None:
                by_chunk_id[chunk.chunk.id] = RetrievalCandidate(
                    chunk=chunk,
                    sources=(source,),
                    score=rrf_score,
                    reasons=(f"matched:{source}", f"rrf:{source}:rank={rank}"),
                )
                best_original_score[chunk.chunk.id] = chunk.score
                continue
            chosen_chunk = _preferred_chunk(chunk, existing.chunk)
            by_chunk_id[chunk.chunk.id] = RetrievalCandidate(
                chunk=chosen_chunk,
                sources=tuple(dict.fromkeys((*existing.sources, source))),
                score=existing.score + rrf_score,
                rerank_score=existing.rerank_score,
                reasons=tuple(
                    dict.fromkeys(
                        (
                            *existing.reasons,
                            f"matched:{source}",
                            f"rrf:{source}:rank={rank}",
                        )
                    )
                ),
            )
            best_original_score[chunk.chunk.id] = max(
                best_original_score[chunk.chunk.id],
                chunk.score,
            )
    return sorted(
        by_chunk_id.values(),
        key=lambda item: (
            -item.score,
            -len(item.sources),
            -_source_priority(item.chunk.source),
            -best_original_score[item.chunk.chunk.id],
            item.chunk.chunk.ordinal,
            item.chunk.chunk.id,
        ),
    )


def _preferred_chunk(
    candidate: RetrievedChunk,
    existing: RetrievedChunk,
) -> RetrievedChunk:
    if _source_priority(candidate.source) > _source_priority(existing.source):
        return candidate
    if (
        _source_priority(candidate.source) == _source_priority(existing.source)
        and candidate.score > existing.score
    ):
        return candidate
    return existing


def _source_priority(source: str) -> int:
    if source.startswith("structured_entity:"):
        return 50
    if source == "document_metadata":
        return 40
    if source == "graph_expansion":
        return 30
    if source == "semantic_vector":
        return 20
    if source == "document_metadata_profile":
        return 15
    return 10
