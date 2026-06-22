---
name: rag-performance-optimizer
description: Use when optimizing RAG, retrieval, ContextForge, embedding, reranking, or database-query performance from measured timing output. Supports a multi-turn workflow where the user provides pre-optimization metrics, Codex identifies bottlenecks and quality-safe options, the user chooses actions, Codex implements them, then the user provides post-optimization metrics for before/after documentation.
---

# RAG Performance Optimizer

## Purpose

Run a measurement-led optimization loop for RAG/retrieval performance without sacrificing retrieval quality, authorization safety, citations, or answer grounding.

Use this as a multi-turn workflow, not a one-shot refactor. Prefer evidence from timing output over guesses.

## Workflow

### 1. Intake pre-optimization metrics

When the user provides timing output, extract:

- scenario labels: route, answer mode, intent, reranker, corpus/source scope, warm/cold start if known;
- summary counts: authorized docs, raw/fused/reranked/injected/rejected candidates, budget truncation;
- top-level phases: total, retrieval latency, candidate gather, reranking, context packing;
- nested phases and call counts, especially `candidate_gather.*` rows;
- obvious cardinality smells: repeated full scans, N+1 query calls, duplicate provider calls, expensive fallbacks.

Calculate approximate shares for dominant phases when useful:

```text
phase_share = phase_ms / total_ms * 100
improvement_delta = before_ms - after_ms
improvement_percent = improvement_delta / before_ms * 100
```

### 2. Diagnose before editing

Respond first with:

1. the major bottleneck by measured time and call count;
2. secondary bottlenecks;
3. what is explicitly not the bottleneck;
4. likely root cause in code shape;
5. quality-safe optimization options.

Do not jump straight to reducing retrieval quality. Treat candidate limits, injected-context budgets, reranker disabling, and quality-profile changes as later options unless the user explicitly wants a latency/quality tradeoff.

### 3. Offer options and wait for user choice

For each option, state:

- expected latency effect;
- quality risk: low / medium / high;
- implementation surface;
- verification needed;
- whether docs/ledger need updating.

Prefer options in this order:

1. remove duplicate work, e.g. reuse one query embedding;
2. batch N+1 SQL/provider calls;
3. replace broad scans with targeted queries after preserving authorization filters;
4. add indexes or verify query plans when SQL itself is slow;
5. cache within one retrieval attempt;
6. only then tune retrieval budgets or quality profiles. Treat cross-encoder reranking as quality-critical for document retrieval; do not disable it unless the user explicitly accepts the quality tradeoff. Prefer low-risk reranker optimizations such as warm-loading, batching/device settings, or avoiding duplicate rerank work.

If the user has already clearly chosen an option, proceed without asking again.

### 4. Implement quality-safe changes

Before editing, inspect the real code path and tests. Preserve these invariants:

- authorization must happen before ranking, reranking, packing, and citations;
- do not retrieve global candidates then filter later;
- citation provenance must still point to authorized source chunks;
- document metadata/profile matches should still inject source/body chunks, not only synthetic metadata;
- overview/summary queries should still receive enough context coverage;
- timing output must stay opt-in and redacted.

When touching `my-agents` retrieval, inspect as needed:

- `my_agents/agents/context_forge/`
- `my_agents/agents/rag_agent/`
- `my_agents/knowledge/retrieval.py`
- `my_agents/knowledge/embeddings.py`
- `my_agents/observability/metrics.py`
- `tests/test_context_forge_reranking.py`
- `tests/test_permission_aware_rag.py`
- `docs/product-chat-service/en/23-rag-retrieval-performance-log.md`

### 5. Validate before claiming improvement

Run targeted behavior tests for changed retrieval paths, then lint/format checks. For `my-agents`, a useful baseline is:

```bash
uv run pytest -q \
  tests/test_context_forge_reranking.py \
  tests/test_metrics.py \
  tests/test_context_forge_contracts.py \
  tests/test_permission_aware_rag.py \
  tests/test_publish_requests.py \
  tests/test_conversations_api.py::test_filename_reference_retrieves_matching_document_metadata \
  tests/test_conversations_api.py::test_streaming_conversation_run_emits_answer_deltas_before_completion
uv run ruff check . --no-cache
uv run ruff format --check .
git diff --check
```

Do not claim real latency improvement from tests alone. Say the code change is expected to improve the measured bottleneck, then ask for or wait for the user's same-scenario post-optimization timing run. If reranking is a visible bottleneck, preserve it as a quality-critical stage unless there is a clearly low-risk optimization.

### 6. Analyze post-optimization metrics

When the user provides after metrics:

- compare the same phases and call counts against the before run;
- calculate absolute and percentage improvement for total, retrieval latency, dominant bottlenecks, and any secondary bottlenecks;
- identify new bottlenecks that emerge after the fix;
- explain whether retrieval quality risk remained low or whether further eval is needed.

Use a compact table:

| Phase | Before calls | Before ms | After calls | After ms | Delta ms | Delta % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |

### 7. Update durable documentation

For `my-agents`, update the performance ledger when measurements or optimizations change:

- `docs/product-chat-service/en/23-rag-retrieval-performance-log.md` is canonical;
- `docs/product-chat-service/ko/23-rag-retrieval-performance-log.md` should summarize the operational state;
- link or update observability docs only when the measurement process changes.

Record:

- measurement ID and date;
- redacted pre/post timing values;
- applied optimization and commit/status;
- measured improvement;
- remaining bottleneck and next recommended option.

Never paste raw prompts, document text, user IDs, document IDs, chunk IDs, emails, tokens, or secrets into docs.

## Response shapes

### Pre-optimization analysis

```markdown
Bottleneck: <phase> = <ms> (<share>), <calls> calls.
Secondary: <phase> = <ms>.
Not bottleneck: <phase> was cheap.
Likely cause: <code/data-access shape>.
Quality-safe options:
1. <option> — expected effect, risk, files, tests.
2. <option> — expected effect, risk, files, tests.
Recommended first action: <one sentence>.
```

### Post-optimization summary

```markdown
Applied: <optimization summary>.
Measured improvement:
<table>
Quality impact: <why grounding/auth/citations are preserved>.
Docs updated: <files>.
Next bottleneck: <phase and suggested next step>.
```
