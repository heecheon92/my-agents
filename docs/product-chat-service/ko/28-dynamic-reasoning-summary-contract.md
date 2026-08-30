# 동적 model-authored reasoning summary 계약

[English original](../en/28-dynamic-reasoning-summary-contract.md) | 한국어

상태: **제안됨 — 다음 즉시 수행할 backend task.** 이 문서의 기능은 아직 구현되지 않았습니다.

## 왜 필요한가

현재 `agent_trace`는 의도적으로 사실 중심이고 대부분 정적입니다. 실제 실행한 stage,
status, display-safe count를 보여 주지만 요청마다 model이 왜 다른 접근을 골랐는지는 설명할
수 없습니다. `reasoning_mode`와 `reasoning_effort`도 provider computation 설정일 뿐
사용자에게 보여 줄 text를 반환하지 않습니다.

제품에는 별도의 동적 artifact가 필요합니다. 이는 model이 이번 요청에 어떤 접근을
선택했다고 설명하는 짧은 model-authored summary입니다. 예를 들면 다음과 같습니다.

- “`SUMMARY.ko.md`를 빠짐없이 검토해 달라는 요청이므로 focused retrieval 대신 전체 문서
  읽기를 선택했습니다.”
- “질문이 AxSystem 정의에 한정되어 있어 전체 문서를 읽지 않고 관련 passage를 검색했습니다.”
- “검색한 정책 section을 비교하고 최신 적용 규칙을 확인한 뒤 남은 불확실성을 중심으로
  답변을 구성했습니다.”

이 text는 생각 과정처럼 보일 수 있지만 raw 또는 정확한 chain-of-thought가 아닙니다.
사용자 표시를 위해 model이 생성한 요약입니다. UI와 API는 model의 비공개 내부 추론을
노출한다고 암시하면 안 됩니다.

## 분리해야 할 세 가지 trust channel

| Surface | 답하는 질문 | Trust 의미 |
| --- | --- | --- |
| `reasoning_summaries` | “Model은 어떤 접근을 취했다고 설명하는가?” | 동적이고 model-authored이며 불완전할 수 있음 |
| `agent_trace` | “Application이 실제 실행을 무엇으로 확인했는가?” | 결정적이고 typed된 실행 기록 |
| citations / `consulted_sources` | “어떤 authorized evidence가 답변을 지원하거나 제공되었는가?” | Backend attribution rule이 관리하는 provenance |

Reasoning summary는 trace, citation, coverage disclosure, warning, final answer를 대체하지
않습니다. Model summary와 verified trace가 다르면 serializer가 이를 숨기지 말고 product
signal로 보존해야 합니다.

## 제안하는 producer stage

### `retrieval_planning`

Luna RAG Agent는 focused/comprehensive typed tool choice와 함께 bounded user-displayable 설명
하나를 반환해야 합니다. 이는 임의 scratchpad가 아니라 strict output field입니다. Scope와
retrieval strategy를 설명할 수 있지만 trusted ID를 선택하거나 authorization을 주장하거나
system knowledge 또는 document body를 노출하면 안 됩니다.

### `answer_synthesis`

마지막 Sol response call은 OpenAI Responses API reasoning summary를 요청하고 provider summary
block만 추출해야 합니다. OpenAI는 현재 `reasoning.generate_summary`와 reasoning-summary
streaming event를 제공합니다. 구현 전 실제 `langchain-openai` block/event shape를 확인해
adapter를 고정해야 합니다.

Provider reference: [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).

두 stage 모두 nullable입니다. Unsupported model, `reasoning_effort=none`, 빈 provider output,
deterministic mode, safe filtering에서는 summary가 없을 수 있습니다. Backend는 실제 summary가
없을 때 model-authored text를 지어내면 안 됩니다.

## 제안하는 API shape

```json
{
  "reasoning_summaries": [
    {
      "stage": "retrieval_planning",
      "text": "전체 범위 검토 요청이므로 comprehensive read를 선택했습니다.",
      "source": "model_generated"
    },
    {
      "stage": "answer_synthesis",
      "text": "구조, 실행 흐름, 위험 요소, 다음 단계 순서로 답변을 구성했습니다.",
      "source": "provider_reasoning_summary"
    }
  ]
}
```

제안하는 closed field는 다음과 같습니다.

- `stage`: `retrieval_planning | answer_synthesis`;
- `text`: 비어 있지 않은 display text, item당 최대 500자;
- `source`: `model_generated | provider_reasoning_summary`.

Completed run response, run detail, replay response, refresh recovery는 같은 ordered list를
반환해야 합니다. Summary가 없으면 placeholder prose 대신 빈 list를 사용합니다.

## 제안하는 SSE와 persistence 계약

- `reasoning_summary_delta`는 SSE 전용이며 `stage`, `delta`, stage별 `sequence`를 전달합니다.
- `reasoning_summary_generated`는 한 stage의 최종 bounded item을 담는 proposed persisted,
  refresh-safe event입니다.
- `run_completed.reasoning_summaries`가 완료된 stream response의 기준입니다.
- `answer_delta`에는 final answer text만 남깁니다. Summary를 reply 또는 assistant message
  content에 합치면 안 됩니다.
- Provider가 제공하면 frontend는 answer delta 전에 summary delta를 보여 줄 수 있지만 모든
  model, effort, run이 summary를 생성한다고 가정하면 안 됩니다.

현재 stream adapter는 control-model token을 filter하고 reasoning block을 제외한
`AIMessage.text`를 추출합니다. 이 filter를 완화하지 말고 명시적인 summary block/event
adapter를 추가해야 합니다.

## 안전성과 정직성 경계

- Raw reasoning text나 encrypted reasoning을 display text로 요청, 저장, stream, 노출하지 않습니다.
- Field 이름을 `chain_of_thought`, `internal_thoughts` 또는 같은 의미로 만들지 않습니다.
- Summary는 final answer와 같은 conversation authorization 안의 untrusted model output입니다.
- System/developer prompt, credential, provider trace, hidden system-KB identity, unauthorized
  source metadata, raw document passage를 금지합니다.
- Citation 및 coverage와 분리합니다. Summary는 evidence가 아닙니다.
- 생성 뒤 번역하지 않고 model-authored text를 보존합니다. Request에 intended display locale
  또는 language context를 전달해야 합니다.
- Persist/serialize 전에 item count와 길이를 제한합니다.
- Summary 생성 token은 향후 platform usage ledger에 포함해야 하며 UI metadata라고 무료가
  아닙니다.
- 권장 UI label은 “AI 작업 과정 요약” / “AI approach summary”이며 private CoT가 아니라
  model-generated summary임을 copy로 설명합니다.

## 즉시 구현 순서

1. `reasoning.generate_summary`가 Responses request에 전달되고 summary block이 `reply`로
   유출되지 않음을 증명하는 mocked provider compatibility test를 추가합니다.
2. 민감한 prompt/output을 repository에 저장하지 않는 bounded credentialed provider spike로
   final/streaming block 및 event shape를 확인합니다.
3. Summary item과 두 proposed event payload의 closed Pydantic contract를 추가합니다.
4. Tool authorization을 바꾸지 않고 Luna bounded retrieval-planning display field를 추가합니다.
5. Sol provider summary를 final answer text와 분리해 추출합니다.
6. Completed item을 persist하고 refresh/replay에서 복원하며 typed delta를 stream합니다.
7. Redaction, prompt injection, system knowledge, authorization, length, ordering, nullable,
   deterministic-mode test를 추가합니다.
8. Sibling frontend 구현 전에 hosted OpenAPI를 배포합니다.

## 완료 정의

- Representative focused, comprehensive, web/general, uncertainty-heavy request에서 summary가
  의미 있게 달라집니다.
- Answer text에는 reasoning-summary block이 byte-for-byte 포함되지 않습니다.
- Refresh와 replay가 같은 completed summary를 보존합니다.
- Unsupported/empty/filtered summary는 answer를 실패시키지 않고 빈 list로 degrade합니다.
- Raw CoT, prompt, provider trace, hidden provenance, credential, document body가 typed boundary를
  넘을 수 없습니다.
- `agent_trace`는 verified record로 남고 model-authored summary와 시각적으로 구분됩니다.
- Offline test는 OpenAI key 없이 실행되며 credentialed smoke에는 safe pass/fail evidence만
  기록합니다.
