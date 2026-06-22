# RAG retrieval performance log

[English original](../en/23-rag-retrieval-performance-log.md) | 한국어

## 요약

이 문서는 local RAG retrieval 성능 개선을 계속 추적하기 위한 유지보수용 ledger입니다.
영어 원문을 canonical log로 두고, 이 한국어 문서는 운영 규칙과 핵심 해석을 빠르게 찾기 위한
요약본입니다.

## 기록해야 하는 항목

- 어떤 phase를 측정했는지
- redacted measurement output의 핵심 숫자
- 어떤 optimization을 적용했는지
- 같은 scenario로 다시 측정했을 때 얼마나 개선됐는지
- 아직 남은 bottleneck은 무엇인지

Raw prompt, 문서 본문, document ID, chunk ID, email, token, secret은 이 문서에 붙이지 않습니다.
Route, intent, count, phase name, millisecond 값만 기록합니다.

## 현재 기준 측정

2026-06-22 기준 nested timing 결과에서 가장 큰 병목은 다음이었습니다.

| Phase | Calls | Elapsed ms | 해석 |
| --- | ---: | ---: | --- |
| `candidate_gather.authorized_chunk_rows_sql` | 5 | 45461.412 | 반복 full authorized chunk scan이 가장 큰 낭비였습니다. |
| `candidate_gather.document_metadata_match` | 1 | 19541.002 | metadata lane이 chunk scan에 의존해 느렸습니다. |
| `candidate_gather.embedding.query.openai` | 2 | 2433.501 | 같은 query embedding을 두 번 호출했습니다. |
| `candidate_gather.entity_mentions_sql` | 9646 | 3356.052 | N+1 query 패턴입니다. |
| `candidate_gather.authorized_related_expansion` | 1 | 12449.364 | entity mention fan-out과 chunk scan 때문에 느렸습니다. |
| `candidate_gather.document_overview_supplement` | 1 | 8777.651 | overview lane도 전체 chunk scan 비용을 냈습니다. |
| `candidate_gather.postgres_vector_sql` | 1 | 78.245 | pgvector search 자체는 병목이 아니었습니다. |
| `reranking` | 1 | 11348.078 | candidate gather 다음의 secondary bottleneck입니다. |

## 적용된 최적화

| ID | 변경 | 측정 전 | 측정 후 |
| --- | --- | --- | --- |
| OPT-1 | metadata-profile lane과 direct retrieval lane이 query embedding을 공유합니다. | OpenAI query embedding 2 calls, 2433.501 ms. | Same-scenario rerun 필요. |
| OPT-2 | metadata/profile/overview lane에서 full chunk scan 대신 document-only rows와 matched-document chunk rows를 사용합니다. | `authorized_chunk_rows_sql` 5 calls, 45461.412 ms. | Same-scenario rerun 필요. |
| OPT-3 | graph expansion entity lookup을 batch query로 바꿉니다. | `entity_mentions_sql` 9646 calls, 3356.052 ms. | Same-scenario rerun 필요. |

자세한 append-only ledger와 계산식은 영어 원문
[`23-rag-retrieval-performance-log.md`](../en/23-rag-retrieval-performance-log.md)를 기준으로 유지합니다.

## 다음에 해야 할 일

`6f23b89` 이후 같은 local corpus/query로 다시 실행해서 영어 원문의
`RAG-PERF-2026-06-22-C` 항목을 채웁니다.

성공 신호:

- `candidate_gather.authorized_chunk_rows_sql`가 더 이상 5회 반복되지 않습니다.
- `candidate_gather.authorized_document_rows_sql`와
  `candidate_gather.authorized_matched_chunk_rows_sql`가 targeted lane에 나타납니다.
- `candidate_gather.entity_mentions_sql`는 수천 번이 아니라 batch 1회 수준이어야 합니다.
- `candidate_gather.embedding.query.openai`는 이 경로에서 1회가 되어야 합니다.
- gather 시간이 크게 줄면 `reranking`이 다음 병목일 가능성이 큽니다.
