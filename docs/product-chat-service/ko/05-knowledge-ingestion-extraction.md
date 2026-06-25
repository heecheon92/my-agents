# Knowledge ingestion과 deterministic extraction

[English original](../en/05-knowledge-ingestion-extraction.md) | 한국어

## 요약

이 문서는 `product-chat-service/05-knowledge-ingestion-extraction.md`의 한국어 문서 트랙 항목입니다. 현재는 핵심 목적과 영어 원문 위치를 안내하는 요약본입니다.

현재 backend 업로드/ingestion 경로는 PDF, Markdown, plain text, `.xlsx`, `.pptx`,
`.docx`를 지원합니다. `.docx`는 local Docling parser가 Markdown과
`word_heading`/`word_paragraph`/`word_table` 같은 block element artifact를 만들고,
기존 chunk/retrieval 경로와 호환되도록 Markdown을 `documents.content`에도 저장합니다.
legacy binary `.doc`는 이 slice에서 계속 지원하지 않습니다.

PDF는 page boundary를 넘지 않는 범위에서 line-heavy page text를 retrieval-sized chunk로
coalesce합니다. 따라서 parser가 줄 단위 text를 많이 만들더라도 같은 page 안에서는 더 큰
chunk로 묶어 embedding row, entity mention, retrieval scan 비용을 줄이면서 `source_page`
provenance는 유지합니다.

Ingestion 최적화 전후 측정은 아래 benchmark harness를 기준으로 합니다.

```bash
uv run python scripts/measure_ingestion_performance.py \
  --scenario pdf \
  --repeat 3 \
  --repeat-units 80 \
  --output /tmp/my-agents-ingestion-pdf.json
```

이 harness는 임시 SQLite DB와 deterministic embedding/metadata mode를 사용해 parse,
persist, ingest, retrieval-smoke, total time, RSS delta, parser/source metadata,
artifact count, redacted quality signature를 출력합니다. 최적화 전후에는 같은 scenario와
repeat 설정을 사용하고, parser/source 변경, metadata profile 누락, retrieval hit 누락,
예상하지 않은 entity 손실은 quality guard 실패로 봅니다.

API를 직접 쓰면서 실제 local 문서가 느린 단계를 보고 싶으면 ingestion timing panel을 켭니다.

```bash
MY_AGENTS_DEBUG_INGESTION_TIMING_LOGGING=true uv run fastapi dev main.py
```

이 local-only 출력은 upload parsing과 extraction/indexing run마다 redacted Rich table을
보여줍니다. File read, PDF validation/checksum/classification, 개별 PDF parser attempt,
PDF quality gate, DB persistence, stale artifact cleanup, chunking, chunk embedding,
entity upsert/linking, chunk/index persistence, metadata generation, metadata embedding,
final commit 중 어디가 느린지 확인할 때 사용합니다. Raw filename이나 문서 본문은 출력하지 않고
source metadata와 count만 출력합니다. OpenAI metadata generation이
켜져 있으면 metadata generation은 chunking 이후 시작되어 chunk embedding/indexing과 병렬로
실행됩니다. 따라서 phase row는 서로 겹칠 수 있는 span이며 wall-clock total에 단순 합산되지
않습니다.

PDF classification은 happy path에서 lazy하게 실행됩니다. Upload parser는 먼저
`pymupdf_text_v1`을 실행하고 기존 quality gate를 통과하면 바로 수락합니다. PyMuPDF가
실패하거나 빈/저품질 text를 만들면 그때 pypdf classification을 실행해서 encrypted,
corrupted, native/mixed/no-text fallback routing을 판단한 뒤 pypdf, Docling, Tesseract,
legacy fallback을 시도합니다.

## Performance optimization 기록

자세한 ingestion before/after 측정 기록은 전용 performance ledger에 둡니다.

- [`docs/performance/ko/ingestion-performance-log.md`](../../performance/ko/ingestion-performance-log.md)

2026-06-25 Aliro PDF run에는 baseline, embedding batch-size 실험, OpenAI metadata 병렬화,
PDF parser sub-timing, lazy PDF classification, quality guard, learning lesson이 기록되어 있습니다.
앞으로의 ingestion 성능 작업도 이 문서가 아니라 performance ledger에 추가합니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`에 있습니다.
- 이 한국어 파일은 같은 주제의 위치를 고정하기 위한 문서입니다.
- DOCX-only 지원 상태는 영어 원문과 의미가 같아야 하며, legacy `.doc` 지원을 암시하지 않습니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/05-knowledge-ingestion-extraction.md](../en/05-knowledge-ingestion-extraction.md)
