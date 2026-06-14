# Knowledge Base path OpenAPI handoff

[English original](../en/12-knowledge-base-path-openapi-handoff.md) | 한국어

이 문서는 KB-first 문서 경로, 그룹 업로드 임시 저장, 채팅 source selection을 프론트엔드에
전달하기 위한 backend-to-frontend handoff 요약입니다.

필터된 OpenAPI artifact:

- `docs/product-chat-service/en/12-knowledge-base-path-openapi-handoff.json`

## 제품 계약 요약

지식베이스는 사용자에게 보이는 검색 가능한 문서 라이브러리입니다.
프론트엔드 기본 흐름은 다음과 같습니다.

1. 지식베이스를 생성하거나 선택합니다.
2. 개인 문서는 해당 KB에 text/PDF/Markdown/plain-text 파일로 추가합니다.
3. 승인 경계가 필요한 그룹 문서는 `POST /knowledge-bases/team-upload-staging`으로
   업로더 전용 숨김 staging KB를 만든 뒤 그 KB에 원본 문서를 씁니다.
4. `POST /groups/{group_id}/publish-requests`로 게시 요청을 만들고,
   approve/reject endpoint로 검토를 완료합니다.
5. 승인된 group copy를 대상 group KB 안에서 ingest합니다.
6. 채팅에서는 All KBs 또는 선택한 KB 집합만 retrieval source로 사용합니다.

## 프론트엔드가 우선 써야 하는 route

- `GET /knowledge-bases`
- `POST /knowledge-bases`
- `POST /knowledge-bases/team-upload-staging`
- `GET /knowledge-bases/{knowledge_base_id}`
- `GET /knowledge-bases/{knowledge_base_id}/documents`
- `POST /knowledge-bases/{knowledge_base_id}/documents`
- `POST /knowledge-bases/{knowledge_base_id}/documents/upload`
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest`
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest/async`
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs`
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}`
- `GET /groups/{group_id}/publish-requests`
- `POST /groups/{group_id}/publish-requests`
- `POST /groups/{group_id}/publish-requests/{request_id}/approve`
- `POST /groups/{group_id}/publish-requests/{request_id}/reject`

`/documents`, `/documents/upload` compatibility route도 남아 있지만 standalone/developer 용도이며,
제품 UI의 기본 경로는 KB-scoped route입니다.

## 그룹 업로드 / 게시 요청 규칙

- `POST /knowledge-bases/team-upload-staging`은 `purpose=team_upload_staging`인
  숨김 personal KB를 반환합니다.
- staging KB는 KB-scoped document create/upload로는 쓸 수 있지만, 일반 KB 목록,
  chat source selection, 일반 retrieval에서는 제외됩니다.
- 문서 단위 publish request는 `source_document_id`와 `target_knowledge_base_id`가 필요합니다.
- KB 전체 publish request는 `source_knowledge_base_id`를 사용하며 group 자체를 대상으로 하고,
  `target_knowledge_base_id`를 보내면 안 됩니다.
- `KnowledgePublishRequestResponse`가 pending/approved/rejected 상태를 UI에 전달하는
  canonical payload입니다.
- 승인되면 source가 target group KB로 복사되고, retrieval은 staging source가 아니라
  승인된 group copy를 사용해야 합니다.

## 채팅 source selection

`ConversationRunRequest`는 다음 shape를 받습니다.

```json
{
  "message": "What do my docs say?",
  "knowledge_base_selection": {
    "mode": "selected",
    "knowledge_base_ids": ["kb_..."]
  }
}
```

규칙:

- `mode: "all"`은 권한 있는 모든 KB를 검색하며 ID를 함께 보내면 안 됩니다.
- `mode: "selected"`는 전달한 KB ID만 hard retrieval boundary로 사용합니다.
- `mode: "selected"`인데 ID가 없으면 `422`입니다.
- `mode: "all"`인데 ID가 있으면 `422`입니다.
- 존재하지 않거나 권한 없는 KB ID는 `404`입니다.
- staging KB ID는 chat selection 대상으로 쓰면 안 되며 일반 retrieval에서도 제외되어야 합니다.

같은 selection metadata는 sync run, stream `run_completed`, run detail, run history summary,
run event에도 노출됩니다.

- `knowledge_base_selection`
- `resolved_knowledge_base_count`

## 관련 문서

- [Knowledge Base path OpenAPI handoff (English)](../en/12-knowledge-base-path-openapi-handoff.md)
- [Group upload staging flow](./18-team-upload-staging-flow.md)
- [V1 contract freeze and evidence map](./11-v1-phase-0-contract-freeze-evidence-map.md)
