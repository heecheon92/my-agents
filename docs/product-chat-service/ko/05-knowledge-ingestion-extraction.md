# Knowledge ingestion과 deterministic extraction

[English original](../en/05-knowledge-ingestion-extraction.md) | 한국어

## 요약

이 문서는 `product-chat-service/05-knowledge-ingestion-extraction.md`의 한국어 문서 트랙 항목입니다. 현재는 핵심 목적과 영어 원문 위치를 안내하는 요약본입니다.

현재 backend 업로드/ingestion 경로는 PDF, Markdown, plain text, `.xlsx`, `.pptx`,
`.docx`를 지원합니다. `.docx`는 local Docling parser가 Markdown과
`word_heading`/`word_paragraph`/`word_table` 같은 block element artifact를 만들고,
기존 chunk/retrieval 경로와 호환되도록 Markdown을 `documents.content`에도 저장합니다.
legacy binary `.doc`는 이 slice에서 계속 지원하지 않습니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`에 있습니다.
- 이 한국어 파일은 같은 주제의 위치를 고정하기 위한 문서입니다.
- DOCX-only 지원 상태는 영어 원문과 의미가 같아야 하며, legacy `.doc` 지원을 암시하지 않습니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/05-knowledge-ingestion-extraction.md](../en/05-knowledge-ingestion-extraction.md)
