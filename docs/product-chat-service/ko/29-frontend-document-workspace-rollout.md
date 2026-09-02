# 임시 conversation file frontend rollout

[English](../en/29-frontend-document-workspace-rollout.md) | 한국어

상태: **제안됨 — backend roadmap에서 관리하는 다음 cross-repository task.** Backend capability은
구현됐지만 frontend와 BFF가 아직 사용자에게 제공하지 않습니다.

## Product opportunity

사용자는 conversation에 임시 file을 직접 첨부하고 assistant에게 분석이나 변환을 요청한 뒤,
인증된 spreadsheet artifact를 내려받을 수 있어야 합니다. 이는 durable document를 knowledge
base에 올리는 flow와 다릅니다.

| Workflow | 목적 | 수명 | 검색 동작 |
| --- | --- | --- | --- |
| Knowledge-base upload | 이후 질문에서도 재사용할 source | Product DB content와 index에 durable 저장 | RAG ingestion/retrieval 사용 |
| Conversation attachment | 이 conversation에서 file 분석 또는 변환 | OpenAI file/container는 만료되고 Product DB에는 metadata만 저장 | 선택한 run의 document workspace에 직접 전달 |

Upload 전에 이 차이를 보여야 합니다. Temporary attachment가 searchable knowledge가 된다고
오해하거나, knowledge 추가가 provider-hosted document execution 동의라고 오해하면 안 됩니다.

## Backend readiness

구현된 backend는 이미 다음을 제공합니다.

- enablement, eligibility, format, limit, output certification, retention을 제공하는
  `GET /capabilities/document-workspace`;
- conversation-scoped attachment create/list/delete route;
- conversation run request의 additive `attachment_ids`;
- attachment/artifact metadata를 담는 run response와 safe event;
- conversation-scoped artifact list와 authenticated download route;
- upload별 `provider_consent=true`, ownership check, guest 차단, expiring OpenAI `user_data`
  file, network-disabled hosted container;
- 필요할 때 Hosted Shell과 spreadsheet skill을 사용하는 GPT-5.6 Sol execution.

현재 frontend에는 attachment model, BFF route, service client, composer action, selection state,
artifact presentation이 없습니다. 따라서 API는 준비됐지만 product에서는 접근할 수 없습니다.

## 사용자에게 제공할 기능

### 첨부와 확인

- Served capability가 `enabled=true`, `eligible=true`일 때만 composer에 attachment action 하나를
  추가합니다.
- Capability의 format registry와 limit를 사용하고 extension, file count, byte limit, retention을
  hardcode하지 않습니다.
- Provider consent 전에 filename, type, size, unsupported/empty/too-large error, combined selected
  size를 local validation으로 보여 줍니다.
- 매 upload 전에 OpenAI 전송 동의를 명시적으로 받습니다. Transfer, temporary retention,
  durable knowledge-base storage와의 차이를 action에서 설명합니다.
- Uploading, available, expired, deleting, deleted, failed 상태를 보여 주고 backend contract가
  안전하게 허용하는 경우에만 retry를 제공합니다.

### 한 run에 사용할 file 선택

- 명시적으로 선택한 available attachment ID만 `attachment_ids`로 보냅니다.
- Composer 옆에 selection을 계속 보여 주고 보내기 전에 attachment를 뺄 수 있어야 합니다.
- Refresh 뒤 metadata를 복원하되 expired/deleted file을 되살리지 않습니다.
- Upload 성공 뒤 run 생성이 실패하면 같은 byte를 다시 전송하지 않고 retry할 수 있도록
  attachment를 유지합니다.

### 결과를 정직하게 표시

- Safe `attachments_ready`, `document_workspace_started`, `artifact_created` activity를 표시합니다.
- 생성한 answer/run에 artifact를 연결하고 available 상태에서 authenticated download를 제공합니다.
- Analysis 지원과 certified downloadable output을 구분합니다. 현재 output contract는
  `/mnt/data/output/`의 `.xlsx`, `.csv`, `.tsv`, `.docx`, `.pptx`, `.pdf`, `.md`, `.markdown`,
  `.html`, `.htm`을 인증합니다.
- Text, code 등 나머지 accepted input은 capability가 artifact status를 명시적으로 확장하기
  전까지 analysis만 약속합니다. Certified download는 application 간 pixel-perfect editing
  fidelity를 뜻하지 않습니다.

## Conversation 생성 결정

Attachment endpoint는 conversation ID가 필요하지만 bare `/chat`은 첫 message 전까지
conversation을 만들지 않습니다. No-empty-conversation invariant를 유지합니다.

첫 구현 권장안: bare `/chat`에서는 file을 local stage에 두고 첫 prompt를 보낼 때 conversation을
생성한 뒤 consented file을 upload하고 성공한 attachment ID로 run을 시작합니다. 모든 upload가
실패하면 prompt와 local selection을 유지하고 attachment 없는 run을 조용히 시작하지 않습니다.
첫 run 중 route replacement가 streaming state를 잃게 만든 전례가 있으므로 browser test가
필수입니다.

## Constraint와 caveat

- Disabled/ineligible이면 오해를 부르는 attachment control을 렌더하지 않습니다.
- Guest session은 계속 사용할 수 없습니다.
- File byte는 Product DB에 저장하지 않습니다. Provider expiry 뒤에도 metadata는 honest status와
  usage audit을 위해 남습니다.
- Conversation이 열려 있는 동안에도 expire될 수 있습니다. Server가 expired를 반환하면 즉시
  selection/download를 막고 re-upload path를 제공합니다.
- Delete 실패 시 provider data가 남을 수 있으므로 attachment를 optimistic하게 숨기지 않습니다.
- Limit는 file별 및 selected run 전체에 적용하며 backend validation이 기준입니다.
- Upload/execution은 billable provider operation입니다. Duplicate submit/retry가 usage를 중복
  기록하면 안 됩니다.
- Attachment instruction과 content는 untrusted input입니다. Hosted Shell command, stdout,
  provider trace, hidden reasoning, raw error를 노출하지 않습니다.
- Mobile에서 attachment 제거, consent, send control이 nested horizontal scroll 없이 닿아야 합니다.

## 구현 순서

1. Feature branch OpenAPI를 serve/capture하고 attachment, artifact, capability, run model을 생성합니다.
2. Capability, attachment CRUD, artifact list/download의 exact BFF allowlist를 추가하고 streaming 및
   authenticated download header를 보존합니다.
3. Frontend service/query/mutation boundary와 cache key를 추가합니다.
4. Composer local staging, consent, upload queue, available selection, refresh recovery를 구현합니다.
5. Reasoning 및 knowledge-source control을 보존하면서 normal streamed run에 `attachment_ids`를
   추가합니다.
6. Safe activity와 run-scoped artifact를 expiry-aware download action으로 렌더합니다.
7. Existing conversation과 bare new chat flow를 390, 768, 1280px에서 검증합니다.
8. 작은 non-sensitive file로 owner-approved credentialed E2E를 한 번 실행하고 automated test는
   provider mock으로 offline 유지합니다.

## 완료 정의

- Eligible registered user가 temporary file을 attach/remove/select하고 available 동안 재사용합니다.
- Knowledge base에 넣지 않고 같은 file의 분석 answer를 받습니다.
- Certified spreadsheet 또는 document transformation이 honest expiry를 가진 downloadable
  artifact를 만듭니다.
- Unsupported, oversized, expired, deleted, partial upload, provider failure가 prompt를 보존하고
  misleading run을 시작하지 않습니다.
- Refresh/replay에서 attachment/artifact metadata가 올바른 run에 연결됩니다.
- Guest와 disabled deployment에는 usable attachment path가 없습니다.
- Provider internal, hidden reasoning, credential, shell output, raw file byte가 public event나 frontend
  cache에 들어가지 않습니다.

현재 backend execution 세부 사항은
[OpenAI hosted document workspace 계약](./25-openai-document-workspace.md)이 기준입니다.
