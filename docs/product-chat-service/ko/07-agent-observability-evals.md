# Agent observability event와 eval fixture

[English original](../en/07-agent-observability-evals.md) | 한국어

## 요약

이 문서는 `product-chat-service/07-agent-observability-evals.md`의 한국어 문서 트랙 항목입니다. 현재는 핵심 목적과 영어 원문 위치를 안내하는 요약본입니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/07-agent-observability-evals.md`에 있습니다.
- 이 한국어 파일은 같은 주제의 위치를 고정하기 위한 문서입니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/07-agent-observability-evals.md](../en/07-agent-observability-evals.md)

## 내부 timing metrics

- `MY_AGENTS_METRICS_ENABLED=true`일 때만 `GET /metrics`가 Prometheus text 형식으로
  노출됩니다.
- 이 endpoint는 product API나 frontend-visible 기능이 아니라 유지보수/품질 분석용
  surface입니다.
- 현재 request, conversation run, ContextForge retrieval, retrieval phase, embedding,
  reranker, graph invocation 시간을 histogram으로 기록합니다.
- Label은 route template, status code, run outcome, retrieval route, answer mode,
  provider/model name, 고정 phase name처럼 낮은 cardinality 값만 사용해야 합니다.
- Raw prompt, 문서 본문, user ID, document ID, chunk ID, email, token, secret, 임의 URL
  path는 metric label로 사용하지 않습니다.
- 자세한 metric 이름과 label 정책은 영어 원문을 기준으로 유지합니다.
- 단일 local retrieval run에서 어느 단계가 느렸는지 보려면
  `MY_AGENTS_DEBUG_RETRIEVAL_TIMING_LOGGING=true`를 사용합니다. 이 값은 retrieval
  attempt마다 Rich timing panel을 출력하며 Prometheus histogram처럼 aggregate가 아니라
  해당 run의 authorization count, planning, candidate gather, fusion, reranking,
  context packing 시간과 redacted count를 보여줍니다.

## 향후 retrieval UX 품질 profile

현재 RAG 품질은 보호해야 할 benchmark입니다. Latency를 줄인다는 이유로 전체
retrieval recall을 무작정 낮추지 말고, metrics/eval 근거를 보고 product UX profile을
분리합니다.

- `Fast`: 일반 대화나 약한 문서 관련 질문에서 빠른 첫 답변을 우선합니다. 후보/vector
  limit, injected chunk 수, 비싼 reranking/expansion을 낮추는 방향을 검토합니다.
- `Balanced`: 대부분의 문서 질문에 대한 기본 UX입니다. Citation과 permission-first
  retrieval을 유지하면서 중간 수준의 후보/context budget을 사용합니다.
- `Thorough`: 명시적인 문서 기반 질문, 비교/요약, 법무/기술 세부 검토, 사용자가 신중한
  답변을 선택한 경우의 고품질 모드입니다. 현재 high-recall baseline을 benchmark로
  유지하고 latency는 UI에서 명확히 보여주는 방향을 검토합니다.

Profile 이름과 수치는 future contract이며 현재 runtime 동작이 아닙니다. 속도 개선은
latency histogram과 retrieval/answer-quality fixture 근거가 있을 때만 진행합니다.

## 향후 observability 목표

- Prometheus + Grafana로 request latency, request volume, error rate, ingestion/worker
  health, queue/stale-run signal, resource saturation 같은 일반 backend operation
  metrics를 운영 관점에서 볼 수 있게 합니다.
- Langfuse 또는 LangSmith를 비교해 LLM/provider latency, token/cost metrics,
  prompt/version tracking, trace, eval dataset, retrieval/answer-quality review를
  다룹니다.
- Fast / Balanced / Thorough retrieval UX profile 실험을 추가해 latency, citation 품질,
  answer usefulness를 비교한 뒤 user-facing selector 노출 여부를 결정합니다.
