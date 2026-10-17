# Reasoning compatibility and bounded Jev decisions

- Status: Shipped implementation, integrated on remote `main` and `develop`.
- Integration verified: 2026-10-17.
- Canonical status: [implementation tracking](../implementation-tracking.md#shipped-and-completed-index).
- Current contracts: [reasoning](../product-chat-service/26-run-reasoning-preferences.md),
  [runtime reference](../product-chat-service/34-runtime-and-integration-reference.md).

## Delivered scope

`6073312` normalizes GPT-5.6 `minimal` to `low` and freezes the seven public reasoning
efforts. `2f2e836` adds GPT-6 reasoning compatibility, including Pro support and normalization
before persistence/provider calls. Model-specific compatibility remains application-owned.

`1e18c8d` implements Jev source selection, retrieval-method selection, and ContextForge intent
classification through `my_agents/decisions.py`. Authorization and OpenAI answer generation stay
outside the decision adapter. Missing keys, malformed output, and provider errors retain local
fallbacks; deterministic response mode suppresses these network calls. Later evidence reranking
has its own [contract](../product-chat-service/37-jev-evidence-reranking.md).

## Acceptance evidence

On 2026-10-17, refreshed remote refs and `git ls-remote` showed `main` at `3b6fd23` and
`develop` at `1f654c5`, with `0 / 1` divergence. All three commits above are ancestors of
remote `main`; stale active/publication-pending roadmap entries were reconciled accordingly.
Source and regression coverage were inspected in `my_agents/reasoning.py`,
`tests/test_reasoning_compatibility.py`, and `tests/test_jev_decisions.py`.

Original offline validation remains preserved under
[dated verification evidence](../implementation-tracking.md#latest-verification-evidence):
GPT-5.6 normalization: 643 passed / 14 skipped; GPT-6 compatibility: 622 passed / 14 skipped;
Jev integration: 602 passed / 14 skipped plus 33 focused checks after final adjustments.
Those are historical results, not new test runs.

## Remaining verification

Repository integration does not identify the deployed backend SHA or prove the complete live
model/effort/provider matrix. Jev decision accuracy, cost, and latency still need representative
live evaluation. The existing 54-run reranking benchmark is evidence for reranking, not all
three decision callers. No runtime behavior, providers, dependencies, or frontend code changed
during this documentation reconciliation.
