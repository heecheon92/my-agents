# Generic container deployment path

[English original](../en/13-generic-container-deployment-path.md) | 한국어

## 요약

이 문서는 `product-chat-service/13-generic-container-deployment-path.md`의 한국어 문서 트랙 항목입니다. 현재는 핵심 목적과 영어 원문 위치를 안내하는 요약본입니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/13-generic-container-deployment-path.md`에 있습니다.
- 이 한국어 파일은 같은 주제의 위치를 고정하기 위한 문서입니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- [Render pre-deploy 명령과 배포 검증](../en/14-render-migration-and-rollback-notes.md#production-pre-deploy-guardrail):
  현재 확인된 명령은 `uv run --no-sync alembic upgrade head`입니다. LangGraph setup/status와
  memory reconciliation을 연결하는 강화안은 문서화된 제안이며, Render 설정에 적용한 것은
  아닙니다. Connection 종료 회귀 테스트는 배포 전에 별도 임시 DB에서 실행하고, 운영
  pre-deploy에서는 실행하지 않습니다. 배포 전 검증이 운영 중의 연결 단절을 막지는 못하므로
  runtime checkout check와 실제 인증 chat 확인도 필요합니다.
- 영어 원문: [product-chat-service/13-generic-container-deployment-path.md](../en/13-generic-container-deployment-path.md)
