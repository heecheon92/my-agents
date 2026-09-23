# User-configurable assistant behavior

This is a design proposal, not an implemented API or user setting. Canonical priority and
implementation status live in [implementation tracking](../implementation-tracking.md).
The current change only makes ordinary assistant prose warmer and more explanatory and sets
the server's OpenAI verbosity default to `medium`.

## User outcome

Let users choose how the assistant communicates without repeatedly writing style instructions.
A beginner can ask for approachable explanations and examples; another user can prefer direct,
compact answers. A request such as “one sentence please” should still override saved style.
These preferences affect final answer presentation, not the accuracy requirements or tools used.

## Small, bounded controls

Start with the first two controls. Add the others only after representative evaluation.

| Preference | Proposed values | Default | Backend mechanism |
| --- | --- | --- | --- |
| Friendliness | `neutral`, `warm` | `warm` | Short, code-owned system-prompt guidance; no excessive praise or forced familiarity |
| Verbosity | `low`, `medium`, `high` | `medium` | OpenAI text verbosity plus matching prompt guidance; no exact word-count promise |
| Explanation level | `beginner`, `general`, `technical` | `general` | Vocabulary, definitions, and explanation depth |
| Response format | `auto`, `paragraphs`, `structured` | `auto` | Prefer prose or lists/headings when suitable; preserve code and tables when needed |
| Examples | `auto`, `on_request` | `auto` | Include a short concrete example when useful, or only when requested |

Do not expose temperature, arbitrary system-prompt editing, token budgets, or model/provider
selection as part of this first feature. Reasoning effort/mode remain the separate existing
contract. More verbosity does not request higher reasoning effort or comprehensive retrieval.

## Resolution and storage proposal

Use one typed, versioned preference contract and allowlisted values. Omitted fields inherit;
provide an explicit reset operation rather than treating every missing field as a reset.

1. Keep authorization, groundedness, privacy, and tool policies mandatory for every style.
2. Resolve structured style values: run override, then conversation override if implemented,
   then saved user preference, then server defaults.
3. Within those boundaries, honor the user's explicit style request in the current message.
   Do not use another classifier call just to interpret style or automatically save inferred preferences.
4. Render a short prompt section once and map the resolved verbosity to the provider option.
5. Store the effective typed values and schema version with the run so replay and debugging can
   explain the behavior. Never store credentials or the rendered system prompt in the preference record.

Registered users should have Product DB-backed preferences so settings follow them across devices.
Guests can use bounded per-run preferences; do not create durable guest profiles for this feature.
For replay, inherit the original run's effective preferences unless explicitly overridden.
Older runs without this metadata use documented defaults. A later default change must not silently
rewrite historical effective preferences.

No routes, schemas, database columns, frontend controls, or persistence are implemented by this
proposal. When scheduled, update the API contract first and coordinate controls in the separate
frontend repository. The backend repository remains frontend-free.

## Execution boundaries

Apply style to final prose only. Jev source selection, retrieval-method choice, ContextForge intent,
permissions, citation attribution, HITL choices, and source coverage must not change with friendliness
or verbosity. In particular, “more verbose” must not imply reading an entire document or adding
unsupported details. Keep factual uncertainty and partial-coverage disclosures at every setting.

Ordinary chat currently uses `MY_AGENTS_OPENAI_VERBOSITY`; document workspace has a separate
provider adapter. A future implementation must explicitly wire both final-answer surfaces before
claiming complete support. Typed activity events and operational traces should remain factual.

Output limits remain server-controlled and include reasoning tokens. Higher verbosity can increase
cost and latency; it does not guarantee the answer fits the existing output budget. Evaluate budget
changes separately rather than silently lifting limits whenever a user chooses `high`.

## Acceptance and evaluation

- Validate all enums, reject unknown preference keys, isolate settings by authenticated user,
  and test defaults, overrides, resets, guest behavior, persistence, and replay.
- Verify effective prompt and provider payload agree and no older “always be concise” instruction
  contradicts the resolved style.
- Compare the same Korean/English factual, document-grounded, learning, and complex explanation
  prompts under low/medium/high verbosity and neutral/warm tone.
- Assess directness, appropriate depth, readability, friendliness, citation fidelity, and absence
  of filler or unsupported claims; avoid brittle tests that require exact generated wording.
- Measure output/reasoning tokens, latency, truncation, and cost. Confirm style settings leave
  deterministic authorization and retrieval policies unchanged.

## Current implementation references

- [Response prompt and model configuration](../../my_agents/agents/general_assistant/responders.py)
- [Server settings](../../my_agents/settings.py)
- [Existing reasoning preference contract](../product-chat-service/en/26-run-reasoning-preferences.md)
- [Semantic interaction contract](../product-chat-service/en/27-agent-frontend-interaction-contract.md)
