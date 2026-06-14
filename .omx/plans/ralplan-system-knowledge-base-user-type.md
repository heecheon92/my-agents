# RALPLAN Consensus Plan — System Knowledge Base + User Type

## Task

Plan system knowledge base and platform user type implementation for `my-agents` using `.omx/specs/deep-interview-system-knowledge-base-user-type.md`.

## Planning mode

Deliberate consensus because the change touches auth, migrations, authorization filters, retrieval behavior, backend/frontend API contracts, privileged UI surfaces, and public-to-all-users project knowledge.

## Planning artifacts

- PRD: `.omx/plans/prd-system-knowledge-base-user-type.md`
- Test spec: `.omx/plans/test-spec-system-knowledge-base-user-type.md`
- Architect review: `.omx/plans/architect-review-system-knowledge-base-user-type.md`
- Critic review: `.omx/plans/critic-review-system-knowledge-base-user-type.md`

## RALPLAN-DR summary

### Principles

1. System knowledge is public authenticated retrieval context, not user memory.
2. User-type mutation is script-only.
3. Personal/group knowledge boundaries are preserved.
4. Retrieval visibility and management visibility are separate authorization dimensions.
5. Root/system are equivalent in v1.
6. Backend contract is authoritative; frontend gates are convenience, not security.

### Decision drivers

1. All authenticated chat users, including guests, should receive project facts.
2. Only root/system users should manage system KBs.
3. Avoid broad user-management/admin scope.
4. Reuse existing knowledge ingestion/retrieval where safe.
5. Keep tests offline and permission-focused.

### Viable options

#### Option 1 — First-class `system` KB scope with split authorization

Recommended. Add `KnowledgeBaseScope.SYSTEM`; keep creator as `owner_user_id` for audit; add explicit helpers for list/manage vs chat retrieval; include system KBs ambiently in chat.

#### Option 2 — Pseudo-user personal KB

Rejected. It misrepresents public project facts as personal content and risks weakening personal KB filters.

#### Option 3 — Static prompt context

Rejected. It cannot support privileged UI/API CRUD, ingestion, citations, or source lifecycle.

## Decision

Proceed with Option 1.

## Architecture plan

1. **Auth/user type**
   - Add `UserType` enum/helper with `normal`, `root`, `system`.
   - Add non-null `users.user_type` migration defaulting/backfilling to `normal`.
   - Extend `Principal` and `/auth/me` with read-only capability; `can_manage_system_knowledge` is the canonical frontend gate, raw `user_type` is optional/read-only.
   - Add `scripts/set_user_type.py` or equivalent operator script; no API mutation route.

2. **System KB scope**
   - Add `KnowledgeBaseScope.SYSTEM`.
   - Represent system KBs with `owner_user_id = creator_user_id`, `group_id = null`, `purpose = standard`.
   - Treat scope, not owner equality, as the system-public source indicator.

3. **Authorization split**
   - Introduce named helpers for management-visible vs chat-retrievable KBs.
   - Root/system list/manage system KBs.
   - Normal/guest cannot list/manage/guess system KB IDs through direct routes.
   - All authenticated users receive system KBs as ambient chat retrieval context.

4. **Retrieval integration**
   - Extend `KnowledgeBaseSelectionContext` or adjacent context object with ambient system KB IDs/count.
   - In `all` mode, retrieve personal/group authorized KBs plus standard system KBs.
   - In `selected` mode, retrieve selected authorized personal/group KBs plus standard system KBs.
   - Update document readability predicates so system documents match retrieval even when owned by a privileged creator.
   - Keep system KB evidence distinct from user memory in trace/source metadata.

5. **System management API**
   - Allow privileged `POST /knowledge-bases` with `scope = system`.
   - Update `/knowledge-bases` list/get to include system only for managers.
   - Add/extend system KB update/delete.
   - Add/extend system document create/upload/edit/delete/ingest routes, with direct `/documents/{id}` system-document operations allowed only for root/system managers and concealed from normal/guest users.
   - Use concealed 404-style failures for guessed IDs where current API style does so.

6. **Frontend UI/contract**
   - Update frontend models from backend OpenAPI/contract.
   - Gate system source management on `can_manage_system_knowledge`.
   - Add system/public project knowledge source-space UI for root/system.
   - Keep normal/guest source management personal/group-only.
   - Add chat copy that system project knowledge is ambient when present.

7. **Docs**
   - Update backend root README pair: `README.md` and `README.en.md`.
   - Update general assistant README pair: `my_agents/agents/general_assistant/README.md` and `my_agents/agents/general_assistant/README.en.md`.
   - Update product KB/RAG contract docs in both language trees where present: `docs/product-chat-service/en/12-knowledge-base-path-openapi-handoff.md`, `docs/product-chat-service/ko/12-knowledge-base-path-openapi-handoff.md`, `docs/product-chat-service/en/06-permission-aware-rag.md`, `docs/product-chat-service/ko/06-permission-aware-rag.md`, and the `docs/product-chat-service/*/README.md` indexes.
   - Update `scripts/README.md` with the operator-only `scripts/set_user_type.py` usage, dry-run behavior, exact-one-identifier rule, safe output, and default refusal to promote guest accounts.
   - Update frontend docs/README if UI changes are user-facing.
   - No `.env.example` update unless implementation adds env knobs.

## Pre-mortem

1. **System KB leaks into normal UI list** — using chat filters in list/manage routes. Mitigate with named split helpers and normal/guest tests.
2. **System retrieval does not work for non-owners** — KB filter updated but document readable predicates still owner/group-bound. Mitigate with retrieval tests using system docs owned by another user.
3. **Public role mutation appears accidentally** — response field copied into request schema/profile update. Mitigate with `extra="forbid"` tests and route audit.
4. **System content contains secrets** — managers upload private docs to a public source. Mitigate with UI/docs warnings and future review workflow if needed.

## Code-quality review and execution readiness

Worker-3 reviewed the plan against the current backend implementation anchors before execution. No production code is modified by this plan task; the following constraints document the code-quality bar for the implementation lanes.

### Current-code anchors

- `my_agents/auth/models.py` has `UserModel.account_type` for registered/guest accounts, but no platform privilege field yet.
- `my_agents/auth/contracts.py` has `Principal(user_id, session_id, is_guest)` and therefore needs an explicit capability or user-type extension before API services can authorize system KB management without reloading users ad hoc.
- `my_agents/knowledge/models.py` limits `KnowledgeBaseScope` to `personal` and `group`, so `system` must be a first-class scope rather than an overloaded owner/group convention.
- `my_agents/knowledge/auth.py` currently uses `retrievable_knowledge_base_filter()` for both ordinary listing and chat retrieval, while direct document writes go through `require_personal_knowledge_base_for_document_write()`. The implementation must split these responsibilities instead of widening the existing helpers in place.
- `my_agents/api/knowledge_bases.py` and `my_agents/api/documents.py` route nested KB/document operations through the current personal/group authorization helpers; system document management needs an explicit privileged path with concealed failures for normal/guest users.

### Required implementation-quality gates

1. **Named authorization helpers over broad condition edits** — add separate helpers for management-visible KBs, selected-source validation, ambient chat retrieval, and privileged system document management. Do not silently make the old personal/group filter mean every surface.
2. **Script-only privilege mutation** — keep `user_type` writes out of request schemas and profile/auth routes; tests should fail if API payloads can promote users or if guest accounts can be promoted without an explicit future override.
3. **Ambient retrieval without UI enumeration** — all authenticated chat users should benefit from system KB retrieval, but normal/guest list/get/selected-source paths should not expose system KB IDs or names unless a future UI contract deliberately adds safe metadata.
4. **Selected-mode exception is explicit** — document and test that user-selected personal/group KBs remain the hard user-controlled boundary, while standard system KBs are added ambiently in both `all` and `selected` modes.
5. **Ambient metadata stays safe** — distinguish user-visible source-selection fields from audit/internal ambient system metadata; do not make `resolved_knowledge_base_ids` a normal/guest enumeration channel by accident.
6. **Guest privilege semantics stay closed** — guest users may receive ambient system KB retrieval, but guest-created principals/rows still carry `user_type = normal` and `can_manage_system_knowledge = false`.
7. **Document predicate parity** — system retrieval is not complete until document readability predicates allow chunks from system KB documents owned by privileged creators; changing only KB filters is insufficient.
8. **Frontend gates are convenience only** — backend authorization and OpenAPI/schema are the source of truth; frontend `can_manage_system_knowledge` gating must not be treated as a security boundary.
9. **Public-content warning** — docs and UI copy must warn root/system managers that system KB content is public to authenticated chat users, including env-gated guests, and must not contain secrets.
10. **Regression preservation** — personal KB, group KB, invitation/membership, published-personal-KB, guest limits, memory redaction, and source-evidence tests must remain green.

### Execution-readiness judgment

The plan is execution-ready after architect and critic approval. A future implementation PR is **not** ready if it only adds `scope=system` to schemas, only updates frontend gates, only broadens `retrievable_knowledge_base_filter()`, or leaves the canonical KB handoff/permission docs teaching that `selected` excludes all non-selected retrieval context.

## Expanded test plan

See `.omx/plans/test-spec-system-knowledge-base-user-type.md` for the complete matrix. Minimum gates: backend migration/script/auth/KB/retrieval/regression tests; frontend schema/gating/system-source UI/chat-copy tests; docs; full backend/frontend verification commands.

## ADR

### Decision

Use a first-class `KnowledgeBaseScope.SYSTEM` and a separate persisted `users.user_type` privilege field. Include system KBs in authenticated chat retrieval ambiently while exposing management UI/API only to root/system users.

### Drivers

- Public project facts should be available to all authenticated users.
- System KBs need full privileged CRUD UI/API.
- User-type assignment must stay script-only.
- Personal/group authorization must remain intact.

### Alternatives considered

- Pseudo-user personal KB.
- Static prompt injection.
- Broad admin/user-management UI.

### Why chosen

The system scope best matches product semantics, source labels, permission tests, and future manageability while avoiding misuse of per-user memory.

### Consequences

- Requires migration for `users.user_type`.
- Requires auth principal/schema changes.
- Requires careful retrieval/list/manage filter split.
- Requires frontend contract/model/UI updates.
- Requires docs warning that system KB content is public to authenticated chat users.

## Available agent types roster

- `architect` — auth/retrieval/API boundary review.
- `critic` — adversarial plan/test adequacy review.
- `planner` — milestone sequencing and risk flags.
- `executor` — backend/frontend implementation.
- `test-engineer` — test matrix and regression coverage.
- `designer` — frontend source-management UX refinement.
- `verifier` — completion evidence and final validation.
- `code-reviewer` — final code/security review.
- `git-master` — branch/merge/tag/push hygiene if needed.
- `writer` — bilingual docs and learning-note updates.

## Follow-up staffing guidance

### Recommended `$team`

Use `$team` because backend and frontend work can split cleanly but must integrate through a contract:

- Lane A — Backend auth/migration/script: `UserType`, migration, principal, `/auth/me`, operator script/tests.
- Lane B — Backend system KB/retrieval: scope, authorization split, CRUD, retrieval predicates, conversation tests.
- Lane C — Frontend contract/UI: schemas, capability gating, system source management, chat copy/tests.
- Lane D — Docs/verification: README pairs, agent docs, final regression commands.

Team verification path: lane reports, backend/frontend contract sync, backend full gate, frontend full gate, final route/schema/security review.

Launch hint:

```bash
$team .omx/plans/ralplan-system-knowledge-base-user-type.md
```

### `$ultragoal` alternative

Use `$ultragoal` if one durable sequential goal ledger is preferred: auth/user type, system KB auth, retrieval, system management API, frontend UI, docs/verification.

### `$ralph` fallback

Use `$ralph` only if the owner explicitly wants a single-owner persistent completion loop after planning. Ensure PRD and test spec paths are passed so the ralph planning gate is satisfied.

## Goal-Mode follow-up suggestions

- `$team` — recommended default because cross-repo backend/frontend lanes are independent but contract-coupled.
- `$ultragoal` — good if durable sequential goals are preferred.
- `$ralph` — fallback only for single-owner loop.
- `$autoresearch-goal` — not recommended; requirements are already clarified.
- `$performance-goal` — not applicable unless retrieval latency becomes primary.

## Consensus status

- Architect review: APPROVE; non-blocking recommendations applied.
- Critic review: APPROVE in `.omx/plans/critic-review-system-knowledge-base-user-type.md`.
- Worker-3 documentation/code-quality review: COMPLETE; implementation-quality gates and exact downstream doc surfaces documented in this RALPLAN.
- Consensus gate: APPROVED — execution-ready for `$team` or `$ultragoal` handoff.
