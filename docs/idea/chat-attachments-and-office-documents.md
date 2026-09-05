# Chat attachments and Office document support idea

This note captures the product and architecture discussion from 2026-06-09 about adding Excel/PowerPoint document support and allowing users to send files together with a chat message.

## Status reconciliation — 2026-09-05

This is a **partially superseded design**, not the current attachment API contract.
Durable Office parsing and temporary conversation attachments are implemented; see the
[workspace completion record](../completed/document-workspace.md) and
[current workspace contract](../product-chat-service/en/25-openai-document-workspace.md).
The shipped attachment flow uploads consented files first, then sends `attachment_ids` on the
ordinary run request. It uses the provider-hosted workspace, not the multipart run endpoint,
local temporary chunk table, or shared ContextForge attachment search proposed below.

Generic `source_location_json` already exists on durable chunks; adding that field is not new work.
Keep this note outside completed because original KB-file retention, uniform richer provenance,
attachment-to-KB promotion, and combined KB/temporary-chunk retrieval are not all implemented.
The remaining sections preserve the original options and open extensions; they are not
instructions to replace the shipped API or promises of supported current behavior.

## User questions

1. Should `my-agents` support Excel and PowerPoint documents?
2. What is the cleanest path to add those file types?
3. If the chatbox later supports file upload, does it need a separate service path?
4. Can users still send a message and files at the same time instead of uploading files in a separate manual step?

## Short answer

Yes, support both capabilities, but keep two product lifecycles separate:

- **Knowledge-base documents** are durable sources that can be selected, ingested, retrieved, cited, and reused later.
- **Chat attachments** are temporary conversation/run sources used for the current message or conversation unless the user explicitly saves them to a knowledge base.

The user experience can still be one action:

```text
message + files -> Send
```

The backend should model that as a conversation run with temporary attachments, not as a hidden knowledge-base upload.

## Historical baseline and design context

The current durable document path is:

```text
KnowledgeBase -> Document -> ExtractionRun -> Chunks/entities/embeddings -> ContextForge -> Answer with citations
```

Today the upload dispatch supports PDF, Markdown, plain text, `.xlsx`, `.pptx`, and `.docx`. The roadmap still points toward original-file retention, parser provider boundaries, richer parse artifacts, canonical Markdown, and layout-aware chunks beyond the current local parsers.

That direction should be reused for Office documents instead of adding ad-hoc parsing directly inside conversation routes.

## Persistent Office documents

### Excel

Start with `.xlsx` only. Defer old `.xls` and macro-heavy `.xlsm` until there is a specific reason to support them.

Excel parsing should preserve spreadsheet structure instead of flattening everything into loose text. The canonical artifact should look roughly like Markdown tables plus sheet/range metadata:

```markdown
# Sheet: Sales Q1

Range: A1:D20

| Month | Revenue | Cost | Margin |
| --- | ---: | ---: | ---: |
| Jan | 10000 | 7000 | 3000 |
| Feb | 12000 | 8000 | 4000 |
```

Important provenance:

```text
sheet_name
cell_range
row_range
column_headers
```

Citation target:

```text
sales.xlsx, Sheet "Sales Q1", A1:D20
```

### PowerPoint

Start with `.pptx` only. Defer old binary `.ppt`.

PowerPoint parsing should preserve slide boundaries:

```markdown
# Slide 4: Deployment Architecture

- Frontend runs on Vercel
- Backend runs on Render
- Database is Neon Postgres

Speaker notes:
...
```

Important provenance:

```text
slide_number
slide_title
shape/table index
speaker_notes if extracted
```

Citation target:

```text
deck.pptx, slide 4
```

## Chatbox attachments

Chatbox files should not become normal knowledge-base documents by default.

They should use a separate product lifecycle:

```text
ConversationAttachment -> Parse -> Temporary chunks/context -> ConversationRun -> Answer
```

This keeps user expectations clear:

- temporary files do not appear in the knowledge-base list;
- temporary files are not reusable as durable sources unless explicitly saved;
- temporary citations can be labeled as temporary attachments;
- deletion/expiry rules are different from durable KB retention;
- group visibility cannot accidentally leak through hidden KB writes.

## Single-submit UX

The frontend should allow one submit action:

```text
[message] + [files] -> Send
```

The backend can expose a multipart conversation-run endpoint, for example:

```text
POST /conversations/{conversation_id}/runs/stream-with-attachments
Content-Type: multipart/form-data
```

Fields:

```text
message: string
knowledge_base_selection: JSON string, optional
files: UploadFile[]
```

Example request shape:

```text
message = "Can you explain this spreadsheet?"
knowledge_base_selection = {"mode":"selected","knowledge_base_ids":["kb_123"]}
files[] = sales.xlsx
files[] = deck.pptx
```

The response can remain Server-Sent Events:

```text
event: run_started
event: user_message_stored
event: attachment_received
event: attachment_parsing
event: attachment_context_ready
event: retrieval_completed
event: answer_delta
event: run_completed
```

This gives users simultaneous message+file submission without forcing chat attachments into the knowledge-base model.

## Suggested model sketch

```text
conversation_attachments
- id
- conversation_id
- user_message_id
- run_id nullable
- owner_user_id
- filename
- content_type
- byte_size
- sha256
- source_type
- parser_name
- status
- retention_expires_at
- created_at
```

Optional derived chunks:

```text
conversation_attachment_chunks
- id
- attachment_id
- ordinal
- content
- source_location_json
- embedding_json nullable
- embedding_vector nullable
- created_at
```

The exact storage shape can change, but the boundary should stay stable: conversation attachments are not durable KB documents.

## Shared parsing layer

Use shared parser infrastructure beneath both durable KB documents and temporary chat attachments:

```text
my_agents/knowledge/parsing/
- providers.py
- text.py
- pdf.py
- office_excel.py
- office_powerpoint.py
- artifacts.py
```

Shared concepts:

```text
ParserProvider
ParsedDocumentArtifact
canonical Markdown
source metadata
source_location_json
chunking
safe parser errors
```

Different higher-level services:

```text
KnowledgeDocumentService
- durable KB document lifecycle
- reusable retrieval source
- explicit KB permissions
- persistent citations

ConversationAttachmentService
- temporary conversation/run lifecycle
- run-scoped context
- expiry/cleanup
- optional Save to Knowledge Base action
```

## ContextForge integration

ContextForge should eventually receive two authorized source pools for a run:

```text
durable KB sources + ephemeral attachment sources
```

The assistant can cite both, but the UI should label them differently:

```text
Sources:
- sales.xlsx, Sheet "Q1", A1:D20 (temporary attachment)
- deck.pptx, Slide 4 (temporary attachment)
- Company Handbook, page 12 (knowledge base)
```

Permission and source policy remain backend-owned. Attachments should be scoped to the authenticated user and the conversation/run that created them.

## Recommended phases

1. **Office parser support for durable KB documents**
   - Add `.xlsx`, `.pptx`, and `.docx` parser support.
   - Convert to canonical Markdown.
   - Preserve sheet/slide provenance where possible.
   - Store as current document content initially.
   - Add parser, upload, ingestion, retrieval, and citation tests.

2. **Richer provenance metadata**
   - Add a generic `source_location_json` or equivalent chunk-level location field.
   - Represent sheet ranges, slide numbers, sections, tables, and pages consistently.

3. **Parse artifacts and original-file retention**
   - Implement the existing plan for original upload storage and parse artifacts.
   - Cache parser output by source hash + provider/version/mode.
   - Support re-extract and re-ingest from retained originals.

4. **Multipart conversation-run attachments**
   - Add a multipart streaming run endpoint for message + files.
   - Store temporary attachment metadata.
   - Parse attachments before assistant invocation or through a run-scoped parsing stage.
   - Emit redacted SSE events for attachment progress.

5. **Attachment retrieval and citations**
   - Feed temporary attachment chunks into the same retrieval/context-packing path as authorized KB chunks.
   - Label citations as temporary attachment sources.
   - Keep raw content out of run events.

6. **Save to Knowledge Base**
   - Add an explicit user action to promote a temporary attachment into a durable KB document.
   - Do not make this automatic.

## Non-goals for the first slice

- Do not support every Office format at once.
- Do not support old binary `.ppt` or `.xls` before `.pptx` and `.xlsx` are stable.
- Do not make chat attachments secretly create knowledge-base documents.
- Do not bypass ContextForge or document/source authorization rules.
- Do not expose raw attachment text, raw prompts, or hidden reasoning in run events.
- Do not add a cloud parser dependency as the only path; offline tests and local parsing should keep working.

## Open questions

- Should temporary attachments be retained for only one run, the whole conversation, or a short expiry window?
- Should attachment chunks get embeddings immediately, or should small files be packed directly into context first?
- What file size limits should apply separately to KB documents and chat attachments?
- Should frontend show a visible “temporary attachment” badge beside every citation?
- When saving a temporary attachment to a KB, should the existing parse artifact be reused or should the durable ingestion pipeline re-parse from the original file?
