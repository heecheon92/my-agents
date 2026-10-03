# 제품 채팅 서비스 문서

[English original](./README.md) | 한국어

제품 채팅 서비스 관련 backend 문서의 읽기 순서와 주요 주제를 안내합니다.

## 읽기 순서

1. [Service foundation scaffold](./01-service-foundation-scaffold.ko.md)
2. [First-party auth and owned sessions](./02-first-party-auth-sessions.ko.md)
3. [Groups and document permissions](./03-group-document-permissions.ko.md)
4. [Server-owned conversations and chat runs](./04-server-owned-conversations.ko.md)
5. [Knowledge ingestion and deterministic extraction](./05-knowledge-ingestion-extraction.ko.md)
6. [Permission-aware RAG and citation-backed answers](./06-permission-aware-rag.ko.md)
7. [Agent observability events and eval fixtures](./07-agent-observability-evals.ko.md)
8. [Postgres, Alembic, and Neon readiness](./08-postgres-alembic-neon.ko.md)
9. [HTTP streaming and frontend contract](./09-http-streaming-frontend-contract.ko.md)
10. [Frontend demo and local runbook](./10-frontend-demo-runbook.ko.md)
11. [V1 contract freeze and evidence map](./11-v1-phase-0-contract-freeze-evidence-map.ko.md)
12. [Knowledge-base path OpenAPI handoff](./12-knowledge-base-path-openapi-handoff.ko.md)
13. [Public demo deployment readiness runbook](./12-public-demo-deployment-readiness.ko.md)
14. [Retrieval-agent hybrid reference](./12-retrieval-agent-hybrid-reference.ko.md)
15. [Generic container deployment path](./13-generic-container-deployment-path.ko.md)
16. [Render migration and rollback notes](./14-render-migration-and-rollback-notes.md)
17. [Deployment troubleshooting log](./15-deployment-troubleshooting-log.md)
18. [Group upload staging flow](./18-team-upload-staging-flow.ko.md)
19. [LangGraph-native memory migration](./19-langgraph-native-memory-migration.ko.md)
20. [Nickname signup and member roster contract](./20-nickname-signup-member-roster-contract.ko.md)
21. [System knowledge base와 user type 계약](./21-system-knowledge-base-user-type.ko.md)
22. [Economic access, usage metering, credits architecture (영어 원문)](./23-economic-access-metering-and-credits-architecture.md)
23. [지식 관리 lifecycle와 publish copy 계약](./24-knowledge-lifecycle-and-publish-copy-contract.ko.md)
24. [OpenAI hosted document workspace](./25-openai-document-workspace.ko.md)
25. [Run reasoning 설정 계약](./26-run-reasoning-preferences.ko.md)
26. [Agent와 frontend 사이의 interaction 계약](./27-agent-frontend-interaction-contract.ko.md)
27. [동적 model-authored reasoning summary 계약](./28-dynamic-reasoning-summary-contract.ko.md)
28. [임시 conversation file frontend rollout](./29-frontend-document-workspace-rollout.ko.md)
29. [Rich response rendering과 future agent-UI boundary](./30-rich-response-rendering-and-agent-ui-boundaries.ko.md)
30. [런타임 설정과 프론트엔드 연동 참고](./34-runtime-and-integration-reference.ko.md)

## 관련 maintenance ledger

- [Performance optimization records](../performance/README.md)

## 문서 상태

- 영어 원문은 `docs/product-chat-service/README.md`에 있습니다.
- 한국어 문서는 영어 원문과 같은 위치를 유지하면서
  핵심 운영 흐름과 링크를 빠르게 찾도록 돕습니다.
- 상세 번역을 확장할 때는 영어 원문과 계약 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/README.md](./README.md)
- KB-first handoff: [Knowledge-base path OpenAPI handoff](./12-knowledge-base-path-openapi-handoff.ko.md)
- 그룹 문서 승인 업로드 흐름: [Group upload staging flow](./18-team-upload-staging-flow.ko.md)
- System knowledge 계약: [System knowledge base와 user type 계약](./21-system-knowledge-base-user-type.ko.md)
- Economic access/metering 설계: [영어 원문](./23-economic-access-metering-and-credits-architecture.md)
- 지식 관리 lifecycle/publish copy 계약: [지식 관리 lifecycle와 publish copy 계약](./24-knowledge-lifecycle-and-publish-copy-contract.ko.md)

## 배포 / 마이그레이션 참고

- Render 관련 의사결정과 다른 호스팅으로 이동할 때의
  rollback 절차는 영어 원문 문서
  [Render migration and rollback notes](./14-render-migration-and-rollback-notes.md)를
  기준으로 유지합니다.
- 배포 중 실제로 겪은 문제와 해결 기록은 영어 원문 문서
  [Deployment troubleshooting log](./15-deployment-troubleshooting-log.md)에
  기록합니다.

- [35. Assistant model preferences](./35-assistant-model-preferences.ko.md)
- [37. Jev 검색 근거 재순위](./37-jev-evidence-reranking.ko.md)
- [38. 메시지 전송부터 답변 수신까지의 서비스 흐름](./38-message-to-answer-service-flow.ko.md)

- [만료 게스트 계정 정리](./39-expired-guest-cleanup.ko.md): 유예 기간, 삭제 범위, audit 보존, 미리보기 명령.

- [게스트 체험 자격과 남용 방지](./40-guest-trial-abuse-protection.ko.md): 공유 제한, 기존 계정 처리, 운영자 초기화.
