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

## 향후 observability 목표

- Prometheus + Grafana로 request latency, request volume, error rate, ingestion/worker
  health, queue/stale-run signal, resource saturation 같은 일반 backend operation
  metrics를 운영 관점에서 볼 수 있게 합니다.
- Langfuse 또는 LangSmith를 비교해 LLM/provider latency, token/cost metrics,
  prompt/version tracking, trace, eval dataset, retrieval/answer-quality review를
  다룹니다.
