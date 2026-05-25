"""Candidate fusion for ContextForge."""

from __future__ import annotations

from collections.abc import Sequence

from my_agents.agents.context_forge.contracts import RetrievalCandidate
from my_agents.knowledge.retrieval import RetrievedChunk


def fuse_candidates(chunks: Sequence[RetrievedChunk]) -> list[RetrievalCandidate]:
    """Dedupe chunk candidates while preserving the strongest score and source evidence."""
    by_chunk_id: dict[str, RetrievalCandidate] = {}
    for chunk in chunks:
        existing = by_chunk_id.get(chunk.chunk.id)
        if existing is None:
            by_chunk_id[chunk.chunk.id] = RetrievalCandidate(
                chunk=chunk,
                sources=(chunk.source,),
                score=chunk.score,
                reasons=(f"matched:{chunk.source}",),
            )
            continue
        sources = tuple(dict.fromkeys((*existing.sources, chunk.source)))
        if _source_priority(chunk.source) > _source_priority(existing.chunk.source):
            chosen_chunk = chunk
            score = max(existing.score, chunk.score)
        elif chunk.score > existing.score:
            chosen_chunk = chunk
            score = chunk.score
        else:
            chosen_chunk = existing.chunk
            score = existing.score
        by_chunk_id[chunk.chunk.id] = RetrievalCandidate(
            chunk=chosen_chunk,
            sources=sources,
            score=score,
            rerank_score=existing.rerank_score,
            reasons=tuple(dict.fromkeys((*existing.reasons, f"matched:{chunk.source}"))),
        )
    return sorted(by_chunk_id.values(), key=lambda item: (-item.score, item.chunk.chunk.ordinal))


def _source_priority(source: str) -> int:
    if source.startswith("structured_entity:"):
        return 50
    if source == "document_metadata":
        return 40
    if source == "semantic_vector":
        return 20
    if source == "document_metadata_profile":
        return 15
    return 10
