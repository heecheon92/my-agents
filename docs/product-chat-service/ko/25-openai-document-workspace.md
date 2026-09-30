# OpenAI hosted document workspace

[English](../en/25-openai-document-workspace.md) | 한국어

## 상태와 범위

승인된 일반 계정이 명시적으로 선택해 쓰는 conversation 기능입니다. Render의 CPU/RAM으로 office file editing을 처리하지 않고, 무거운 문서 분석과 spreadsheet 생성을 OpenAI에 위임합니다. Deep Agents, 두 번째 assistant, 영구 file storage, guest access는 추가하지 않습니다.

일반 chat은 기존 `ChatOpenAI` provider를 그대로 사용합니다. 첨부가 있는 turn만 좁은 OpenAI SDK adapter를 사용합니다. 현재 `ChatOpenAI` surface가 Files, Containers, Hosted Shell, Skills API를 모두 노출하지 않기 때문입니다.

## Lifecycle

```mermaid
sequenceDiagram
    participant UI
    participant API as FastAPI
    participant DB as Product DB
    participant OpenAI
    participant Graph as general_assistant

    UI->>API: file + provider_consent=true 업로드
    API->>OpenAI: Files API (purpose=user_data, expiry)
    API->>DB: metadata + normalized upload usage
    UI->>API: run(message, attachment_ids)
    API->>DB: conversation과 attachment 권한 확인
    API->>Graph: 기존 run + document_workspace_runtime
    Graph->>OpenAI: expiring network-disabled container
    Graph->>OpenAI: GPT-5.6 Sol + Hosted Shell (+ spreadsheet skill)
    OpenAI-->>Graph: reply + /mnt/data/output/ 아래 file
    Graph->>DB: artifact metadata + normalized token/tool usage
    API-->>UI: attachment와 certified artifact가 포함된 run response
    UI->>API: 인증된 artifact download
    API->>OpenAI: container file byte stream
```

Product DB에는 file metadata, run association, workspace metadata, artifact metadata, 대화 단위 파일 메모, immutable normalized usage event를 저장합니다. Response usage event는 input, cached-input, output, reasoning token을 구분하고 Hosted Shell 실행 여부를 기록합니다. 업로드하거나 생성한 file byte는 저장하지 않습니다. 새 OpenAI file은 기본값으로 업로드 후 7일 뒤, hosted container는 20분 idle 뒤 만료됩니다. 만료된 metadata는 UI가 상태를 정직하게 표시하고 usage를 감사하는 데 남습니다.

## Public API 계약

- `GET /capabilities/document-workspace`: 실제 enable 상태, account eligibility, format registry, limit, retention.
- `POST /conversations/{conversation_id}/attachments`: multipart `file`과 필수 `provider_consent=true`.
- `GET /conversations/{conversation_id}/attachments`: attachment metadata와 expiry 상태.
- `DELETE /conversations/{conversation_id}/attachments/{attachment_id}`: 남아 있는 provider file을 지우고 metadata를 deleted로 변경.
- `POST /conversations/{conversation_id}/runs`: additive `attachment_ids` list를 받음.
- `GET /conversations/{conversation_id}/artifacts`: 생성된 artifact metadata.
- `GET /conversations/{conversation_id}/artifacts/{artifact_id}/download`: active provider container에서 인증된 byte stream을 proxy.

Run response에는 `attachments`와 `artifacts`가 추가됩니다. Display-safe persisted event enum에는 `attachments_ready`, `document_workspace_started`, `artifact_created`가 추가되며, payload는 count, provider가 제공한 경우의 byte size, filename, content type, ID, expiry timestamp만 노출합니다.

## Format과 output certification

Analysis allowlist는 `my_agents/document_workspace/formats.py`에서 versioning하며 2026-08-09에 확인한 OpenAI File Inputs의 PDF, spreadsheet, rich-document, presentation, text/code extension family를 반영합니다. Video와 임의 binary는 범위 밖입니다. Frontend가 목록을 hardcode하지 않도록 capability endpoint가 실제 registry를 제공합니다.

분석 가능 범위와 output 인증 범위는 다릅니다. `/mnt/data/output/` 아래의 `.xlsx`, `.csv`, `.tsv`, `.docx`, `.pptx`, `.pdf`, `.md`, `.markdown`, `.html`, `.htm` 결과는 downloadable artifact가 됩니다. 다른 허용 input도 분석할 수 있지만 assistant는 downloadable edited document를 만들었다고 주장하면 안 됩니다. 여기서 인증은 Hosted Shell 결과를 인식하고 만료되는 artifact metadata로 보관해 authenticated download path로 제공한다는 뜻입니다. 모든 office application에서 pixel-perfect fidelity를 보장한다는 뜻은 아니므로, 사용자는 생성된 file을 실제 용도에 쓰기 전에 검토해야 합니다.

## Security와 경제성 경계

- 기본 disabled이며 guest principal을 거부합니다.
- 매 upload마다 provider 전송 동의가 필요합니다.
- Provider 실행과 download 전에 conversation ownership과 attachment ownership을 확인합니다.
- Hosted container network는 disabled입니다.
- Upload file, retrieved KB snippet, memory snippet은 provider instruction에서 untrusted data로 취급합니다.
- Provider trace, shell command, stdout, prompt, credential, hidden reasoning은 public event에 들어가지 않습니다.
- Usage event는 input/output/cached token, file-input byte, container start, hosted-shell call 같은 provider-neutral unit을 기록합니다. Unique idempotency key가 중복 기록을 막으므로, 이후 credit settlement는 Langfuse나 특정 model vendor와 독립적으로 설계할 수 있습니다.

## 보류한 확장: attachment 없는 artifact 생성

현재 구현은 run에 `attachment_id`가 하나 이상 있을 때만 `document_workspace_runtime`을 만듭니다. 따라서 “이 개념을 HTML file로 설명해 줘” 같은 요청은 지금은 일반 chat response로 남습니다. Downloadable extension allowlist를 넓히는 것만으로 Hosted Shell이 실행되지는 않습니다.

향후 milestone에서는 General Assistant가 소유하는 typed `create_artifact` capability를 추가할 수 있습니다. RAG Agent는 output file 생성이 아니라 retrieval 판단을 계속 소유해야 합니다. 사용자가 downloadable result를 명시적으로 요청하면 General Assistant가 server-approved output format을 고르고, attachment가 없거나 여러 개 있는 상태로 기존 document-workspace adapter를 호출할 수 있습니다. Attachment가 없다면 adapter는 비어 있는 expiring network-disabled hosted container를 만들고, 생성된 file은 기존 artifact metadata, event, expiry, authorization, usage accounting, authenticated download path를 재사용합니다.

이 확장은 의도적으로 보류했으며 immediate next task가 아닙니다. 구현 전에 다음을 결정해야 합니다.

- Natural-language intent만으로 충분한지, frontend에 명시적인 file-format control도 제공할지
- Downloadable HTML/Markdown과 chat 안의 code block을 구분하려면 요청이 얼마나 명시적이어야 하는지
- Model이 closed output-format enum에서 고를지, client가 format을 직접 요청할 수 있는지
- User file 전송이 없는 Hosted Shell 실행에 어떤 account credit과 limit을 적용할지
- Artifact 생성 요청에서는 tool choice를 optional로 두지 않고 Hosted Shell을 필수로 할지

User byte가 browser 밖으로 나가지 않으면 provider-transfer consent는 필요하지 않지만, feature eligibility, guest restriction, cost accounting, output allowlist, safe event disclosure는 그대로 적용합니다. 이 경로는 client에 arbitrary shell access, provider trace, unrestricted filename을 노출하면 안 됩니다.

## 배포

Flag를 켜기 전에 Alembic revision `20260809_0030`을 적용합니다. `OPENAI_API_KEY`와 `MY_AGENTS_DOCUMENT_WORKSPACE_ENABLED=true`를 설정하고, 문서화된 `MY_AGENTS_DOCUMENT_WORKSPACE_*` 환경 변수로 제한을 조정합니다. 이 경로를 위해 Render process에 local office suite, code sandbox, high-memory parser를 추가하지 않습니다.

Test suite는 provider boundary를 offline fake로 교체합니다. Credential을 쓰는 live smoke는 OpenAI file, container, model token, Hosted Shell 비용이 발생할 수 있으므로 operator가 명시적으로 실행하는 단계로 남깁니다.

## 이미지 attachment

Workspace는 정지 JPEG(`.jpg`, `.jpeg`), PNG(`.png`), WebP(`.webp`), GIF(`.gif`)를
시각 분석 입력으로 받습니다. Registry에는 category `image`, 확장자별 canonical MIME,
`analysis_supported=true`, `artifact_status=unavailable`을 제공합니다. 이미지 입력 지원은
인증된 출력 형식이나 이미지 생성 기능을 추가하지 않습니다.

Provider로 보내기 전에 consent/account/conversation 소유권, 확장자, MIME와 기존 byte 제한을
검증합니다. 기존 Docling/office dependency tree에 포함된 Pillow로 실제 encoding, decode 가능 여부,
정지 여부를 확인합니다. 손상되거나 확장자/MIME가 다른 이미지, animated GIF/APNG/WebP와
Pillow decompression-bomb 입력은 415 `unsupported_attachment_type`입니다.
Consent false는 기존 400, 필수 consent field 누락은 422를 유지합니다. 원본 bytes를 유지하며
검증한 generic binary MIME은 canonical MIME으로 바꿉니다. 로컬 검증은 제한된 image decode이며
분석은 hosted 환경에서 수행합니다. 새 package나 DB migration은 없습니다.

Upload는 `purpose=user_data`와 명시적 expiry를 유지합니다. Image는 network-disabled container에
그대로 mount하고 Responses에는 `input_image` file-ID로 전달합니다. 문서는 `input_file`입니다.
혼합 입력의 순서도 유지합니다.

```json
[
  {"type": "input_text", "text": "Compare this picture with the document"},
  {"type": "input_image", "file_id": "file-image", "detail": "high"},
  {"type": "input_file", "file_id": "file-document"}
]
```

`detail=high`로 provider 전처리를 제한해 original-resolution auto 모드의 큰 사진 patch-limit
거부를 피합니다. 기존 파일 개수/합계 byte, consent, 소유권, guest 차단, retention, 모델과
출력 인증 정책을 유지합니다. Image attachment turn도 별도 workspace 모델을 사용합니다.
근거: [image 입력 요구사항](https://developers.openai.com/api/docs/guides/images-vision),
[Files purpose/expiry](https://developers.openai.com/api/reference/typescript/resources/files/methods/create).

[대화 맥락 유지와 파일 보관 정책](./36-conversation-continuity.md)은 메시지 연결, 대화 메모, 일반 계정의 자동 재참조와 정리를 추가합니다. 원본 byte는 provider에 남고 메모는 Product DB의 별도 파생 기록입니다. 새 원본의 기본 만료는 업로드 후 7일입니다.
