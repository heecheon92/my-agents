# Shared answer finalization

Normal sync, normal SSE, resumed sync/SSE and replay completion share prepare_answer in
my_agents/api/conversations/answer_finalization.py. It receives the caller-selected base reply,
resolved retrieval context, graph result and memory snapshot. It composes the existing RAG reply,
reconciles coverage and applies the unchanged grounding verifier.

PreparedAnswer is request-local, never graph/checkpoint state. It carries reply, consulted sources,
coverage, insufficiency and memory metadata; reasoning summaries are validated lazily when
persistence requests them, preserving the previous failure timing. Its raw graph reference is
excluded from repr. It performs no provider calls or database writes.

Callers retain clarification/missing-reply/interrupt branches, SSE order, fallback deltas,
cancellation checks, checkpoint deletion and replay pruning. persist_completed_run remains the
single persistence operation. No new transport or transaction policy is introduced.

Extend common answer preparation here; do not duplicate composition/grounding across transport
handlers. New terminal-state behavior needs its own cross-path tests and explicit scope.
