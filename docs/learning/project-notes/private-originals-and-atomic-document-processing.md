---
created: 2026-10-18
updated: 2026-10-18
status: active
topics:
  - object-storage
  - document-ingestion
  - provenance
  - transactions
related_code:
  - my_agents/api/document_files.py
  - my_agents/storage/objects.py
  - my_agents/storage/lifecycle.py
  - my_agents/knowledge/document_processing.py
  - my_agents/knowledge/extraction.py
  - my_agents/persistence/database.py
---

# Private originals and atomic document processing

## The three things called a document

A stored document has three different representations:

| Representation | Purpose | Owner |
| --- | --- | --- |
| Original file bytes | Download and repeat parsing later | Private object storage |
| Active extracted/processed text | Stable content used for answers and source previews | Product DB |
| Chunks, vectors, and metadata | Derived search index that can be rebuilt | Product DB |

Keeping extracted text does not preserve the original PDF or Office file. It loses layout, embedded
images, and other parser inputs. An original must therefore be retained when it is first uploaded;
old extracted text cannot safely be labelled as a recovered original.

## Why upload first, then verify and seal

A presigned PUT URL gives temporary permission to write one object. It is not a one-use ticket.
The user can retry or overwrite that staging object while the URL is valid. If ingestion trusted
that mutable key, the bytes could change after validation.

The backend reads bounded bytes, compares their size and SHA-256 with the admission, and writes
those exact verified bytes to a different server-owned original key. The browser cannot write that
key. Only then does the application attach it to a document. This preserves a stable input for
later processing and audit.

## Why reprocessing needs a candidate

Suppose a document already answers questions correctly and the owner tries AI-assisted layout
cleanup. Deleting its chunks before the model returns would make the document disappear from
search if the provider timed out.

Instead, prepare candidate text, mapped source sections, metadata, and vectors while the old
version stays active. Replace the active text and index in one final database transaction. A
provider failure or failed final commit leaves the old version usable. Progress commits must not
accidentally activate the candidate; the implementation uses a detached working document.

AI output is a proposed formatting change. It does not become more authoritative than the
original. Fidelity checks preserve words, punctuation, order, code, and tables, while provenance
records the recipe, model, input/output hashes, and section-level mapping limits.

## Why deletion uses an outbox

PostgreSQL and R2 cannot share an ordinary SQL transaction. Deleting the DB row and then hoping an
R2 call succeeds can leave unreachable files. Deleting R2 first can destroy an original even if
the DB transaction later rolls back.

A deletion transaction instead revokes the document/file and inserts a cleanup job together. The
API's background loop retries the external delete. The job remains durable across API restarts,
although it only runs while the API is up. Capacity is released after confirmed erasure. Staging
cleanup waits until all upload grants have expired so a late PUT cannot recreate deleted bytes.
Server-side failed writes have a separate five-minute grace for bounded in-flight requests. An R2
one-day lifecycle rule for `staging/` is a fallback for orphaned bytes, not a replacement for the
DB cleanup ledger or permission-aware document deletion.

This small application can host the loop in the API. A separate paid worker is not required for
the file-storage feature; crash-resumable model processing and larger-scale worker isolation are
separate concerns.

## Debugging lessons from the first integration checks

The first real storage round trip exposed a configuration distinction: an S3 endpoint identifies
the account service, while the bucket is a separate SDK argument. Appending the bucket name to
both can address the wrong resource. Correcting only that suffix allowed the scoped credentials
to pass `HeadBucket`; authenticated upload/download checks then verified byte identity and denied
unsigned access. A dashboard API permission check alone would not have established S3 readiness.

A small live AI cleanup probe was rejected by the fidelity guard. The model had not satisfied the
verbatim-content contract, and the candidate was never written to the database. Relaxing that
validator would conceal information loss. The mitigation is to keep protected code/table sections
out of the model-editable path, copy them directly, and ask the model to adjust only prose layout.
The implementation records that protection with a machine-code warning. A real `gpt-6-luna`
retry on a synthetic Korean four-principle passage plus indented Python preserved all four
principles and kept the code byte-identical. Provenance identified OpenAI, two sections, and the
section-location/protected-content warnings. The first safe rejection and this successful small
probe establish specific boundaries; they do not establish broad cleanup quality or latency.

Independent review also found that allowing a content edit while extraction was queued could let
the old candidate replace the user's new text. Both queued and running extraction now block
content PATCH, while title-only edits remain allowed. This must be checked at admission and final
activation, rather than relying only on a UI-disabled button or a check after a worker starts.

A later async test stopped at 95% progress even though its permission checks passed. A forced
interleaving reproduced the cause: SQLAlchemy's `StaticPool` gave polling and worker sessions the
same in-memory SQLite connection, so closing the poll session could roll back the worker's pending
completion. Extending the timeout would not repair that isolation error. Threaded tests now use
file-backed SQLite and separate connections; repeated tests passed without changing the deadline.

R2 also needs persistent ownership records for a separate reason: restarting an in-memory Product
DB would lose the mapping and cleanup ledger while remote files survived. The API now refuses R2
original writes against an in-memory database and reports direct uploads unavailable. PostgreSQL
or file-backed SQLite preserves the control records as well as supporting independent sessions.

File SQLite fixed the shared-connection test issue but exposed a second concurrency problem in
real uploads: rollback journaling blocked an auth/session reader during a large atomic index write.
The API now enables and verifies WAL on file SQLite connections, preserving `FULL` synchronization
and the five-second busy timeout. A regression forces a write larger than 5 MiB and proves a
separate auth reader sees the previous committed state, then the new state after commit.

WAL improves reader/writer overlap; it does not provide concurrent writers. Keep this local option
on a persistent local filesystem and use PostgreSQL when hosting requires separate machines or
persistent remote database ownership. The [SQLite WAL reference](https://sqlite.org/wal.html)
explains the one-writer and shared-memory/filesystem boundaries. Increasing a timeout alone would
not address either the shared-connection rollback or the blocked-reader cause.

The large plain-text upload also exposed too many tiny index units. A 4.8 MB Korean fixture had
200,000 sentence-sized chunks, multiplying embedding work and database rows. Packing adjacent
sentences into exact source spans around 1,500 characters reduced that same fixture to 1,334
chunks, with complete source coverage and a maximum chunk length of 1,499 characters. Spreadsheet
and presentation records remain isolated instead of using this packing rule across records.

The measured chunker itself took 0.231 seconds before and 0.300 seconds after. The useful result
is fewer downstream embedding/index operations, not a claim that the CPU chunker became faster.
A local concurrent API/R2 check completed the 4.8 MB and 31-byte files together in 6.03 seconds;
that single scenario does not establish production performance.

These checks used synthetic content. The final browser walkthrough exercised persisted settings,
concurrent small/4.8 MB uploads, original hash/provenance, and document deletion without API/page
errors or application credentials sent to R2. The full staging-expiry wait and production deployment
remain separate evidence. The earlier synthetic original and staging bytes were removed, with the
durable staging cleanup record left to complete its normal idempotent path.

## A useful local exercise

1. Upload a synthetic document and note its original hash and active processing provenance.
2. Reprocess it successfully and verify that its original hash stays unchanged.
3. Make the processing test double fail and verify that the previous text/chunks remain active.
4. Delete the document while the storage double rejects deletion once; verify the cleanup job
   remains and the next attempt erases the object and releases capacity.

Use offline fixtures or an isolated development bucket. Record which steps actually ran and their
results in implementation tracking; preserve the distinction between local and production checks.

The [product contract](../../product-chat-service/44-private-document-files-and-preferences.md)
owns exact routes, limits, consent, and setup. Current status belongs to implementation tracking.

## Revision history

- 2026-10-18: Explain original/active/index separation, staging grants, candidate activation,
  and durable cleanup for the active private-storage implementation. Added the S3 endpoint,
  live fidelity rejection/successful protected-span retry, queued-edit race, and SQLite polling
  isolation/persistent-ownership, WAL blocked-reader, and exact-source text-packing lessons.
  Recorded bounded live API/browser evidence without claiming hosted performance.
