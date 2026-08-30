# Permission-aware RAG와 citation 기반 응답

[English original](../en/06-permission-aware-rag.md) | 한국어

## 요약

이 문서는 `product-chat-service/06-permission-aware-rag.md`의 한국어 문서 트랙 항목입니다. 현재는 핵심 목적과 영어 원문 위치를 안내하는 요약본입니다.
2026-06-16 기준 영어 원문은 `general_assistant` graph가 RAG Agent runtime을 호출하고, RAG Agent가 내부적으로 ContextForge `RetrievalGraph`에 위임하는 현재 retrieval entrypoint를 설명합니다. 더 깊은 tool-using retrieval graph orchestration은 future-gated 상태입니다. 2026-06-17 업데이트는 현재 high-recall RAG 품질을 benchmark로 유지하면서, 향후 Fast / Balanced / Thorough retrieval profile로 product UX latency와 answer quality를 균형 있게 조정하는 방향을 기록합니다.

2026-08-24에는 semantic `comprehensive_document` 경로가 추가되었습니다. OpenAI mode에서는
General Assistant가 private knowledge로 위임한 뒤 고정된 `gpt-5.6-luna` standard/low RAG
planner가 focused chunk search와 comprehensive document read 중 typed tool 하나를 고릅니다.
Deterministic mode, invalid output, provider failure는 같은 two-tool local fallback을 사용합니다.
이 경로는
“문서 전체를 빠짐없이 검토해줘”처럼 completeness 표현과 document task가 함께 있는
명시적 요청에서만 동작합니다. 일반 문서 질문이나 chunk retrieval 결과가 약하다는
이유만으로 자동 전환하지 않습니다.

## 문서 전체 검토 경로

- 현재 authorized user-controllable personal/group document 하나만 target으로 정합니다.
  Ambient system KB/document는 target이나 interaction option이 될 수 없습니다.
- 하나로 결정할 수 없으면 기존 versioned `document_selection` HITL interaction으로
  같은 run을 중단/재개합니다. Resume 때 현재 권한을 다시 확인합니다.
- `DocumentModel.content`의 정규화된 추출 text를 읽으며 offset은 현재 text 기준
  half-open character range `[start_offset, end_offset)`입니다.
- 기본값은 24,000자 이하면 `complete`, 더 크면 처음 12,000자만 읽고 `partial`입니다.
  Partial 답변에는 검토 범위와 전체 길이를 알리는 문구를 반드시 먼저 붙입니다.
- 응답과 run detail의 `document_coverage`는 mode, document metadata, start/end offset,
  total chars를 제공합니다. Citation은 읽은 범위와 겹치는 chunk에서만 생성합니다.
- Full-document read는 읽은 범위와 겹치는 authorized chunk를 최대 2,000개까지 모두
  검증합니다. Valid chunk가 100개보다 많으면 첫/마지막을 포함해 범위 전체에 고르게
  분산된 provenance chunk 100개를 유지합니다. 이는 답변에 제공된 문서 범위를 증명하지만
  각 chunk가 특정 생성 claim을 직접 뒷받침한다는 뜻은 아닙니다. 응답의
  `consulted_sources`는 이 전체 consulted superset을, `citations`는 최종 답변에서
  명시적 lexical 근거가 확인된 보수적 subset을 나타냅니다. 두 배열에 같은 source가 있으면
  동일한 persisted `id`와 `chunk_id`를 사용합니다. 보수적 selector는 과장된 citation보다
  false negative를 선택하므로 paraphrase 답변에서는 `citations: []`이면서
  `consulted_sources`가 비어 있지 않은 경우가 흔할 수 있습니다. 더 정교한 semantic/claim-level
  attribution은 향후 과제입니다. Scan bound 2,000개를 넘는 range는 계속 fail closed합니다.
- 새 attribution run에서 source를 하나도 참조하지 않았으면 `consulted_sources: []`입니다.
  Attribution 이전 legacy run은 검증할 수 없으므로 `consulted_sources: null`로 직렬화하고
  기존 flat `citations`를 그대로 유지합니다. Legacy row를 answer-supported로 backfill하지 않습니다.
- 각 chunk-level response row는 nullable `document_title`과 `knowledge_base_name`도
  제공합니다. Product UI는 `document_id`로 묶어 문서당 한 항목만 보여주고,
  document/knowledge-base 이름과 optional unique page number만 표시합니다. 일반 citation
  상세에서 snippet과 document/KB/chunk ID는 노출하지 않습니다.
- Raw full-document body는 application checkpoint, run event, full-body logging payload에
  저장하지 않습니다. 해당 provider call은 LangSmith tracing도 끕니다. 기존 opt-in DEBUG
  logging의 제한된 citation-chunk snippet은 별도 기존 동작입니다.
- Run/resume/replay SSE는 retrieval이 처음 준비됐을 때 `retrieval_completed` progress event를
  최대 한 번만 보냅니다. 하지만 terminal persistence는 그 early snapshot을 재사용하지 않고
  response node의 authorization/content 재읽기가 끝난 final graph result에서 retrieval context를
  다시 구성합니다. 준비와 응답 사이에 문서가 바뀌거나 접근할 수 없게 되면 empty coverage
  sentinel은 public `document_coverage: null`이 되고, `full_document_read` event와 consulted
  citation을 남기지 않은 채 안전한 insufficient-evidence 응답으로 완료됩니다. Coverage contract는
  `start_offset <= end_offset <= total_chars`를 강제하고, `complete`는 정확히
  `[0, total_chars)`여야 합니다. `partial`은 end offset이 우연히 current total과 같아도 mode 값
  그대로 partial입니다.
- 큰 문서의 자동 multi-range 순회/최종 synthesis는 아직 없고 budget은 token이 아니라
  character 기준입니다. Content/chunk revision이 바뀌면 offset provenance가 stale해질 수
  있어 검증 실패 시 insufficient evidence로 안전하게 종료합니다.
- Focused retrieval의 adaptive surrounding-context expansion은 별도 milestone입니다.
  Bounded sufficiency decision이 authorized anchor 주변의 같은 문서 chunk를 요청할 수 있지만,
  permission 재검증, ordinal/offset window, round/token budget, reranking/packing, citation
  selectivity는 backend가 강제해야 합니다. 읽은 neighboring chunk가 자동 citation이 되지는
  않습니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/06-permission-aware-rag.md`에 있습니다.
- 이 한국어 파일은 같은 주제의 위치를 고정하기 위한 문서입니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/06-permission-aware-rag.md](../en/06-permission-aware-rag.md)

## Revision history

- 2026-08-31: Run/resume/replay SSE terminal이 post-re-read final retrieval context를 사용하도록 하고 coverage 관계 검증과 TOCTOU 안전 downgrade 동작을 기록했습니다.
- 2026-08-25: Chunk-level audit provenance 위에 document/knowledge-base 이름을 추가하고 document-level UI grouping 계약을 기록했습니다.
- 2026-08-25: Consulted source 전체 집합과 보수적인 answer-supported citation subset을 분리하고 legacy `null`/신규 빈 배열 의미 및 동일 ID 계약을 기록했습니다.
- 2026-08-25: Valid 190-chunk Markdown 문서가 insufficient evidence로 잘못 종료되던 문제를 bounded distributed provenance sampling으로 수정했습니다.
