# RAG retrieval performance log

[English original](../en/23-rag-retrieval-performance-log.md) | 한국어

## 요약

이 문서는 local RAG retrieval 성능 개선을 계속 추적하기 위한 유지보수용 ledger입니다.
영어 원문을 canonical log로 두고, 이 한국어 문서는 운영 규칙과 핵심 해석을 빠르게 찾기 위한
요약본입니다. 자세한 before/after 표와 계산식은 영어 원문을 기준으로 유지합니다.

## 기록해야 하는 항목

- 어떤 phase를 측정했는지
- redacted measurement output의 핵심 숫자
- 어떤 optimization을 적용했는지
- 같은 scenario로 다시 측정했을 때 얼마나 개선됐는지
- 아직 남은 bottleneck은 무엇인지

Raw prompt, 문서 본문, document ID, chunk ID, email, token, secret은 이 문서에 붙이지 않습니다.
Route, intent, count, phase name, millisecond 값만 기록합니다.

## 기준 측정과 post-optimization 결과

2026-06-22 nested timing 기준으로 병목은 full authorized chunk scan 반복, duplicate embedding,
entity mention N+1이었습니다. `6f23b89` 이후 같은 scenario로 다시 측정한 결과는 다음과 같습니다.

| Metric / Phase | Before | After | 개선 |
| --- | ---: | ---: | ---: |
| `total_ms` | 63754.876 ms | 25369.799 ms | 38385.077 ms / 60.2% faster |
| `retrieval_latency_ms` | 63709.865 ms | 25323.592 ms | 38386.273 ms / 60.3% faster |
| `candidate_gather` | 52353.431 ms | 14601.382 ms | 37752.049 ms / 72.1% faster |
| `candidate_gather.embedding.query.openai` | 2 calls / 2433.501 ms | 1 call / 993.725 ms | 1 call 제거, 59.2% lower |
| `candidate_gather.document_metadata_match` | 19541.002 ms | 620.095 ms | 96.8% lower |
| `candidate_gather.entity_mentions_sql` | 9646 calls / 3356.052 ms | 1 call / 2.127 ms | 9645 calls 제거, 99.9% lower |
| `candidate_gather.authorized_related_expansion` | 12449.364 ms | 167.141 ms | 98.7% lower |
| `candidate_gather.document_overview_supplement` | 8777.651 ms | 3303.452 ms | 62.4% lower |
| `reranking` | 11348.078 ms | 10713.234 ms | 5.6% lower; 이제 co-bottleneck |

## 적용된 최적화

| ID | 변경 | 측정 전 | 측정 후 |
| --- | --- | --- | --- |
| OPT-1 | metadata-profile lane과 direct retrieval lane이 query embedding을 공유합니다. | OpenAI query embedding 2 calls, 2433.501 ms. | 1 call, 993.725 ms. |
| OPT-2 | metadata/profile/overview lane에서 full chunk scan 대신 document-only rows와 matched-document chunk rows를 사용합니다. | `authorized_chunk_rows_sql` 5 calls, 45461.412 ms. | full scan row 제거. `authorized_document_rows_sql` 1 call / 15.100 ms, `authorized_matched_chunk_rows_sql` 3 calls / 13220.396 ms. |
| OPT-3 | graph expansion entity lookup을 batch query로 바꿉니다. | `entity_mentions_sql` 9646 calls, 3356.052 ms. | `entity_mentions_sql` 1 call / 2.127 ms, `related_entity_chunks_sql` 1 call / 157.361 ms. |

## 현재 해석과 다음 단계

성능 개선은 실제로 확인됐습니다. 다만 새로운 병목이 남아 있습니다.

1. `candidate_gather.authorized_matched_chunk_rows_sql`: 3 calls, 13220.396 ms.
   - full scan은 제거됐지만 targeted matched-document chunk fetch가 아직 큽니다.
   - 다음 quality-safe 후보는 single retrieval attempt 안에서 matched-document chunk rows를 cache/reuse하거나 metadata profile과 overview lane 사이의 반복 fetch를 줄이는 것입니다.
2. `reranking`: 1 call, 10713.234 ms.
   - gather가 줄면서 cross-encoder reranking이 co-bottleneck이 됐습니다.
   - 하지만 reranking은 document retrieval 품질에 중요한 단계이므로 기본 latency fix로 끄지 않습니다.
   - 다음 후보는 cross-encoder warm/cold behavior, device, batch setting, duplicate rerank 회피처럼 품질을 낮추지 않는 low-risk 최적화만 우선 측정하는 것입니다.

Candidate limit, injected context, reranker off 같은 quality tradeoff는 위 두 항목을 먼저 측정하고, 사용자가 명시적으로 품질 tradeoff를 수용할 때만 검토합니다.
