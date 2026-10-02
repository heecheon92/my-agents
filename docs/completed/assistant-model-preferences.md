# Assistant model preferences and compatibility

- Status: Shipped implementation on `develop`; production rollout and provider verification remain separate.
- Completed: 2026-09-30.
- Canonical status: [implementation tracking](../implementation-tracking.md#shipped-and-completed-index).
- Current contracts: [model preferences](../product-chat-service/35-assistant-model-preferences.md),
  [reasoning policy](../product-chat-service/26-run-reasoning-preferences.md),
  [runtime reference](../product-chat-service/34-runtime-and-integration-reference.md).

## Delivered scope

Registered users can save a Product DB-owned assistant model preference from the composer or
settings page. Guests remain on `MY_AGENTS_OPENAI_MODEL`, standard mode, and the application's
default effort. Saving `null` restores the deployment fallback. Current reasoning capabilities
follow the registered user's selected model.

The API supports `gpt-5.6-luna`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-6-sol`, `gpt-6.1-sol`,
and `gpt-6-astra`. `EXPOSED_ASSISTANT_MODELS` advertises only GPT-6.1 Sol, GPT-6 Luna, and
GPT-6 Astra; startup validates it as a subset of `SUPPORTED_ASSISTANT_MODELS`. Existing supported
preferences and deployment defaults remain valid when unexposed. The frontend shows those
values read-only and never adds them as explicit model radio options.

Application-owned default effort entries are editable, initially seeded from provider guidance.
The old effort environment override is retired. The seven public reasoning choices are frozen;
GPT-6.1 Sol and Astra normalize `none`/`minimal` to `low` before persistence and provider calls.

Admission pins the actual configured answer model. Sync/stream/replay use the current preference;
resume uses the admitted pin. Provider instances are cached by model without global Settings
mutation, and request/model-identity prompt use the same selected settings. Temporary document
workspace, decision, embedding, and metadata models retain their separate server configuration.

Migration `20260930_0035` adds nullable user preference and run model columns. Legacy rows keep
unknown model provenance as null. Apply the migration before running the new backend against an
existing database; rollback drops the added preference/provenance columns.

## Acceptance evidence

- Backend: **747 passed, 13 gated skips, 11 dependency deprecation warnings**; Ruff lint/format
  and diff checks passed. Coverage includes all six model selections, subset validation, guest
  enforcement, unexposed preferences/default reset, user isolation, streaming/replay/resume
  pinning, provider cache/identity, SQLite upgrade/downgrade, and PostgreSQL offline SQL.
- Frontend work was completed by Claude in the separate repository: **390 unit tests and 21
  targeted browser tests** passed, plus lint/typecheck/build. The earlier full browser suite had
  **202 passed, 2 skipped, 2 failures** also reproduced on clean develop: workspace file drop
  and transcript autoscroll. Those unrelated failures were preserved as known gaps.
- Served OpenAPI/Zod comparison and real authenticated BFF checks passed against an isolated
  deterministic, in-memory backend: selection/reset, pinned sync/stream metadata, normalization,
  guest reads/403 writes, invalid-request 422, CSRF and exact proxy path restrictions.
- Live phone-width inspection caught send-button overflow from the longer model label. The fix
  was visually checked and a regression test failed with the fix removed, then passed restored.
- The owner reported that the feature seems to work. Temporary verification servers were stopped.

## Limitations

Live integration used deterministic responses. OpenAI account/model access and actual response
quality were not tested; the record does not certify production deployment or live PostgreSQL
migration execution. Existing dependency warnings and the two baseline frontend failures remain.
Historical runs without a pin use the deployment fallback on resume.
