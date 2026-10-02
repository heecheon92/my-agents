---
created: 2026-09-30
updated: 2026-09-30
status: active
topics: [image-inputs, openai, document-workspace, validation]
related_code:
  - my_agents/document_workspace/formats.py
  - my_agents/document_workspace/images.py
  - my_agents/document_workspace/provider.py
  - my_agents/document_workspace/service.py
  - tests/test_document_workspace_images.py
---

# Workspace 이미지 입력

## 문제와 원인

GPT가 이미지를 이해할 수 있어도 application의 파일 allowlist와 실제 request type이 맞아야 한다.
기존 workspace는 문서 확장자만 받았고 모든 attachment를 `input_file`로 보냈다.
이미지 확장자만 추가하거나 PNG를 문서로 이름 변경하는 방법은 visual input을 만들지 못한다.

## 적용한 경계

1. Registry가 정지 JPEG/PNG/WebP/GIF와 확장자별 MIME를 공개한다.
2. 인증/소유권/consent/용량을 확인한 뒤 실제 image encoding, decode와 animation을 검증한다.
3. 원본은 `user_data` file로 명시적 expiry와 함께 upload하며 Product DB는 metadata만 저장한다.
4. 실행 시 image file ID는 `input_image`/`detail=high`, 문서는 `input_file`로 전달한다.
5. Image도 원본 container에 mount하되 답변은 별도 workspace 모델을 사용한다.

Image decode는 기존 dependency tree의 Pillow를 사용한다. 원본 upload stream을 닫지 않도록
owned buffer에서 처리하고 종료 후 stream을 처음으로 되돌린다. 원본 bytes는 변경하지 않는다.
Pillow의 decompression-bomb warning/error를 거부하며 GIF/APNG/WebP의 animation도 허용하지 않는다.
High detail은 provider 전처리를 제한해 큰 사진의 original-resolution patch-limit 문제를 줄인다.

## 폐기한 지름길과 확인한 오류

- 모든 파일을 `input_image`로 바꾸면 문서 처리 계약이 깨진다. 혼합 입력은 category로 구분한다.
- 확장자만 믿으면 PNG bytes가 JPEG 이름으로 통과할 수 있다. 실제 codec과 MIME도 확인한다.
- animated GIF의 첫 frame만 조용히 보내면 사용자가 motion을 분석했다고 오해한다. 업로드 전에 거부한다.
- 이미지 입력을 추가해도 다운로드 PNG/JPEG 출력 인증은 별개다. 이번 작업은 analysis input만 추가한다.
- Consent field가 없는 request는 기존 Form validation으로 422, false는 service에서 400이다.
  처음 만든 테스트가 이를 혼동해 기대값을 수정했고 runtime 계약은 바꾸지 않았다.
- Frontend의 MIME fallback이 확장자 없는 image를 받으면 backend는 거부한다. Image는 registry의
  지원 확장자가 있어야 consent 전 staging을 통과하도록 맞췄으며 기존 non-image fallback은 유지했다.

## 검증과 남은 한계

Backend 전체 offline suite 765 passed / 13 skipped, Ruff lint/format 통과.
실제 image bytes, generic MIME canonicalization, 손상/위장/animation/pixel/byte 제한,
consent/guest/다른 사용자 접근, 혼합 vision/file request와 expiry를 검증했다.
SDK boundary는 mock이므로 실제 OpenAI image access, 품질과 latency는 확인하지 않았다.
Frontend의 OpenAPI/Zod, 실제 BFF pre-provider refusal 및 guest 검증도 통과했다.
파일-drop browser baseline 실패는 이번 범위에서 고치지 않았다.

현재 계약: [Image attachments](../../product-chat-service/25-openai-document-workspace.md#image-attachments).

## Revision history

- 2026-09-30: Image/file 입력 구분, static validation, consent 상태 및 staging 일치 검증 기록.
