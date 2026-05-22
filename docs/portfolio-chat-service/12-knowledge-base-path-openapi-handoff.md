# Knowledge Base Path OpenAPI Handoff

This is the backend-to-frontend handoff for the KB-first document and chat-source path.
The filtered OpenAPI artifact is:

- `docs/portfolio-chat-service/12-knowledge-base-path-openapi-handoff.json`

## Product contract

A knowledge base is the user-facing searchable document library. The frontend flow should be:

1. Create or choose a knowledge base.
2. Add text/PDF/Markdown/plain-text files to that knowledge base.
3. Ingest the document inside that knowledge base.
4. In chat, choose either **All KBs** or one or more selected KBs as the assistant retrieval source.

## Canonical user-facing routes

Use the KB-nested routes for the product UI:

- `GET /knowledge-bases`
- `POST /knowledge-bases`
- `GET /knowledge-bases/{knowledge_base_id}`
- `GET /knowledge-bases/{knowledge_base_id}/documents`
- `POST /knowledge-bases/{knowledge_base_id}/documents`
- `POST /knowledge-bases/{knowledge_base_id}/documents/upload`
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest`
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest/async`
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs`
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}`

Compatibility routes `/documents` and `/documents/upload` still exist for standalone/developer usage,
but write calls require an authorized `knowledge_base_id`. They are not the primary product UX.

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

The same selection metadata is exposed on sync runs, stream `run_completed` payloads, run detail,
run history summaries, and run events:

- `knowledge_base_selection`
- `resolved_knowledge_base_count`

## Verification evidence

Backend G003 OpenAPI gate is locked by:

- `tests/test_kb_openapi_contract.py`
- `uv run ruff check . --no-cache`
- `uv run ruff format --check .`
- `uv run pytest -q`

