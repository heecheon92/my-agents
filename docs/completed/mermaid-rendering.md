# Mermaid rendering in assistant answers

- Status: Shipped to frontend develop/main; feature-level production verification remains limited.
- Recorded: 2026-09-05.
- Release evidence: frontend `9c8e365`; owner-reported manual testing during implementation.
- Current contract: [rich response rendering](../product-chat-service/en/30-rich-response-rendering-and-agent-ui-boundaries.md).
- Canonical status: [implementation tracking](../implementation-tracking.md#shipped-and-completed-index).

## Delivered scope and decisions

The sibling frontend implements Mermaid dispatch at the Markdown code-block boundary,
lazy library loading, strict rendering configuration, bounded source/output, SVG validation,
an image presentation boundary, source/error fallback, and theme-aware rendering. Persisted
Markdown remains the transcript/copy source; SVG is derived UI state. Neither AG-UI nor A2UI
was introduced. The general assistant backend contract did not need a new UI protocol.

Source: frontend `components/ui/mermaid-engine.ts`, `mermaid-diagram.tsx`, `mermaid-policy.ts`,
plus Mermaid dispatch/policy and browser tests. The historical implementation plan remains
in the linked bilingual contract; it is not a request to reimplement the feature.

## Acceptance evidence

Historical implementation checks: lint, typecheck, production build, 365 unit/component tests,
and three focused browser scenarios passed; the broader browser run had 190 passes, two skips,
and a file-drop failure that subsequently passed twice in isolation. The owner reported manual
testing completed. Release preflight later recorded 371 unit tests, 193 browser passes,
two gated skips, and one known unreliable conversation-list focus assertion.

Frontend 9c8e365 was deployed and its public routes verified serving on 2026-09-05.
These are recorded results, not a fresh frontend test run during documentation cleanup.

## Remaining verification and future work

Production authenticated Mermaid use, screen-reader testing, and adversarial CPU-stress
testing were not established by the release smoke. The UI timeout is not a proof that
synchronous library layout work can be preempted. AG-UI transport and A2UI catalog adapters
remain future decisions, not unfinished implementation tasks inside this shipped renderer slice.
