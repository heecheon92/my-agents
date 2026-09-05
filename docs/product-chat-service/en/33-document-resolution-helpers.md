# Pure document resolution helpers

RetrievalService remains the public facade in my_agents/knowledge/retrieval.py. Authorization,
database queries, vector/BM25 execution, full-document ranges and transactions stay there.

Compact document-option and target-resolution value objects live in retrieval_contracts.py;
the existing retrieval imports explicitly re-export them. Pure filename/title normalization,
matching, scoring and candidate ordering live in document_resolution.py and accept only
already-authorized metadata. That module must not query a database or decide access.

This extraction preserves algorithm bodies, constants, thresholds, scores, tie-breakers and
fallbacks. It does not optimize ranking or change which documents are selected. Tests of scoring
can now import the pure module; integration tests should continue using RetrievalService so
permission and full-document behavior remain covered.
