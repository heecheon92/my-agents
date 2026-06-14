# Knowledge Base Path OpenAPI Handoff

This is the backend-to-frontend handoff for the KB-first document,
group-upload staging, and chat-source path.
The filtered OpenAPI artifact is:

- `docs/product-chat-service/en/12-knowledge-base-path-openapi-handoff.json`

## Product contract

A knowledge base is the user-facing searchable document library. The frontend flow should be:

1. Create or choose a knowledge base.
2. For personal content, add text/PDF/Markdown/plain-text files to that knowledge base.
3. For group content that needs approval, call
   `POST /knowledge-bases/team-upload-staging` to create or reuse the
   uploader's hidden staging KB, then write the source document into that
   staging KB.
4. Submit the staged document with
   `POST /groups/{group_id}/publish-requests` and approve/reject it with
   the group review endpoints.
5. Ingest the approved group copy inside the target group knowledge base.
6. In chat, choose either All KBs or one or more selected KBs as the assistant retrieval source.

## Canonical user-facing routes

Use the KB-nested and group publish routes for the product UI:

- `GET /knowledge-bases`
- `POST /knowledge-bases`
- `POST /knowledge-bases/team-upload-staging`
- `GET /knowledge-bases/{knowledge_base_id}`
- `GET /knowledge-bases/{knowledge_base_id}/documents`
- `POST /knowledge-bases/{knowledge_base_id}/documents`
- `POST /knowledge-bases/{knowledge_base_id}/documents/upload`
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest`
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest/async`
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs`
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}`
- `GET /groups/{group_id}/publish-requests`
- `POST /groups/{group_id}/publish-requests`
- `POST /groups/{group_id}/publish-requests/{request_id}/approve`
- `POST /groups/{group_id}/publish-requests/{request_id}/reject`

Compatibility routes `/documents` and `/documents/upload` still exist
for standalone/developer usage,
but write calls require an authorized `knowledge_base_id`. They are not the primary product UX.

## Group upload and publish-request rules

- `POST /knowledge-bases/team-upload-staging` returns a hidden personal
  KB with `purpose=team_upload_staging`.
- Staging KBs are writable by direct KB-scoped document create/upload
  routes, but are excluded from normal KB lists, chat source selection,
  and ordinary retrieval.
- Document-copy publish requests require `source_document_id` plus `target_knowledge_base_id`.
- Whole-KB publish requests require `source_knowledge_base_id` and
  target the group directly; they must not send
  `target_knowledge_base_id`.
- `KnowledgePublishRequestResponse` is the canonical UI payload for
  pending/approved/rejected group publication state.
- Approval copies the source into the target group KB and ingests the
  group copy; retrieval should use the approved group copy, not the
  staging source.

## Chat source selection

`ConversationRunRequest` accepts:

```json
{
  "message": "What do my docs say?",
  "knowledge_base_selection": {
    "mode": "selected",
    "knowledge_base_ids": ["kb_..."]
  }
}
```

Rules:

- `mode: "all"` searches all authorized KBs and must not include IDs.
- `mode: "selected"` searches only the provided KB IDs as a hard retrieval boundary.
- `mode: "selected"` with no IDs returns `422`.
- `mode: "all"` with IDs returns `422`.
- Nonexistent or unauthorized selected KB IDs return `404`.
- Staging KB IDs are not valid chat-selection targets and must stay out of normal retrieval.

The same selection metadata is exposed on sync runs, stream `run_completed` payloads, run detail,
run history summaries, and run events:

- `knowledge_base_selection`
- `resolved_knowledge_base_count`

## Related references

- `docs/product-chat-service/en/18-team-upload-staging-flow.md`
- `docs/product-chat-service/en/11-v1-phase-0-contract-freeze-evidence-map.md`

## Verification evidence

Backend G003 OpenAPI gate is locked by:

- `tests/test_kb_openapi_contract.py`
- `uv run ruff check . --no-cache`
- `uv run ruff format --check .`
- `uv run pytest -q`
