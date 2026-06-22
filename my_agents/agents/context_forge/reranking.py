"""Evidence Judge reranking seam for ContextForge."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol

from my_agents.agents.context_forge.contracts import RetrievalCandidate, RetrievalPlan
from my_agents.settings import Settings


class Reranker(Protocol):
    """Common interface for candidate rerankers."""

    @property
    def name(self) -> str:
        """Return a redacted observability name for this reranker."""
        ...

    def rerank(
        self,
        *,
        plan: RetrievalPlan,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        """Return candidates sorted by answer relevance."""
        ...


class DeterministicReranker:
    """Stable offline reranker that preserves fused score order."""

    @property
    def name(self) -> str:
        return "deterministic"

    def rerank(
        self,
        *,
        plan: RetrievalPlan,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        _ = plan
        return sorted(
            candidates,
            key=lambda item: (
                -(item.rerank_score if item.rerank_score is not None else item.score),
                item.chunk.chunk.ordinal,
            ),
        )


class CrossEncoderReranker:
    """Second-stage reranker that scores bounded query/document pairs together.

    The implementation follows the two-stage retrieval pattern: ContextForge first gathers
    authorized candidates with fast retrieval, then the cross-encoder only scores the bounded
    `CandidateLimits.rerank_limit` set. The heavy `sentence-transformers` dependency remains
    optional so deterministic CI and local smoke checks stay offline by default.
    """

    def __init__(
        self,
        *,
        model_name: str,
        batch_size: int,
        device: str | None = None,
        model: object | None = None,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._device = device
        self._model: Any | None = model

    @property
    def name(self) -> str:
        return "cross_encoder"

    def rerank(
        self,
        *,
        plan: RetrievalPlan,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        pairs = [(plan.rewritten_query, _candidate_text(candidate)) for candidate in candidates]

        raw_scores = self._cross_encoder.predict(pairs, batch_size=self._batch_size)
        scores = [float(score) for score in raw_scores]
        scored_candidates = [
            RetrievalCandidate(
                chunk=candidate.chunk,
                sources=candidate.sources,
                score=candidate.score,
                rerank_score=score,
                reasons=(*candidate.reasons, f"cross_encoder:{self._model_name}"),
            )
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        return sorted(
            scored_candidates,
            key=lambda item: (
                -(item.rerank_score if item.rerank_score is not None else item.score),
                item.chunk.chunk.ordinal,
            ),
        )

    @property
    def _cross_encoder(self) -> Any:
        if self._model is None:
            self._model = _load_cross_encoder(self._model_name, self._device)
        return self._model


def build_reranker(settings: Settings) -> Reranker:
    """Build the configured ContextForge reranker."""
    if settings.reranker_mode == "cross_encoder":
        return CrossEncoderReranker(
            model_name=settings.cross_encoder_model,
            batch_size=settings.cross_encoder_batch_size,
            device=settings.cross_encoder_device,
        )
    return DeterministicReranker()


def _candidate_text(candidate: RetrievalCandidate) -> str:
    return candidate.chunk.chunk.content.strip()


@lru_cache(maxsize=4)
def _load_cross_encoder(model_name: str, device: str | None) -> object:
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:  # pragma: no cover - exercised without optional package installed.
        raise RuntimeError(
            "MY_AGENTS_RERANKER_MODE=cross_encoder requires the optional "
            "`sentence-transformers` package in the runtime environment. "
            "Install it before enabling cross-encoder reranking, or keep "
            "MY_AGENTS_RERANKER_MODE=deterministic for offline mode."
        ) from exc
    if device is None:
        return CrossEncoder(model_name)
    return CrossEncoder(model_name, device=device)
