---
created: 2026-06-25
updated: 2026-06-25
status: active
topics:
  - ingestion
  - performance
  - pdf
  - embeddings
  - metadata
  - observability
related_code:
  - scripts/measure_ingestion_performance.py
  - my_agents/knowledge/timing.py
  - my_agents/knowledge/uploads.py
  - my_agents/knowledge/pdf_uploads.py
  - my_agents/knowledge/extraction.py
  - my_agents/settings.py
  - tests/test_knowledge_ingestion.py
---

# Ingestion performance log

[English original](../en/ingestion-performance-log.md) | 한국어

이 문서는 document ingestion 성능 최적화 기록을 남기는 ledger입니다. 영어 원문을 canonical
기록으로 두고, 이 한국어 문서는 핵심 숫자, 적용한 변경, learning lesson을 빠르게 찾기 위한
요약입니다.

Raw document text, private filename, document ID, chunk ID, email, token, secret은 이 문서에
남기지 않습니다. Parser name, source metadata, count, redacted phase name, millisecond 값만
기록합니다.

## 측정 surface

반복 가능한 code-path 비교에는 benchmark harness를 사용합니다.

```bash
uv run python scripts/measure_ingestion_performance.py \
  --scenario pdf \
  --repeat 3 \
  --repeat-units 80 \
  --output /tmp/my-agents-ingestion-pdf.json
```

실제 local API 문서 업로드/ingestion을 진단할 때는 Rich timing panel을 켭니다.

```bash
MY_AGENTS_DEBUG_INGESTION_TIMING_LOGGING=true uv run fastapi dev main.py
```

Timing panel은 redacted phase와 count만 출력하고 raw filename이나 문서 본문은 출력하지 않습니다.

## INGEST-PERF-2026-06-25-A: Aliro PDF 최적화 run

측정 대상은 195쪽 Aliro 1.0 specification PDF였습니다.

- Accepted parser: `pymupdf_text_v1`
- Source size: 3,569,429 bytes
- Extracted text: 409,701 chars
- Runtime shape: OpenAI embeddings, OpenAI metadata generation
- Batch-size experiment: `MY_AGENTS_EMBEDDING_BATCH_SIZE=64`
- 목표: parser/source output과 ingestion quality count를 유지하면서 wall-clock time 감소

## Before / after summary

| 단계 | Upload total | Extraction total | End-to-end | 주요 변경 |
| --- | ---: | ---: | ---: | --- |
| Baseline | 8.50s | 27.66s | 36.16s | OpenAI metadata/embedding 직렬 실행, PDF pre-classification. |
| Batch-size tuning | 8.45s | 23.43s | 31.88s | Embedding request 수 감소. |
| Parallel metadata | 8.48s | 13.01s | 21.50s | Metadata generation을 embedding/indexing과 병렬화. |
| Lazy classification | 5.00s | 11.57s | 16.57s | Native-text PDF happy path에서 pypdf pre-classification 생략. |

최종 end-to-end 개선은 local profile 기준 약 54%였습니다.

## Quality guard

아래 값은 최적화 전후 유지됐습니다.

| Field | Value |
| --- | ---: |
| `parser` | `pymupdf_text_v1` |
| `page_count` | 195 |
| `content_chars` | 409701 |
| `chunk_count` | 392 |
| `entity_count` | 1935 |
| `relationship_count` | 6537 |
| `structured_entity_count` | 127 |

## 적용된 변경

1. **Embedding batch-size 64 실험**
   - 392개 chunk에 대한 OpenAI embedding request 수를 줄였습니다.
   - Chunk text, embedding model, parser output은 변경하지 않았습니다.
2. **OpenAI metadata generation 병렬화**
   - Chunking 이후 metadata generation을 background thread에서 시작하고, main ingestion thread는
     chunk embedding/entity/index persistence를 계속 진행합니다.
   - SQLAlchemy DB write는 main ingestion thread에 남겼습니다.
3. **PDF parser sub-timing**
   - `parse.pdf.classify`, `parse.pdf.parser.pymupdf_text_v1`, quality gate 같은 세부 phase를
     관찰할 수 있게 했습니다.
4. **Lazy PDF classification**
   - 먼저 PyMuPDF를 실행하고 existing quality gate를 통과하면 바로 수락합니다.
   - PyMuPDF 실패/저품질일 때만 pypdf classification을 실행하고 fallback routing을 수행합니다.

## Learning lesson

- 먼저 측정해야 합니다. 실제 병목은 RAM보다 OpenAI/network/model span과 pypdf pre-classification이었습니다.
- Quality를 낮추지 않는 최적화를 먼저 찾는 것이 좋습니다. Chunk count, metadata profile, quality gate,
  fallback parser를 줄이지 않고도 큰 개선이 가능했습니다.
- Timing row는 span입니다. Metadata 병렬화 이후에는 `metadata.generate`가 커 보여도 wall-clock total과
  단순 합산하면 안 됩니다.
- Lazy work가 deletion보다 안전했습니다. pypdf classification은 fallback routing에 유지하되 happy path에서는
  비용을 내지 않게 했습니다.

## 남은 병목

- `metadata.generate`는 여전히 큰 span이지만 이제 extraction work와 겹칩니다.
- PDF parse에는 아직 visible subphase 외 overhead가 있습니다. 추가 최적화는 다시 측정한 뒤 진행해야 합니다.
- Hosted production 성능은 OpenAI latency, DB latency, web/worker isolation에 영향을 받습니다.
