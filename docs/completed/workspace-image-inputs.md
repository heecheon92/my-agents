# Static workspace image inputs

- Status: Shipped implementation on `develop`; the owner reported successful manual browser testing.
- Completed: 2026-09-30.
- Canonical status: [implementation tracking](../implementation-tracking.md#shipped-and-completed-index).
- Current contract: [image attachments](../product-chat-service/en/25-openai-document-workspace.md#image-attachments).
- Learning/debug notes: [workspace image inputs](../learning/project-notes/workspace-image-inputs.md).

## Delivered scope

Temporary workspace attachments accept static JPEG (`.jpg`, `.jpeg`), PNG (`.png`), WebP
(`.webp`), and GIF (`.gif`). The registry serves category `image`, extension-specific MIME,
analysis support, and unavailable image-output certification. Original bytes remain expiring
`user_data` uploads and are mounted in the existing network-disabled container.

Before transfer, existing account/guest, ownership, consent, extension/MIME, and byte controls
apply. Pillow already present through the Docling/office dependencies checks actual encoding,
decodability, animation and decompression-bomb protections. Malformed, mismatched, animated
GIF/APNG/WebP and unsafe images fail with 415 `unsupported_attachment_type`. Generic binary MIME
is canonicalized only after validation. The owned validation buffer cannot close the original
upload stream, which is rewound before upload.

The SDK adapter receives image file IDs separately and sends `input_image` with `detail=high`;
other attachments retain `input_file`, including in mixed turns. Provider preprocessing avoids
original-resolution auto-mode patch-limit failures for ordinary large photos. Consent, ownership,
file-count/combined-byte limits, expiry and the separate workspace model are preserved.

Frontend controls use the served registry, show static-image/analysis-only information, and
classify permanent refusals without suggesting a retry. Image staging requires a supported
extension; unsupported/extensionless names cannot enter through MIME fallback. Existing
non-image MIME fallback is retained. No package addition, DB migration, image-generation feature,
or PNG/JPEG download certification was included.

## Acceptance evidence

- Backend focused image/workspace checks: **30 passed**. Full offline suite: **765 passed,
  13 gated skips, 12 dependency/API deprecation warnings**; Ruff lint/format and diff checks passed.
- Tests cover real bytes in all five extensions, exact MIME registry, generic MIME normalization,
  original-byte preservation, malformed/mismatched/animated/pixel/byte limits, consent, guest and
  foreign-user isolation, mixed vision/document routing, SDK expiry, and output boundaries.
- Claude completed the separate frontend work: lint/typecheck/build and **399 unit tests** passed.
  Workspace browser checks: **18 passed**, one file-drop failure also reproduced on clean develop.
- Served OpenAPI/Zod comparison and real production-BFF pre-provider checks passed on isolated
  local auth/in-memory services: registry, missing/false consent, malformed/mismatched/animated
  refusals, empty stored attachment list after refusal, guest eligibility and 403 upload rejection.
- The owner reported that manual browser testing works. The tested formats, runtime model and
  response mode were not specified, so this is not a full format/model/provider certification.
- Session-owned verification servers were stopped; no production configuration was changed.

## Limits

Agent verification did not perform valid cloud uploads or live OpenAI image execution.
Animation, HEIC/AVIF/BMP/SVG inputs, image output editing/downloads and raw-image KB indexing are
outside the delivered scope. Local validation decodes bounded images and uses existing Pillow
protections; it is not a dedicated image-processing sandbox. The baseline frontend file-drop
failure and existing warnings remain separate follow-up work.
