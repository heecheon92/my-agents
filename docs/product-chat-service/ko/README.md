# 제품 채팅 서비스 문서

[English original](../en/README.md) | 한국어

제품 채팅 서비스 관련 backend 문서의 읽기 순서와 주요 주제를 안내합니다.

## 읽기 순서

1. [Service foundation scaffold](./01-service-foundation-scaffold.md)
2. [First-party auth and owned sessions](./02-first-party-auth-sessions.md)
3. [Groups and document permissions](./03-group-document-permissions.md)
4. [Server-owned conversations and chat runs](./04-server-owned-conversations.md)
5. [Knowledge ingestion and deterministic extraction](./05-knowledge-ingestion-extraction.md)
6. [Permission-aware RAG and citation-backed answers](./06-permission-aware-rag.md)
7. [Agent observability events and eval fixtures](./07-agent-observability-evals.md)
8. [Postgres, Alembic, and Neon readiness](./08-postgres-alembic-neon.md)
9. [HTTP streaming and frontend contract](./09-http-streaming-frontend-contract.md)
10. [Frontend demo and local runbook](./10-frontend-demo-runbook.md)
11. [V1 contract freeze and evidence map](./11-v1-phase-0-contract-freeze-evidence-map.md)
12. [Knowledge-base path OpenAPI handoff](./12-knowledge-base-path-openapi-handoff.md)
13. [Public demo deployment readiness runbook](./12-public-demo-deployment-readiness.md)
14. [Retrieval-agent hybrid reference](./12-retrieval-agent-hybrid-reference.md)
15. [Generic container deployment path](./13-generic-container-deployment-path.md)
16. [Render migration and rollback notes](./14-render-migration-and-rollback-notes.md)
17. [Deployment troubleshooting log](./15-deployment-troubleshooting-log.md)
18. [Group upload staging flow](./18-team-upload-staging-flow.md)
19. [LangGraph-native memory migration](./19-langgraph-native-memory-migration.md)
20. [Nickname signup and member roster contract](./20-nickname-signup-member-roster-contract.md)
21. [System knowledge base와 user type 계약](./21-system-knowledge-base-user-type.md)

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/README.md`에 있습니다.
- 한국어 문서는 영어 원문과 같은 위치를 유지하면서
  핵심 운영 흐름과 링크를 빠르게 찾도록 돕습니다.
- 상세 번역을 확장할 때는 영어 원문과 계약 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/README.md](../en/README.md)
- KB-first handoff: [Knowledge-base path OpenAPI handoff](./12-knowledge-base-path-openapi-handoff.md)
- 그룹 문서 승인 업로드 흐름: [Group upload staging flow](./18-team-upload-staging-flow.md)
- System knowledge 계약: [System knowledge base와 user type 계약](./21-system-knowledge-base-user-type.md)

## 배포 / 마이그레이션 참고

- Render 관련 의사결정과 다른 호스팅으로 이동할 때의
  rollback 절차는 영어 원문 문서
  [Render migration and rollback notes](../en/14-render-migration-and-rollback-notes.md)를
  기준으로 유지합니다.
- 배포 중 실제로 겪은 문제와 해결 기록은 영어 원문 문서
  [Deployment troubleshooting log](../en/15-deployment-troubleshooting-log.md)에
  기록합니다.
