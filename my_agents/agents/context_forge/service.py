"""ContextForge service boundary for retrieval planning, candidates, and packing."""

from __future__ import annotations

from time import perf_counter

from sqlalchemy.orm import Session

from my_agents.agents.context_forge.candidates import CandidateScouts
from my_agents.agents.context_forge.contracts import (
    CandidateLimits,
    ContextForgeRequest,
    ContextForgeResult,
)
from my_agents.agents.context_forge.debug import debug_agent_turn
from my_agents.agents.context_forge.fusion import fuse_candidates
from my_agents.agents.context_forge.observability import build_retrieval_evidence
from my_agents.agents.context_forge.packing import ContextCurator
from my_agents.agents.context_forge.planner import QueryCartographer
from my_agents.agents.context_forge.reranking import Reranker, build_reranker
from my_agents.agents.context_forge.source_policy import SourceWarden
from my_agents.agents.context_forge.timing import RetrievalTimingTrace
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.knowledge.routing import answer_mode_for_route, is_relevant_retrieval_result
from my_agents.observability.metrics import (
    observe_context_forge,
    track_reranker,
    track_retrieval_phase,
)
from my_agents.settings import get_settings


class ContextForgeService:
    """Multi-role retrieval layer that returns answer-ready authorized context."""

    def __init__(
        self,
        db: Session,
        retrieval_service: RetrievalService | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        settings = get_settings()
        self._retrieval_service = retrieval_service or RetrievalService(db)
        self._planner = QueryCartographer(
            candidate_limits=CandidateLimits(rerank_limit=settings.reranker_top_k),
        )
        self._source_warden = SourceWarden()
        self._scouts = CandidateScouts(self._retrieval_service)
        self._reranker = reranker or build_reranker(settings)
        self._curator = ContextCurator()
        self._debug_retrieval_timing_logging = settings.debug_retrieval_timing_logging

    def retrieve(self, request: ContextForgeRequest) -> ContextForgeResult:
        context_forge_started = perf_counter()
        timing = RetrievalTimingTrace(enabled=self._debug_retrieval_timing_logging)
        debug_agent_turn(
            sender="ConversationRun",
            receiver="QueryCartographer",
            message="Plan retrieval for the latest user message.",
            payload={
                "conversation_id": request.conversation_id,
                "query": request.query,
                "history_message_count": len(request.messages),
                "quality_mode": request.quality_mode,
            },
        )
        selected_ids = self._source_warden.knowledge_base_ids(request.selection_context)
        with timing.phase("authorized_document_count"):
            with track_retrieval_phase("authorized_document_count"):
                document_count = self._retrieval_service.authorized_document_count(
                    user_id=request.user_id,
                    knowledge_base_ids=selected_ids,
                )
        timing.update(authorized_document_count=document_count)
        with timing.phase("query_planning"):
            plan = self._planner.plan(
                message=request.query,
                history=request.messages,
                authorized_document_count=document_count,
            )
        timing.update(
            route=plan.route_decision.route,
            intent=plan.intent,
            reranker=self._reranker.name,
        )
        debug_agent_turn(
            sender="QueryCartographer",
            receiver="SourceWarden",
            message="Resolve the source boundary for the retrieval plan.",
            payload={
                "intent": plan.intent,
                "route": plan.route_decision.route,
                "document_scope": plan.route_decision.document_scope,
                "rewritten_query": plan.rewritten_query,
                "structured_entity_types": list(plan.structured_entity_types),
                "authorized_document_count": document_count,
            },
        )
        if plan.route_decision.route in {"no_retrieval", "clarification_required"}:
            debug_agent_turn(
                sender="SourceWarden",
                receiver="ConversationRun",
                message="Skip candidate retrieval for this route.",
                payload={
                    "route": plan.route_decision.route,
                    "answer_mode": answer_mode_for_route(
                        decision=plan.route_decision,
                        relevant_context_found=False,
                    ),
                },
            )
            evidence = build_retrieval_evidence(
                plan=plan,
                candidates=[],
                injected_chunks=[],
                rejected_count=0,
                budget_truncated=False,
                reranker_name=self._reranker.name,
            )
            result = ContextForgeResult(
                plan=plan,
                decision=plan.route_decision,
                answer_mode=answer_mode_for_route(
                    decision=plan.route_decision,
                    relevant_context_found=False,
                ),
                retrieved_chunks=[],
                retrieval_latency_ms=0.0,
                evidence=evidence,
            )
            timing.emit(
                retrieval_latency_ms=result.retrieval_latency_ms,
                answer_mode=result.answer_mode,
                raw_candidate_count=0,
                fused_candidate_count=0,
                reranked_candidate_count=0,
                injected_count=0,
                rejected_count=0,
                budget_truncated=False,
            )
            observe_context_forge(
                retrieval_route=result.decision.route,
                answer_mode=result.answer_mode,
                duration_seconds=perf_counter() - context_forge_started,
            )
            return result

        started = perf_counter()
        debug_agent_turn(
            sender="SourceWarden",
            receiver="CandidateScouts",
            message="Gather only candidates inside the resolved knowledge-base boundary.",
            payload={
                "selected_knowledge_base_ids": list(selected_ids) if selected_ids else None,
                "vector_limit": plan.limits.vector_limit,
                "structured_limit": plan.limits.structured_limit,
                "rerank_limit": plan.limits.rerank_limit,
            },
        )
        with timing.phase("candidate_gather"):
            with track_retrieval_phase("candidate_gather"):
                raw_chunks = self._scouts.gather(
                    user_id=request.user_id,
                    plan=plan,
                    knowledge_base_ids=selected_ids,
                )
        timing.update(raw_candidate_count=len(raw_chunks))
        debug_agent_turn(
            sender="CandidateScouts",
            receiver="CandidateFusion",
            message="Return authorized raw candidates for dedupe and source fusion.",
            payload={
                "raw_candidate_count": len(raw_chunks),
                "raw_sources": sorted({item.source for item in raw_chunks}),
                "raw_chunk_ids": [item.chunk.id for item in raw_chunks[: plan.limits.rerank_limit]],
            },
        )
        with timing.phase("candidate_fusion"):
            with track_retrieval_phase("candidate_fusion"):
                candidates = fuse_candidates(raw_chunks)
        timing.update(fused_candidate_count=len(candidates))
        debug_agent_turn(
            sender="CandidateFusion",
            receiver="EvidenceJudge",
            message="Send bounded fused candidates for reranking.",
            payload={
                "fused_candidate_count": len(candidates),
                "reranker": self._reranker.name,
                "rerank_limit": plan.limits.rerank_limit,
                "sent_candidate_ids": [
                    item.chunk.chunk.id for item in candidates[: plan.limits.rerank_limit]
                ],
            },
        )
        with timing.phase("reranking"):
            with track_reranker(self._reranker.name):
                reranked = self._reranker.rerank(
                    plan=plan,
                    candidates=candidates[: plan.limits.rerank_limit],
                )
        timing.update(reranked_candidate_count=len(reranked))
        debug_agent_turn(
            sender="EvidenceJudge",
            receiver="ContextCurator",
            message="Send reranked candidates for answer-context packing.",
            payload={
                "reranked_candidate_count": len(reranked),
                "injected_limit": plan.limits.injected_limit,
                "char_budget": plan.limits.char_budget,
                "top_candidate_ids": [item.chunk.chunk.id for item in reranked[:5]],
                "top_rerank_scores": [item.rerank_score for item in reranked[:5]],
            },
        )
        with timing.phase("context_pack"):
            with track_retrieval_phase("context_pack"):
                injected_chunks, rejected, budget_truncated = self._curator.pack(
                    plan=plan,
                    candidates=reranked,
                )
        timing.update(
            injected_count=len(injected_chunks),
            rejected_count=len(rejected),
            budget_truncated=budget_truncated,
        )
        debug_agent_turn(
            sender="ContextCurator",
            receiver="ConversationRun",
            message="Return packed authorized context and redacted retrieval evidence.",
            payload={
                "injected_chunk_ids": [item.chunk.id for item in injected_chunks],
                "rejected_count": len(rejected),
                "budget_truncated": budget_truncated,
            },
        )
        retrieval_latency_ms = round((perf_counter() - started) * 1000, 3)
        relevant_context_found = any(
            is_relevant_retrieval_result(
                route=plan.route_decision.route,
                source=item.source,
                score=item.score,
            )
            for item in injected_chunks
        )
        evidence = build_retrieval_evidence(
            plan=plan,
            candidates=candidates,
            injected_chunks=injected_chunks,
            rejected_count=len(rejected),
            budget_truncated=budget_truncated,
            reranker_name=self._reranker.name,
        )
        result = ContextForgeResult(
            plan=plan,
            decision=plan.route_decision,
            answer_mode=answer_mode_for_route(
                decision=plan.route_decision,
                relevant_context_found=relevant_context_found,
            ),
            retrieved_chunks=injected_chunks,
            retrieval_latency_ms=retrieval_latency_ms,
            evidence=evidence,
            rejected_candidates=rejected,
        )
        timing.emit(
            retrieval_latency_ms=result.retrieval_latency_ms,
            answer_mode=result.answer_mode,
        )
        observe_context_forge(
            retrieval_route=result.decision.route,
            answer_mode=result.answer_mode,
            duration_seconds=perf_counter() - context_forge_started,
        )
        return result
