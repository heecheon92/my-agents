# RAG retrieval performance log

[English original](../en/rag-retrieval-performance-log.md) | 한국어

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
각 section은 최신 작업이 위에 오도록 recent-work-first 순서로 유지합니다.

## 2026-07-24 — Lightweight BM25 corpus projection

2026-07-24 hybrid-search 변경은 모든 authorized chunk로 request-local `BM25Okapi` corpus를
만든 뒤 `chunk_id` 기준 RRF를 수행합니다. 첫 live timing에서 BM25 계산은 `102.931 ms`였지만,
unused chunk embedding과 chunk/document join마다 반복되는 full document content까지 선택한
full-model corpus query는 `14232.460 ms`였습니다. Follow-up fix는 corpus query를
`chunk_id`, `document_id`, `ordinal`, chunk text projection으로 줄이고 BM25 top-k row만
hydrate합니다. 인접 retrieval query도 사용하지 않는 `embedding_json`과
`documents.content`를 defer합니다.

같은 scenario의 post-fix run은 retrieval shape(`80` raw, `52` fused, `40` reranked, `12`
injected)를 유지하면서 다음을 측정했습니다.

- `total_ms`: 31778.045 → 13329.462 ms, 18448.583 ms / 58.1% faster.
- `retrieval_latency_ms`: 31741.277 → 13282.737 ms, 18458.540 ms / 58.2% faster.
- `candidate_gather`: 31416.879 → 1842.801 ms, 29574.078 ms / 94.1% faster.
- metadata-profile matched chunk SQL: 12326.666 → 243.452 ms, 98.0% faster.
- related-entity chunk SQL: 3314.911 → 40.707 ms, 98.8% faster.
- BM25 corpus/rank/hydration: 14335.391 → 141.120 ms, 99.0% faster.

Post-fix process의 cross-encoder reranking은 이전 warm run의 `309.478 ms`와 달리
`11429.167 ms`를 사용했습니다. 이제 total latency는 이 phase가 지배하므로 process를
restart하지 않은 같은 요청으로 다시 측정해 cached scoring과 model cold start를 분리해야
합니다. BM25 data path는 더 이상 주 병목이 아닙니다.

Server를 restart하지 않고 새 conversation에서 같은 message를 다시 보낸 warm run은
reranker cache가 conversation이 아니라 process 범위에서 유지됨을 확인했습니다.

- `total_ms`: 최초 31778.045 → warm post-fix 2522.165 ms, 92.1% faster.
- `retrieval_latency_ms`: 31741.277 → 2494.906 ms, 92.1% faster.
- `candidate_gather`: 31416.879 → 1103.555 ms, 96.5% faster.
- BM25 corpus/rank/hydration: 14335.391 → 137.710 ms, 99.0% faster.
- Cross-encoder reranking: cold 11429.167 → warm 1383.150 ms, 87.9% lower.

Retrieval shape는 계속 `80` raw, `52` fused, `40` reranked, `12` injected였습니다. Warm total은
약 `2.5 s`이며, 이후 reranker top-k 또는 deterministic mode 조정은 BM25 data-path fix가
아니라 product quality/speed tradeoff로 다뤄야 합니다.

## 현재 작업

이번 변경에서는 single retrieval attempt 안에서 authorized matched-document chunk rows를
재사용했고, 같은 scenario로 다시 측정했습니다.

RAG-PERF-2026-06-22-D 결과:

- `total_ms`: 25369.799 ms → 22442.777 ms, 2927.022 ms / 11.5% faster.
- `retrieval_latency_ms`: 25323.592 ms → 22398.436 ms, 2925.156 ms / 11.6% faster.
- `candidate_gather`: 14601.382 ms → 11672.401 ms, 2928.981 ms / 20.1% faster.
- `authorized_matched_chunk_rows_sql`: 3 calls / 13220.396 ms → 2 calls / 9275.471 ms,
  3944.925 ms / 29.8% lower.
- `document_overview_supplement`: 3303.452 ms → 5.021 ms, 3298.431 ms / 99.8% lower.
- `embedding.query.openai`는 993.725 ms → 1904.795 ms로 느려졌습니다. 그래서 total
  improvement는 matched-row/overview gain보다 작게 보입니다.

## 최신 측정 결과

2026-06-22 nested timing 기준으로 병목은 full authorized chunk scan 반복, duplicate embedding,
entity mention N+1이었습니다. 최신 same-scenario 측정까지 포함한 핵심 결과는 다음과 같습니다.

| Metric / Phase | Before | After | 개선 |
| --- | ---: | ---: | ---: |
| `total_ms` | 25369.799 ms | 22442.777 ms | 2927.022 ms / 11.5% faster |
| `retrieval_latency_ms` | 25323.592 ms | 22398.436 ms | 2925.156 ms / 11.6% faster |
| `candidate_gather` | 14601.382 ms | 11672.401 ms | 2928.981 ms / 20.1% faster |
| `candidate_gather.authorized_matched_chunk_rows_sql` | 3 calls / 13220.396 ms | 2 calls / 9275.471 ms | 1 call 제거, 29.8% lower |
| `candidate_gather.document_overview_supplement` | 3303.452 ms | 5.021 ms | 99.8% lower |
| `reranking` | 10713.234 ms | 10719.124 ms | 거의 동일; 현재 largest single phase |

직전 큰 개선(`6f23b89`)의 기준 변화는 다음과 같습니다.

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
| OPT-4 | single retrieval attempt 안에서 matched-document chunk rows를 request-local cache로 재사용합니다. | RAG-PERF-2026-06-22-C: `authorized_matched_chunk_rows_sql` 3 calls / 13220.396 ms, overview supplement 3303.452 ms. | RAG-PERF-2026-06-22-D: `authorized_matched_chunk_rows_sql` 2 calls / 9275.471 ms, overview supplement 5.021 ms. |
| OPT-3 | graph expansion entity lookup을 batch query로 바꿉니다. | `entity_mentions_sql` 9646 calls, 3356.052 ms. | `entity_mentions_sql` 1 call / 2.127 ms, `related_entity_chunks_sql` 1 call / 157.361 ms. |
| OPT-2 | metadata/profile/overview lane에서 full chunk scan 대신 document-only rows와 matched-document chunk rows를 사용합니다. | `authorized_chunk_rows_sql` 5 calls, 45461.412 ms. | full scan row 제거. `authorized_document_rows_sql` 1 call / 15.100 ms, `authorized_matched_chunk_rows_sql` 3 calls / 13220.396 ms. |
| OPT-1 | metadata-profile lane과 direct retrieval lane이 query embedding을 공유합니다. | OpenAI query embedding 2 calls, 2433.501 ms. | 1 call, 993.725 ms. |

## 현재 해석과 다음 단계

OPT-4 개선은 실제로 확인됐습니다. 다만 새로운 병목이 남아 있습니다.

1. `reranking`: 1 call, 10719.124 ms.
   - gather가 줄면서 cross-encoder reranking이 co-bottleneck이 됐습니다.
   - 하지만 reranking은 document retrieval 품질에 중요한 단계이므로 기본 latency fix로 끄지 않습니다.
2. `candidate_gather.authorized_matched_chunk_rows_sql`: 2 calls, 9275.471 ms.
   - overview lane reuse는 해결됐지만 metadata-profile matching 쪽 chunk loading이 아직 큽니다.
   - 다음 quality-safe 후보는 metadata-profile lane에서 retrieval limit을 만족시키는 top matched documents에 대해서만 source chunks를 fetch하고, 작은 safety buffer를 두는 것입니다.
3. `candidate_gather.document_metadata_profile_match`: 8933.371 ms.
   - 남은 gather cost 대부분이 이 phase에 모여 있습니다.

Candidate limit, injected context, reranker off 같은 quality tradeoff는 사용자가 명시적으로
품질 tradeoff를 수용할 때만 검토합니다.
