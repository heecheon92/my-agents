# Performance optimization records

This folder keeps durable performance ledgers for `my-agents`. Use it for measured
before/after optimization work where preserving quality is part of the result.

## English logs

- [Ingestion performance log](./en/ingestion-performance-log.md)
- [RAG retrieval performance log](./en/rag-retrieval-performance-log.md)

## Korean summaries

- [Ingestion performance log](./ko/ingestion-performance-log.md)
- [RAG retrieval performance log](./ko/rag-retrieval-performance-log.md)

## Logging contract

Each performance record should capture:

1. the representative scenario and runtime configuration;
2. pre-optimization timings;
3. the exact behavior-preserving change;
4. post-optimization timings;
5. quality guards that stayed stable;
6. lessons learned and remaining bottlenecks.

Do not include raw prompts, raw document text, document IDs, chunk IDs, emails, tokens, or
secrets. Prefer redacted phase names, counts, parser/source metadata, and millisecond values.
