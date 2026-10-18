# Conversation continuity and temporary file recall

English | [한국어](./36-conversation-continuity.ko.md)

## Ownership and context policy

Product DB owns transcripts, explicit message/file associations, run provenance, and rebuildable
conversation summaries. These summaries belong to one conversation, including guest text
conversations; they do not enable user-global long-term memory or change its consent policy.
LangGraph checkpoints remain bounded to `run_id`.

The default policy uses up to 16,000 estimated recent-history tokens and a 64-message defensive
ceiling, with up to 32,000 estimated tokens at the assembled **text** boundary. Recent causal turns
remain verbatim. Older history compacts as needed before an answer, targeting roughly 8,000 recent
tokens afterward; summary bodies are bounded to 3,000 tokens and relevant file notes to 4,000.
Mandatory instructions and the current question are protected. Oversized current messages are
refused before admission. Native file/image token processing remains governed by provider limits
and existing attachment byte/count limits; text estimates are not exact provider token counts.

Preliminary history/summary selection uses the existing local `o200k_base` cache with a
conservative UTF-8 byte fallback. At the final ordinary/workspace answer boundary, the selected
model determines the tokenizer; unknown mappings or unavailable/corrupt cache files use an explicit
UTF-8 byte estimate. The rendered-text count includes prompt overhead and any incomplete-context
disclosure. Internal delivery metadata identifies the model, counting method, and text-only scope. Summarization uses at most two bounded calls, each with a 30-second timeout.
Failures retain valid summaries and bounded recent text, mark omitted history, and continue
without inventing a replacement summary. Cancellation prevents installing the result.

Summaries record their exact covered prefix, digest, policy version, model, and source-message
references. Replay invalidates summaries when it changes their covered prefix. Source-derived
details are filtered when their supporting file is removed or document authorization is revoked.
Historical preparation, current delivery, and claim correctness remain separate. Existing legacy
delivery is unknown; the absence of current excerpts is not proof of an earlier grounding failure.
Private input manifests record counts/references, not raw prompts in general logs. Jev remains on
its existing human/assistant-only four-message, 4,000-character-per-message boundary.

## Models, public interfaces, and activity

`MY_AGENTS_SUMMARIZATION_MODEL` defaults to `gpt-6-luna`. Registered users can override it through
`GET/PATCH /summarization/preferences`; PATCH requires `{ "summarization_model": <supported ID or
null> }`, where null follows the deployment default. The four response fields match assistant
preferences: `customizable`, `default_model`, `selected_model`, `effective_model`. Guest GET is
locked and PATCH is 403. `GET /capabilities/summarization-models` serves the three exposed assistant
models as `{id,name}`, plus `recommended_model=gpt-6-luna`; all six supported IDs remain API-valid.
Luna is strongly recommended in Settings, not enforced. Standard reasoning uses the editable
model-default map independently of the answer's requested reasoning preferences.

Admission captures model choices. Automatic file access can leave `run_started.assistant_model`
unknown until `run_model_resolved` supplies the actual answer model and effective reasoning.
Compaction emits persisted/SSE `context_compaction_started`, `context_compaction_completed`, or
`context_compaction_failed` before retrieval/answering. Payloads contain policy, model, count and
fallback metadata; they never expose summary bodies. Terminal counts describe messages compacted.
Cancellation remains a terminal run state, not a continuing fallback answer.

An optional UUID `client_request_id` correlates the request, started event, and run list. It is
unique per user and cannot be reused to create another run (409). It is a correlation mechanism,
not automatic replay: after a lost admission acknowledgement the UI finds that exact ID and never
automatically resends. Legacy runs without an ID remain ambiguous.

## Attachments and retention

```mermaid
flowchart LR
    Submit["Message and explicit attachments"] --> Admit["Atomic admission and message/file links"]
    Admit --> Clear["Frontend clears submitted selection"]
    Admit --> Inspect["Authorized workspace inspection"]
    Inspect --> Notes["Conversation-scoped observations and coverage"]
    Followup["Later message"] --> Context["Recent history, summary and permitted file notes"]
    Notes --> Context
    Context --> Resolve["Answer from notes or reopen a specific original"]
```

The original bytes remain in OpenAI Files, with a seven-day fixed upload-relative expiry for new
uploads. Existing uploads keep their recorded expiry. Workspaces retain the 20-minute idle lifetime
and can be recreated from an available original. Unsubmitted uploads become unavailable after
24 hours and enter cleanup. Compact notes survive normal original expiry until conversation
deletion. They describe provider-reported findings/coverage, not independently verified facts or a
complete copy of the original. Failed runs do not supply notes to future context.

Registered-user recall uses only submitted files owned by this conversation. Explicit current
attachments take precedence. Ordinary discussion uses the selected assistant; exact checking or
editing reopens authorized originals through the separately configured workspace model. Praise or
unrelated requests do not reopen files. V2 `attachment_selection` resolves ambiguity using offered
IDs; `access=notes` can use retained discussion after expiry, while `access=original` requires an
available source. Options include filename, category, availability, and optional size/date.
If exact work needs an expired file, explain the limitation rather than guessing its contents.
Workspace file storage, notes, and recall are forbidden to guests.

`MessageResponse.attachments` contains explicit user-message associations, with empty lists for
legacy/unattached/guest messages. Run attachments also record automatic reuse. A queue owns its
snapshot; acknowledgement clears only that submission's IDs. Pre-admission failures preserve the
draft/selection, and failures after admission do not silently resubmit. Capability fields declare
the original TTL, abandoned-upload TTL, conversation note retention, and automatic-recall support.

Removing a file immediately revokes application access, removes its notes, invalidates derived
summaries, and schedules original/container-copy/derived-artifact erasure. Historical chat text is
still visible; affected source replies are excluded from future context. Conversation deletion
removes the transcript and derived records and schedules provider cleanup before losing IDs.
Active runs must be stopped before file/conversation removal. Cleanup is a durable leased outbox
with a minute-period sweeper and retries; provider outages can delay physical deletion. No raw
uploaded bytes are stored in Product DB. Unselected input copies are removed before workspace
reuse, while unrelated generated outputs retain their existing short lifetime.

## Rollout and verification

Apply Alembic `20260930_0036` before starting an existing DB. Drain/cancel old V2 waiting runs before
deploying graph version `general-assistant-checkpoint-v3`; incompatible checkpoints use the existing
safe failure contract. `MY_AGENTS_CONTEXT_CONTINUITY_ENABLED=false` restores bounded legacy history
and disables automatic recall; existing explicit workspace inputs remain supported.

Offline tests cover context packing, prefix reuse/failure, preferences, guest boundaries,
admission correlation, automatic recall, semantic selection/resume, cleanup and transaction failure
recovery. Verification adapters and the live frontend proxy prove contracts/workflows, not model
fidelity. Actual summary quality, cost, and latency require representative provider evaluation.
The existing request-bound SSE execution limitation remains; this change does not add a durable
background answer executor. Generated-file durability and attachment-free artifact generation
remain separate work.

Implementation-time evidence (2026-09-30): backend full suite 786 passed / 13 skipped; final
affected checks 48 passed / 1 skipped; lint/format passed. Frontend 425 unit tests and final
browser suite 229 passed / 2 skipped / 2 independently confirmed baseline failures. Real
BFF/UI stand-in checks include original/notes selection and refresh recovery. No real provider
fidelity or deployed-release claim is made. The owner subsequently reported browser testing and
approved commit/push. Publication evidence is preserved in the
[completion record](../completed/conversation-continuity.md).

## Session evidence for earlier decisions

Conversation history can preserve an assistant's explanation without preserving the inputs that
supported it. The backend therefore keeps a private, versioned `agent_runs.session_evidence_json`
record for successful ordinary and workspace answer calls. This complements the metadata-only
`context_delivery_json`; it is not returned by run APIs, SSE, or OpenAPI models.

Ordinary answer capture happens **after final prompt packing**. It retains the supplied visible
document excerpts, hashes, message references, input-truncation state, memory count, and the
recorded source-selection choice/reported reason. A web-search block can establish that a tool
call was observed; missing blocks do not prove the tool was not used. Native workspace file bytes,
hosted tool internals, stored-memory bodies, raw provider traces, and private reasoning are not
copied. Workspace calls retain the packed text-document context; their existing attachment notes
and native-input manifest remain responsible for file observations and attachment provenance.

The response node uses a runtime-only Product DB adapter to inject relevant prior-turn evidence
immediately before answer composition. Recalled raw excerpts do not enter graph updates,
checkpoints, compaction inputs, or Jev decisions. Ordinary provider tracing is disabled for calls
containing these historical excerpts. SQL exception parameters are hidden. Source-selectors still
see their existing bounded human/assistant context; this feature does not widen provider scope.

Records are recalled only for completed runs in the same owner's conversation, before the current
user-message boundary. This excludes an old answer and future turns during regeneration, even
before successful replay prunes them. Current document authorization is rechecked; removed or
revoked evidence also excludes its linked assistant claim and summary entries. Source dependencies
carry forward into follow-up records even when no new retrieval occurs, so removing a source also
withholds answers derived from its historical evidence. Conversation/run
pruning and guest cleanup remove records with their owning run. A failed/cancelled run can have an
internal boundary record if its provider returned before cancellation, but never supplies recall.

The initial bounded policy considers the latest 64 eligible completed turns and supplies at most
four, preferring the latest and lexical matches in prior replies. Recall uses at most 6,000 locally
estimated tokens (also capped to one quarter of the total input budget); final provider packing can
remove it with the existing incomplete-context disclosure. Each persisted record is bounded to
128 KiB, with at most 32 retained excerpts; omitted/truncated excerpts are explicit. Recall windows
are capped at 1,600 characters per excerpt. This is not unlimited or exhaustive session memory.
Older decisions can still appear in source-linked conversation summaries, but exact older evidence
outside this window is not automatically loaded.

The assistant is instructed to distinguish recorded inputs, user statements, prior assistant claims,
and inferred rationale. Supplied text proves availability at the successful call boundary, not that
the model noticed or used it. Later corrections and re-ingestion never rewrite the original record.
Legacy runs without records remain unknown; malformed records are withheld. A partial/omitted
record cannot establish that a fact was absent. Current source documents must be re-read for
current-fact verification; historical snippets describe the earlier turn only.

Apply migration `20261018_0043` with `uv run alembic upgrade head` before restarting an existing API
or optional answer worker. The column is nullable and no backfill invents earlier evidence. There
is no new worker, dependency, public endpoint, frontend change, or environment variable. Existing
`MY_AGENTS_CONTEXT_CONTINUITY_ENABLED=false` disables capture and recall. Offline coverage lives
in `tests/test_session_evidence.py`; real-provider explanation fidelity is a separate evaluation.
