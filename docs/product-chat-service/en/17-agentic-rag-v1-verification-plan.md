# Agentic RAG Workflow v1 verification and redaction plan

Last updated: 2026-06-06
Owner lane: backend verification / redaction / hosted-smoke evidence

This plan is the evidence checklist for the Agentic RAG Workflow v1 team delivery. It is intentionally backend-owned and does not mutate `.omx/ultragoal` or Codex goal state; the team leader owns that runtime checkpoint.

## Redacted event contract

Conversation run events and SSE progress events may expose operational metadata only. They are safe for a compact frontend agent trace when they keep this shape:

- event names and sequence/order;
- run/conversation IDs already present in the API envelope;
- route labels, retrieval route, answer mode, document scope, and localization-neutral clarification metadata;
- counts and booleans such as message length, retrieved/candidate/rejected/injected counts, citation count, `budget_truncated`, and latency;
- knowledge-base selection IDs/counts that the authenticated caller is already allowed to use.

Run event payloads must not include:

- raw user prompt/message text;
- raw assistant reply text;
- raw retrieved chunk text, document body, packed context, or provider prompt payload;
- email addresses, passwords, auth/session/CSRF tokens, cookies, API keys, provider credentials, or database URLs;
- hidden chain-of-thought, internal planner scratchpads, or unredacted ContextForge role handoff transcripts.

The local/hosted smoke helper enforces the core API-level gate through `assert_redacted_run_events`: required lifecycle event types must be present, each event payload must be an object, known smoke prompt/account/document strings must not appear, and sensitive payload keys such as `token`, `password`, `api_key`, `raw_context`, `prompt`, `message`, `content`, and `reply` are rejected recursively.

## Required backend evidence

Run these from the backend repository before claiming Agentic RAG v1 backend readiness:

```bash
uv run pytest -q tests/test_local_demo_smoke.py
uv run pytest -q tests/test_conversations_api.py tests/test_context_forge_structured_retrieval.py tests/test_permission_aware_rag.py
uv run pytest -q
uv run ruff check . --no-cache
uv run ruff format --check .
git diff --check
```

For a local API smoke after `python -m scripts.local_demo_seed` and a running backend:

```bash
uv run python -m scripts.local_demo_smoke --base-url http://localhost:8000 --timeout 120
```

For hosted smoke, use the same product flow through the deployed frontend/BFF or backend URL and record a redacted evidence bundle using `docs/product-chat-service/en/12-public-demo-deployment-readiness.md`.

## Frontend coordination gate

The frontend agent trace may render localized labels for these backend event types, but it should not require raw backend text fields:

- `run_started`
- `user_message_stored`
- `retrieval_completed`
- `graph_invoked`
- `answer_composed`
- `run_failed`
- `run_cancelled`

Frontend verification should prove:

- Korean and English labels render from event type / route / answer-mode metadata;
- existing evidence, citation, run-detail, and SSE answer UI still render;
- no browser storage, logs, screenshots, or evidence bundles contain cookies, CSRF/session tokens, provider keys, raw prompt text inside event payloads, or uploaded document content beyond intentionally safe snippets.

## Hosted smoke evidence checklist

Record the following for preview/production without exposing secrets:

- backend commit and frontend commit or deployment identifiers;
- migration state / database target in redacted form;
- runtime mode and model/budget boundary;
- health result;
- signup or guest login path, with `/auth/dev/outbox` explicitly not used outside local dev;
- document create/upload and ingest evidence;
- streamed run status, `answer_delta` count, `run_completed`, citation count, and persisted run-detail citations;
- run events count and required event types;
- redaction statement covering emails, codes, cookies, CSRF/session tokens, document IDs/user IDs/KB IDs/run IDs when omitted, provider/database credentials, and raw document contents;
- cleanup state for disposable production smoke accounts/artifacts.

## Current lane status

- Redaction audit source: `my_agents/api/conversations/run_events.py` emits count/route/selection metadata, not raw prompt/reply/context strings.
- Verification helper update: `scripts/local_demo_smoke.py` now recursively rejects sensitive event payload keys and smoke prompt/account/document strings.
- Regression tests: `tests/test_local_demo_smoke.py` covers accepted safe payloads, raw prompt leakage, forbidden nested keys, and non-object payloads.
- Pending integration evidence: full-suite and hosted smoke should run after worker-1/worker-2/worker-3 code lanes are integrated, because this lane intentionally does not own backend orchestration or frontend UI implementation.
