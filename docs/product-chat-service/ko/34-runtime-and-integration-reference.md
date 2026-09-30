# 런타임 설정과 프론트엔드 연동 참고

[English](../en/34-runtime-and-integration-reference.md)

루트 README에 두기에는 자세한 실행 옵션, 운영 절차, 프론트엔드가 의존하는 계약을 모아 둔 문서입니다. 각 항목의 전체 계약은 링크한 주제 문서가 기준입니다.

## 응답 모드와 모델 경계

- `MY_AGENTS_RESPONSE_MODE=deterministic`은 외부 자격 증명 없이 동작하고, 외부 결정 호출도 끕니다.
- 실제 OpenAI 응답을 받으려면 `.env`에 `OPENAI_API_KEY`를 넣고 `MY_AGENTS_RESPONSE_MODE=openai`로 실행합니다. 일반 응답은 `langchain-openai`의 `ChatOpenAI` 경계를 거칩니다. 선택 기능인 임시 document workspace만 Files, Containers, Hosted Shell, Skills를 쓰기 위해 격리된 OpenAI SDK adapter를 사용합니다.
- 출처 선택, 검색 도구 선택, ContextForge intent 분류는 별도의 제한된 결정 provider가 맡습니다. 설정 방법, fallback 규칙, 외부로 보내는 데이터의 범위는 [RAG Agent README](../../../my_agents/agents/rag_agent/README.md)에 있습니다.
- 어시스턴트는 공식 도메인 `https://my-agents.dev`에 연결된 일관된 `my-agents` 정체성을 유지하고, 바뀔 수 있는 제품 정보는 권한이 확인된 context에 근거해 답합니다.
- 사용자가 어떤 모델과 대화 중인지 물으면 설정된 모델 ID를 알려 주도록 지시합니다. 일반 채팅 system prompt에는 API 요청과 같은 설정에서 가져온 실제 run 모델이 들어갑니다. 일반 계정의 저장된 선택 또는 `MY_AGENTS_OPENAI_MODEL` 배포 fallback을 사용합니다. 이 값은 설정한 모델 ID이며 provider가 내부적으로 고른 snapshot을 뜻하지 않습니다.
- 일반 답변은 친근하고 이해하기 쉬운 말투를 씁니다. 간단한 질문은 짧게, 학습용이거나 복잡한 질문은 필요한 깊이로 답합니다. 서버 기본값은 `MY_AGENTS_OPENAI_VERBOSITY=medium`이고 `low`/`high`도 설정할 수 있습니다. 기존 환경 변수 override가 우선하며 출력 토큰 예산은 그대로입니다. 사용자별 스타일 설정은 [제안](../../idea/assistant-behavior-preferences.md) 단계이며 아직 구현되지 않았습니다.

## Run reasoning 설정

일반 계정의 run 요청은 선택적으로 `reasoning_mode`(`standard` 또는 `pro`)와 `reasoning_effort`(`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`)를 보낼 수 있습니다. 생략하면 mode는 `standard`, effort는 실행 surface 모델의 `my_agents/model_defaults.py` application 기본값을 사용합니다(API가 지원하는 여섯 모델 모두 `medium`). Guest는 어떤 값을 보내도 `standard`와 해당 모델 기본 effort로 고정됩니다. 기존 `MY_AGENTS_OPENAI_REASONING_EFFORT` 환경 변수는 무시합니다. 실제 기본값과 모델 지원 여부는 `GET /capabilities/reasoning`에서 확인합니다. `pro`는 지원하는 GPT-5.6, GPT-6, GPT-6.1 Sol 모델에서 허용됩니다.

GPT-5.6과 GPT-6 모델에서 `minimal`은 호환용 alias로 받아서, run 저장과 provider 호출 전에 `low`로 바꿉니다. 일반 채팅, document workspace, replay 상속, guest를 포함한 서버 기본값에 모두 적용되며 응답과 event에는 실제 적용값 `low`가 표시됩니다. GPT-6.1 Sol과 Astra는 `none`도 `low`로 변환하며 다른 effort 값은 그대로입니다.

전체 계약: [Run reasoning 설정 계약](./26-run-reasoning-preferences.md)

## 임시 document workspace

`MY_AGENTS_DOCUMENT_WORKSPACE_ENABLED=true`로 켜면 승인된 일반 계정이 대화에 임시 파일을 첨부해 GPT-5.6 Sol로 분석하고, 검증된 결과물(`.xlsx`, `.csv`, `.tsv`, `.docx`, `.pptx`, `.pdf`, `.md`, `.markdown`, `.html`, `.htm`)을 내려받을 수 있습니다. Guest에게는 열리지 않고, 업로드할 때마다 OpenAI로 파일을 보내는 데 동의해야 합니다. 파일 본문은 Product DB에 저장하지 않고, 만료되는 OpenAI `user_data` file과 네트워크가 차단된 hosted container에만 둡니다.

전체 계약: [OpenAI document workspace](./25-openai-document-workspace.md)

## LangGraph persistence와 실험적 메모리

- PostgreSQL 배포에서는 PostgresStore와 PostgresSaver를 기본 LangGraph 리소스로 구성합니다. PostgreSQL 트래픽을 받기 전에 `uv run python -m scripts.langgraph_persistence setup`을 실행하세요. 배포 container에서는 같은 운영 명령을 `uv run --no-sync python -m scripts.langgraph_persistence <command>`로 실행합니다.
- PostgresSaver 덕분에 모호한 문서 기반 run은 `202 waiting_for_input`으로 멈췄다가, 프로세스가 재시작돼도 권한 있는 문서를 고르면 이어서 실행됩니다. SQLite에서는 Product DB recall과 영속되지 않는 graph 실행 fallback을 씁니다.
- LangGraph 공유 connection pool은 checkpoint·Store 작업 전에 연결 상태를 확인하고, 서버가 끊은 idle connection을 교체합니다. graph 전체를 다시 실행하는 것은 아니며, 작업 도중 끊긴 연결까지 자동으로 복구하지는 않습니다. 추가 환경 변수나 migration은 필요 없습니다.
- Store는 Product DB가 관리하는 memory의 semantic projection입니다. 사용자별 실험 설정이 동의와 사용 자격을 정하고, status·sensitivity·provenance·source staleness는 계속 Product DB row가 강제합니다. 사용자에게 실험적 메모리를 열기 전에 reconciliation 결과에 drift가 없는지 확인하세요. PostgreSQL checkpoint 재시작 스모크는 2026-08-17에 local pgvector 프로필에서 별도로 통과했습니다.
- 실험적 메모리는 지금 명시적으로 저장한 memory와 사용자가 직접 확인한 제안만 불러옵니다. 일반 채팅이 memory를 자동으로 만들지는 않으며, 대화 후에 도는 별도 `memory_graph` 추출·갱신 흐름은 계획 단계입니다.

참고: [LangGraph-native memory migration](./19-langgraph-native-memory-migration.md)

## 전체 문서 검색

전체 문서 검색은 “문서 전체를 빠짐없이 검토해”나 “해당 문서에서 빠짐없이 검토해”처럼 전체 검토 의도가 분명한 요청에서 동작합니다. RAG Agent가 범위를 좁힌 청크 검색과 전체 문서 읽기 중 하나를 고르고, deterministic 모드나 provider 오류 시에는 같은 계약의 로컬 fallback을 씁니다.

- `MY_AGENTS_FULL_DOCUMENT_MAX_CHARS=24000`은 한 번에 완전히 검토할 수 있는 한도입니다. 더 큰 문서는 현재 앞쪽 `MY_AGENTS_FULL_DOCUMENT_RANGE_CHARS=12000`자만 읽고 `mode=partial`을 반환합니다.
- 파일명이 정확하면 자동으로 정합니다. 모호하면 현재 권한 범위에서 관련도 순으로 최대 5개 후보를 보여 주고, 같은 run에서 파일명 단서를 최대 두 번 더 받은 뒤에야 전체 목록 탐색을 엽니다.
- 전체 문서 graph는 대기 run 호환 버전을 `general-assistant-checkpoint-v2`로 올립니다. 배포 전에 이전 버전의 대기 run을 drain하거나 취소해야 합니다. 구버전 run은 재개할 수 없어 version-mismatch 경로에서 안전하게 failed 처리됩니다.

참고: [권한 기반 RAG 설계](./06-permission-aware-rag.md)

## 배포 참고

- 대화 실행 시작은 데이터베이스에서 원자적으로 처리합니다. 배포 전에 Alembic `20260905_0034`를 적용하세요. [마이그레이션과 경쟁 요청 처리](../en/31-atomic-run-admission.md)를 참고하세요.
- Render pre-deploy 절차: [Render migration and rollback notes](../en/14-render-migration-and-rollback-notes.md#production-pre-deploy-guardrail)
- 운영·마이그레이션 명령: [scripts/README.md](../../../scripts/README.md)

## 로컬 도구

VS Code의 `FastAPI: uvicorn main:app (local pgvector)` 프로필은 실행 전 마이그레이션을 Python 확장이 선택한 인터프리터로 돌립니다. GUI로 켠 VS Code의 `PATH`에 `uv`가 없어도 동작하지만, 쓰기 전에 이 저장소의 `.venv` 인터프리터를 선택해 두세요.

## 프론트엔드가 의존하는 계약

- 공개 엔드포인트 `GET /auth/guest/policy`는 배포된 환경의 게스트 유효 시간, 사용 한도, 코드 전달 방식을 반환합니다. UI는 이 값을 화면에 고정하지 말고 그대로 보여 줘야 합니다.
- Run 응답에는 보수적으로 판정한 답변 지원 `citations`, 별도로 공개하는 `consulted_sources`, 사람이 읽을 수 있는 document/knowledge-base citation metadata가 담깁니다.
- HTTP·검증 오류는 기존 `detail`과 함께 기계가 읽을 수 있는 `code`를 반환합니다. UI는 `code`를 번역 키로 쓰고 `detail`은 진단용으로만 취급해야 합니다.
- `GET /conversations/{conversation_id}/runs/{run_id}/events`는 `event_type`으로 구분되는 닫힌 union입니다. 기존 run/retrieval/graph/workspace/answer/cancellation/failure 이벤트에 `run_interrupted`, `run_resumed`, 원문을 뺀 `full_document_read` metadata가 추가됩니다.
- Checkpointer가 켜져 있으면 run 생성은 `200 completed` 또는 `202 waiting_for_input`을 반환합니다. 대기 중인 document-selection interaction은 새로고침 뒤에도 복구되며, resume은 guest prompt를 추가로 소비하지 않습니다. Resume SSE는 `run_resumed` 뒤에 실제 진행 event와 answer delta를 보내고, 단서로 해결하지 못하면 새 `run_interrupted`를 보냅니다. Ambient system knowledge는 선택지로 노출하지 않습니다. 새 interaction은 semantic `schema_version=2`를 쓰고, 이미 대기 중인 V1 checkpoint도 계속 resume할 수 있습니다. 세부 규칙은 [agent와 frontend 사이의 interaction 계약](./27-agent-frontend-interaction-contract.md)에 있습니다.
- 완료된 comprehensive-document run은 sync/stream/replay/새로고침 응답에 nullable `document_coverage`를 추가합니다. `mode`, 문서 metadata, `[start_offset, end_offset)`, `total_chars`를 제공하지만 원문이나 내부 continuation cursor는 노출하지 않습니다. 부분 답변은 전체 문서 검토가 아니라는 현지화된 안내로 시작합니다.
- `GET /capabilities/document-workspace`는 현재 활성화 여부와 사용 자격, 허용 형식, 제한, 보관 기간을 반환합니다. 첨부는 `POST/GET/DELETE /conversations/{conversation_id}/attachments`, 결과물은 `GET /conversations/{conversation_id}/artifacts`와 해당 download URL을 사용합니다. Run 요청의 `attachment_ids`가 실제 실행에 쓸 파일을 고릅니다.
- `GET /capabilities/reasoning`은 surface별 Pro 지원 여부, 서버 기본 effort, 허용 enum, 현재 계정의 변경 가능 여부를 반환하며, provider의 원본 모델 식별자는 의도적으로 뺍니다. Run/replay 요청의 선택적 `reasoning_mode`와 `reasoning_effort`는 실제 적용값으로 run에 저장되고, 응답과 `run_started` event에 다시 담깁니다.
- 완료된 run은 retrieval planning과 answer synthesis에 대한 길이 제한된 `reasoning_summaries`도 반환할 수 있습니다. 모델이 작성한 이 설명은 nullable이며, `reasoning_summary_delta`로 답변과 따로 stream되고 refresh/replay를 위해 전용 event로 저장됩니다. 검증된 `agent_trace`, 인용, 답변 본문을 대체하지 않습니다.
- 일반 OpenAI 답변은 `ChatOpenAI.stream()`을 쓰므로 LangGraph가 실제 provider chunk를 여러 `answer_delta` event로 전달합니다. Provider는 같은 chunk를 최종 저장용으로 합치고, reasoning-summary block은 답변 본문에 섞지 않습니다. Deterministic fallback과 comprehensive-document 안내 경로는 설계상 계속 buffer할 수 있습니다.
- 저장된 이벤트의 payload와 `agent_trace`는 이벤트·단계별 허용 목록을 통과한 필드만 내보냅니다. `answer_delta`, `run_completed`, `run_error`는 스트리밍 전용이라 저장되는 이벤트 union에 들어가지 않습니다.
- Skip되지 않은 각 `agent_trace` step은 버전이 붙은 선택적 `operational_summary`를 제공합니다. 닫힌 semantic message key와 이벤트별 안전한 parameter로 구성한, 애플리케이션이 검증한 채널이며 모델이 작성한 `reasoning_summaries`와는 분리됩니다. 알 수 없는 미래 버전이면 검증된 step은 유지하고 summary만 무시합니다.
- 일반·resume·replay 대화 stream의 OpenAPI는 `text/event-stream.x-sse-events`에 SSE 전용 `reasoning_summary_delta` payload를 게시합니다. 개별 delta에는 500자 상한을 두지 않고, 저장되는 최종 summary item에만 길이 제한을 적용합니다.
- 비동기 수집 진행률은 `queued=0`, `claimed=1`, `chunking=15`, `embedding=45`, 선택적으로 `indexing=70`, `entities=85`, `metadata=95`, `completed=100`으로 저장되며 폴링 엔드포인트에서 읽을 수 있습니다. 경과 시간이 아니라 어느 단계까지 왔는지를 나타내는 값입니다.

더 넓은 스트리밍 계약은 [HTTP streaming and frontend contract](./09-http-streaming-frontend-contract.md)에 있습니다.

## Assistant 모델 선택

일반 계정은 `GET/PATCH /assistant/preferences`로 모델을 저장합니다. 배포 fallback은
`MY_AGENTS_OPENAI_MODEL`이며 guest는 그 모델로 고정됩니다. 선택지는
`GET /capabilities/assistant-models`로 조회하고 reasoning capability는 저장된 선택을 반영합니다.
`{"assistant_model": null}`로 reset합니다. 일반 run/stream/replay는 선택 모델을 고정하고
resume는 해당 모델을 유지합니다. 기존 DB 실행 전에 migration `20260930_0035`를 적용하세요.
전체 계약: [assistant 모델 선택](./35-assistant-model-preferences.md).
