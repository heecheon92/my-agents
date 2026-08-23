---
created: 2026-08-24
updated: 2026-08-24
status: active
topics:
  - rag
  - full-document-retrieval
  - langgraph
  - permissions
  - citations
related_code:
  - my_agents/agents/general_assistant/graph.py
  - my_agents/agents/general_assistant/rag_retrieval.py
  - my_agents/agents/rag_agent/retrieval.py
  - my_agents/knowledge/retrieval.py
  - tests/test_full_document_retrieval.py
---

# Full-document retrieval: coverage, authorization, and checkpoint safety

Ranked retrieval and full-document retrieval solve different problems.

- Semantic/vector and keyword search answer: “Which passages are most relevant?”
- Full-document retrieval answers: “Did the agent inspect every part of the requested
  document?”

A few excellent chunks may still miss a requirement in an unrelated section. Conversely,
injecting an entire file for every question is slower, more expensive, and harder to secure.
The implementation therefore keeps ranked retrieval as the default and adds an explicit,
opt-in comprehensive-document path.

## 1. Intent is part of the safety boundary

The graph enters this path only when all three conditions are true:

1. `MY_AGENTS_FULL_DOCUMENT_RETRIEVAL_ENABLED=true`;
2. the source-selection gate chose `knowledge_base`;
3. the prompt contains both a completeness hint and a document task.

Examples that activate it include “review the entire document and identify every
requirement” and “문서 전체를 빠짐없이 검토해줘.” “Summarize this document” stays on the
normal ranked-retrieval path. A weak semantic/BM25 result does not silently escalate to a
full read.

This explicit gate matters because comprehensive reading has a larger context and latency
cost. It also makes the product promise honest: the response can claim whole-document
coverage only when the graph deliberately took the coverage path.

## 2. The graph resolves identity before loading text

```mermaid
flowchart TD
    Start["Explicit comprehensive request"] --> Resolve["Resolve authorized document metadata"]
    Resolve -->|One target| Prepare["Read range and prepare compact coverage"]
    Resolve -->|Several targets| Select["document_selection interrupt"]
    Select --> Resume["Resume with typed document_id"]
    Resume --> Resolve
    Resolve -->|No authorized target| Stop["Insufficient evidence"]
    Prepare --> Memory["Retrieve governed user memory"]
    Memory --> Respond["Re-read range inside respond_full_document"]
    Respond --> Persist["Persist reply, citations, coverage event"]
```

Resolution works from metadata first. It accepts the exact selected/replayed document, the
only eligible document, or one unique normalized title/source-filename match. If several
documents remain, it reuses the existing versioned `document_selection` interaction. The
resume answer is authorized again before reading any body text.

“Eligible” is deliberately narrower than ordinary ambient retrieval. It includes currently
authorized user-controllable personal/group documents, including explicit document read
grants, within the selected KB scope. System KBs and their documents are excluded from
automatic targets and selection options, even though ambient system knowledge can still
support normal answers.

## 3. Complete and partial are different claims

The service reads `DocumentModel.content`, which is normalized extracted text rather than
the original PDF/DOCX bytes. A read result uses a half-open character range:

```text
[start_offset, end_offset)
```

With default settings:

- `total_chars <= 24_000` reads the whole current text and reports `mode=complete`;
- a larger document reads `[0, 12_000)` and reports `mode=partial`;
- the service calculates an internal canonical-decimal next cursor but does not expose it
  in the V1 public response.

The public `document_coverage` contains document identity, title/source filename,
start/end offsets, and total characters. Citations come only from authorized chunks that
overlap the range. A partial response receives a deterministic Korean or English notice
before the generated answer, so it cannot pretend to be a complete review.

The limits are characters, not model tokens. They provide deterministic application bounds,
but they do not predict provider token usage exactly.

## 4. Why the body is read twice

`prepare_full_document_read` first checks that authorized text and valid overlapping chunk
provenance exist. It then places only compact metadata, IDs, and coverage in graph state and
clears `retrieved_context`.

`respond_full_document` revalidates authorization and reads the same range again immediately
before composition. The raw range is passed as a local response-provider override and is
never returned as node state. LangSmith tracing is disabled around that provider call.

This design keeps four persistence/diagnostic boundaries distinct:

| Boundary | What it keeps | What it excludes |
| --- | --- | --- |
| LangGraph checkpoint | selected document ID, coverage, citation IDs, internal cursor | normalized document body |
| Product DB event | `full_document_read` mode, document metadata, offsets, total chars, latency | body and internal cursor |
| Application full-body log path | compact graph/retrieval metadata | full-body provider override |
| Provider tracing | disabled for this response call | body-bearing trace |

Existing opt-in DEBUG retrieval logging can still show its normal bounded 240-character
citation-chunk previews. That is not the full-body override and should not be confused with
storing an entire document.

## 5. Provenance is checked against the current revision

Offsets belong to the current normalized `DocumentModel.content`; they are not permanent
coordinates in the original upload. The service verifies that overlapping chunks have valid
offsets and still match the current content. It also rechecks the prepared start/end/total
values before composing.

This avoids confidently citing stale chunks, but it is not document versioning. There is no
durable content revision or hash in the coverage contract yet. If body/chunk provenance has
drifted, if more than 100 chunks overlap one read, or if the document becomes unavailable,
the graph returns insufficient evidence instead of producing an uncited comprehensive
answer.

## 6. Streaming and replay preserve the honest claim

Full-document provider tokens are buffered. For a partial read, the backend first prepends
the deterministic coverage notice and only then emits `answer_delta`; concatenated deltas
equal `run_completed.reply`. A typed `full_document_read` event is emitted before
`answer_composed`, and run detail reconstructs `document_coverage` from that safe event.

Replay reads the original `full_document_read` event and preselects that exact document. If
the target was deleted or authorization was revoked, replay warns that the source is
unavailable and does not substitute another document that happens to be accessible later.
This is replay fidelity: regenerate from the same source boundary or disclose that it cannot
be reproduced.

## 7. What remains for true large-document coverage

The current large-document path is intentionally one bounded first range. It does not yet:

- loop through every continuation cursor;
- keep a range-by-range coverage ledger and intermediate summaries;
- synthesize those summaries into a final whole-document result;
- estimate provider-aware token budgets;
- bind coverage offsets to a durable document revision/hash.

Those steps are the next milestone. Until they exist, `partial` is a meaningful product
contract, not a temporary implementation detail that the UI or responder may hide.

## Revision history

- 2026-08-24: Created after implementing the opt-in bounded full-document retrieval path.
