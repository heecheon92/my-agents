# Rich response rendering과 future agent-UI boundary

[English](../en/30-rich-response-rendering-and-agent-ui-boundaries.md) | 한국어

상태: **제안됨 — backend roadmap에서 관리하는 두 번째 immediate cross-repository task.** 첫
milestone은 assistant Markdown 안의 safe Mermaid rendering입니다. AG-UI와 A2UI는 이 milestone의
dependency가 아니라 future adapter decision입니다.

## 필요한 이유

Assistant answer는 이미 Markdown을 사용하고 frontend는 `react-markdown`으로 일반 Markdown을
렌더합니다. 그러나 fenced `mermaid` block은 diagram이 아니라 source code로 남습니다. Model이
architecture, request flow, state, sequence, data relationship을 시각적으로 설명해도 사용자는
의도한 형태로 볼 수 없습니다.

Immediate goal은 unrestricted generative UI가 아닙니다. 알려진 answer part를 maintained, secure,
accessible component로 렌더하고 모든 enhancement가 durable text fallback을 가지는 renderer
catalog를 만드는 것입니다.

## Milestone 1: Markdown answer 안의 Mermaid

### Rendering contract

- 기존 `react-markdown` component boundary에서 normalized language가 `mermaid`인 fenced code
  block을 감지합니다. 다른 code block은 현재 renderer를 유지합니다.
- Closing fence가 도착한 뒤 또는 answer가 settle된 뒤에만 렌더합니다. 매 token마다 incomplete
  Mermaid syntax를 반복 parse하지 않습니다.
- Diagram이 있는 answer에서만 Mermaid를 lazy load하고 dependency를 채택하기 전에 production
  bundle impact를 기록합니다.
- Application boundary에서 `startOnLoad=false`, `securityLevel="strict"`로 initialize합니다.
  Model-authored content에 `loose`/`antiscript`, click handler, secure site config override를 허용하지
  않습니다.
- Source length, edge, render-time, rendered-size limit를 둡니다. Malformed/expensive diagram 하나가
  주변 answer를 잃게 하면 안 됩니다.
- Collision-safe render ID를 만들고 streamed content, theme, route, message identity가 바뀌면 stale
  render를 cancel합니다.

### User experience

- Fence는 완성됐지만 rendering 중이면 주변 answer를 크게 움직이지 않는 compact skeleton을
  보여 줍니다.
- 성공하면 message width에 맞는 responsive `figure`를 렌더하고, mobile evidence가 필요성을
  증명할 때만 zoom/open affordance를 추가합니다.
- Accessible name과 text alternative 또는 source-code disclosure를 제공합니다. SVG만으로는 screen
  reader, copy, print, failed-render path를 충족하지 못합니다.
- Parse/render 실패 시 localized failure copy와 original fenced source disclosure를 보여 줍니다. Raw
  Mermaid error HTML이나 stack trace를 표시하지 않습니다.
- Answer copy는 generated SVG markup이 아니라 Markdown source를 유지합니다.
- Product theme 변경 시 theme-safe Mermaid variable로 다시 렌더하고 390, 768, 1280px에서 contrast를
  검증합니다.

### Security와 quality caveat

- Model-generated diagram source는 우리 model에서 왔어도 untrusted content입니다.
- Mermaid default `strict` security level은 HTML label을 encode하고 click을 막습니다. 이를 약화하면
  별도 security review가 필요합니다.
- Syntax가 맞아도 diagram 내용은 틀리거나 너무 복잡할 수 있습니다. Rendering은 model claim을
  검증하지 않습니다.
- Mermaid distribution은 initial JavaScript에 큰 영향을 줄 수 있습니다. Product가 약속할 diagram
  type을 기준으로 full Mermaid, Mermaid Tiny, route-local dynamic import를 비교합니다.
- 첫 slice는 SSR이 필요하지 않습니다. Browser-only rendering을 가장 작은 client component에 두고
  주변 Markdown hydration을 바꾸지 않습니다.

## Renderer architecture

```mermaid
flowchart LR
    Answer["Persisted Markdown answer"] --> Parser["react-markdown"]
    Parser --> Text["Maintained Markdown components"]
    Parser --> Fence{"Fenced language"}
    Fence -->|"mermaid"| Mermaid["Safe lazy Mermaid renderer"]
    Fence -->|"other"| Code["Existing code renderer"]
    Mermaid --> Diagram["Accessible responsive figure"]
    Mermaid -->|"failure"| Fallback["Localized error + source disclosure"]
```

Persisted assistant Markdown가 durable source of truth입니다. Rendered SVG, component state, zoom
state는 derived frontend artifact이며 Product DB에 저장하지 않습니다.

## Future AG-UI integration boundary

AG-UI는 streamed text, tool call, lifecycle, state snapshot/delta, activity update, attachment,
human interaction을 다루는 event-based agent-to-application protocol입니다. Interoperability나 더
풍부한 bidirectional agent event가 product need가 되면 현재 REST/SSE transport edge에 adapter를
추가합니다.

```mermaid
flowchart LR
    Domain["Product DB + semantic run contracts"] --> Current["Current REST/SSE adapters"]
    Domain -. "future" .-> AGUI["AG-UI adapter"]
    Current --> Frontend["Current frontend client"]
    AGUI -. "optional client" .-> Frontend
```

Adapter는 기존 run, message, trace, reasoning-summary, tool, artifact, interaction semantic을 map해야
합니다. Product DB transcript/audit를 대체하거나 authorization/redaction을 약화하거나 AG-UI event
state를 persistence model로 만들거나 Mermaid milestone에 transport 변경을 강요하면 안 됩니다.
Adoption에는 written mapping과 replay/resume/cancellation parity test가 필요합니다.

## Future A2UI integration boundary

A2UI는 structure와 data를 분리한 streamed declarative JSON message로 UI를 설명합니다. Maintained
renderer catalog를 넘어 model-selected interactive component가 필요한 경우에만 검토합니다. 예를
들어 control이 있는 comparison table, review form, artifact inspection surface입니다.

A2UI slice는 다음을 지켜야 합니다.

- versioned closed application-owned component catalog;
- 모든 component, property, binding, action, data update의 render 전 validation;
- arbitrary HTML, JavaScript, URL, event handler, style injection, backend authority claim 차단;
- user action을 normal authorization/confirmation을 거치는 typed application command로 mapping;
- unsupported client, refresh, export, audit, accessibility를 위한 Markdown/text fallback;
- Product DB conversation/HITL domain model이 아닌 adapter/presentation artifact.

AG-UI와 A2UI는 다른 layer를 해결합니다. AG-UI는 agent/application event를 표준화하고 A2UI는
constrained generated surface를 설명합니다. 나중에 함께 쓸 수 있지만 Mermaid rendering에는 둘 다
필요하지 않습니다.

## 구현 순서

1. 현재 assistant Markdown renderer, streaming, sanitization, copy action, theme token, code-block test를
   audit합니다.
2. Dependency/bundle comparison을 기록하고 Mermaid loading strategy 하나를 승인합니다.
3. Strict security, bounded rendering, cancellation, theme, accessible fallback, error isolation을 갖춘 leaf
   Mermaid component를 구현합니다.
4. 완료된 `mermaid` fence만 current Markdown code-block component에서 route합니다.
5. Supported/invalid/oversized fixture unit test와 streaming, theme, resize, copy, print/fallback, mobile
   overflow browser test를 추가합니다.
6. Future maintained answer component를 위한 renderer registry seam을 문서화합니다.
7. Transport interoperability가 approved milestone이 될 때만 AG-UI mapping을 작성하고 구현은 별도
   승인합니다.
8. Markdown/Mermaid가 해결할 수 없는 concrete interactive use case가 생길 때만 A2UI catalog/security
   proposal을 작성하고 구현은 별도 승인합니다.

## 완료 정의

- Valid flowchart, sequence, state, entity-relationship fixture가 closing fence 뒤 diagram으로 렌더됩니다.
- Invalid, unsupported, oversized, timed-out diagram이 answer를 읽을 수 있게 유지하고 safe source
  fallback을 제공합니다.
- Model-authored Mermaid가 HTML label, link, callback, script, insecure config를 켤 수 없습니다.
- Streaming Markdown이 flicker, 반복 parse error, auto-scroll intent 손실을 만들지 않습니다.
- Light/dark theme, reduced motion, keyboard, screen reader, narrow mobile, answer copy, refresh, replay를
  검증합니다.
- Non-Mermaid Markdown behavior는 바뀌지 않습니다.
- Bundle cost와 lazy-loading behavior를 측정하고 기록합니다.
- AG-UI/A2UI는 implied dependency가 아니라 explicit adoption gate를 가진 optional future adapter로
  남습니다.

Reference: [react-markdown component override](https://github.com/remarkjs/react-markdown),
[Mermaid usage와 security](https://mermaid.js.org/config/usage),
[AG-UI overview](https://docs.ag-ui.com/), [A2UI concepts](https://a2ui.org/concepts/overview/).
