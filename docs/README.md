# Documentation map and lifecycle

This file routes contributors and agents to the smallest authoritative document set for a task. Do
not preload the entire documentation tree: read current summaries first and follow links only when
the task requires deeper architecture, operations, decisions, or history.

## Source-of-truth map

| Document or class | Owns | Authority |
| --- | --- | --- |
| [`implementation-tracking.md`](./implementation-tracking.md) | Current project status, priority, sequence, known gaps, latest verification, and terminal milestone index | Sole mutable status authority |
| [`../ROADMAP.md`](../ROADMAP.md) | Detailed initiative scope, broader backlog, deferred work, and definitions of done | Companion checklist; status mirrors implementation tracking when both mention an item |
| Root and agent README pairs | Current user-facing setup, behavior, and agent/module responsibilities | Current behavior authority for their surface |
| [`product-chat-service/`](./product-chat-service/) | Current product architecture, API contracts, security boundaries, and runbooks | Current technical authority by topic |
| `docs/completed/` | Durable records for shipped/completed initiatives | Historical evidence only; never mutable status authority |
| [`idea/`](./idea/) and [`plan/`](./plan/) | Unaccepted exploration and proposed approaches | Non-authoritative until promoted into implementation tracking |
| [`learning/`](./learning/) | Personal learning notes and debugging lessons | Educational context, not project status |
| [`performance/`](./performance/) | Measurement methods and historical performance evidence | Evidence source for performance work |

The `docs/completed/` directory is created when the first initiative is compacted; do not create an
empty archive or placeholder record.

## Status vocabulary

- `[ ] **Proposed:**` intended but not implemented.
- `[ ] **Active:**` explicitly authorized implementation or evidence collection is underway.
- `[ ] **Deferred:**` intentionally waiting on a decision, dependency, or later priority.
- `[x] **Shipped:**` repository-evidenced product behavior.
- `[x] **Completed:**` repository-evidenced non-product work.
- `[x] ~~Item~~ — **Canceled:**` stopped with its reason preserved.
- `[x] ~~Item~~ — **Superseded:**` replaced by a linked decision or initiative.

Existing legacy `[~]` and `[later]` roadmap markers may remain until their initiatives are next
touched. New and actively updated initiatives use the vocabulary above.

## Documentation router

| Task type | Always read | Read when relevant | Do not preload |
| --- | --- | --- | --- |
| Status or planning | Implementation tracking: current status, known gaps, and recommended sequence | Relevant roadmap initiative; linked idea for an unresolved design | Completion records and unrelated product docs |
| Implementation | Current module/agent README and relevant product contract | Authoritative tracking/roadmap scope; established security or architecture decision | All ideas, completion records, and historical plans |
| Bug fix | Current behavior docs and affected source/tests | Completion record only for regressions or provenance | Entire roadmap and unrelated archives |
| Architecture change | Current architecture and relevant active/deferred initiative | Specific idea, security, operations, or decision material | Unrelated historical milestones |
| Deployment or release | Relevant operations/runbook docs and current verification status | Migration, security, and initiative scope | Unrelated ideas and completion records |
| Documentation-only change | This map and the target document | Source/code that proves the described behavior | Whole documentation tree |
| Historical investigation | Relevant completion record | Changelog, migration, performance evidence, or superseded plan | Unrelated current docs |

## Completion and compaction

When an initiative reaches a terminal state:

1. Verify the state from repository behavior and applicable tests; a plan, patch, or uncommitted
   worktree is not shipped.
2. Update current behavior documentation before moving historical implementation detail.
3. Create or update `docs/completed/<initiative-slug>.md` when the delivered scope, decisions,
   evidence, limitations, or history need more than a concise index row.
4. Replace active detail in implementation tracking with one checked `Shipped` or `Completed` row
   linking to the completion record. Concise milestones may remain index-only.
5. Remove the item from known gaps and recommended work, promote the next priority, and then update
   the ROADMAP mirror in the same change.
6. Preserve important rationale, acceptance evidence, limitations, and links. Never archive secrets,
   raw sessions, mutable runtime state, or sensitive logs.

A completion record uses a stable filename and contains status, completion date, current-behavior
links, delivered scope, decisions and boundaries, acceptance evidence, known limitations/deferred
follow-ups, and only the historical implementation detail worth retaining.

Run a lifecycle maintenance pass when an initiative closes, completed detail dominates the hot
tracker, either tracker becomes difficult to scan, statuses disagree, routing becomes stale, or an
archive link breaks. Historical records explain what happened but never regain status authority.

## Minimal reading order

1. Read the already-loaded repository instructions.
2. For status, scope, or priority questions, read only the relevant sections of implementation
   tracking.
3. Classify the task with the router above.
4. Read headings or targeted ranges before loading a large document in full.
5. Follow links only until the task's authoritative prerequisites are satisfied.

## Documentation definition of done

- Each mutable status has one authoritative record in implementation tracking.
- Current behavior documentation reflects shipped behavior before historical detail is compacted.
- ROADMAP mirrors canonical status without becoming a competing priority source.
- Completion records preserve substantive evidence and limitations without duplicating current docs.
- Links resolve, bilingual surfaces remain semantically aligned when required, and no sensitive or
  machine-local runtime material is archived.
