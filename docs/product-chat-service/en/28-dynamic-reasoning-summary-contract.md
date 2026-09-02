# Dynamic model-authored reasoning summary contract

[한국어](../ko/28-dynamic-reasoning-summary-contract.md)

Status: **Implemented on the reasoning-summary feature branch.** Backend, persisted recovery,
typed SSE, and sibling-frontend rendering are covered by offline tests; hosted deployment and the
owner's manual E2E review remain separate rollout evidence.

## Why this is needed

The current `agent_trace` is intentionally factual and mostly static. It reports which verified
stages ran, their status, and display-safe counts. That is valuable, but it cannot explain why a
model chose a different approach for each request. Likewise, `reasoning_mode` and
`reasoning_effort` describe provider computation settings; they do not return user-visible text.

The product needs a separate, dynamic artifact: a short model-authored account of the approach it
selected for this request. For example:

- “This asks for exhaustive coverage of `SUMMARY.ko.md`, so I selected a comprehensive document
  read instead of focused retrieval.”
- “This question is limited to the definition of AxSystem, so I searched the most relevant
  passages rather than reading the whole document.”
- “I compared the retrieved policy sections, resolved the newest applicable rule, and organized
  the answer around the remaining uncertainty.”

This text may resemble a thought process, but it is not raw or faithful chain-of-thought. It is a
model-generated summary intended for display. The UI and API must not imply that it exposes the
model's private internal reasoning.

## Three separate trust channels

| Surface | Answers | Trust meaning |
| --- | --- | --- |
| `reasoning_summaries` | “What approach does the model say it took?” | Dynamic, model-authored, and potentially imperfect |
| `agent_trace` | “What did the application verify actually ran?” | Deterministic, typed execution record |
| citations / `consulted_sources` | “Which authorized evidence supported or was available to the answer?” | Provenance governed by backend attribution rules |

The reasoning summary must never replace the trace, citations, coverage disclosure, warnings, or
final answer. A mismatch between a model summary and verified trace is a product signal to preserve,
not something the serializer should conceal by rewriting history.

## Producer stages

### `retrieval_planning`

The Luna RAG Agent should return one bounded, user-displayable explanation alongside its typed
focused-versus-comprehensive tool choice. This is a strict output field, not arbitrary scratchpad
text. It may explain the scope and chosen retrieval strategy, but it must not select trusted IDs,
claim authorization, reveal system knowledge, or include document body text.

### `answer_synthesis`

The final Sol response call requests the OpenAI Responses API reasoning summary with
`reasoning.summary="auto"`. The adapter extracts only provider `summary_text` blocks and matching
reasoning-summary streaming deltas; raw reasoning content is not accepted by the public contract.

Provider reference: [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).

Both stages are nullable. Unsupported models, `reasoning_effort=none`, empty provider output,
deterministic mode, or safe filtering may produce no summary. The backend must not fabricate a
model-authored summary when none exists.

## API shape

```json
{
  "reasoning_summaries": [
    {
      "stage": "retrieval_planning",
      "text": "The request asks for complete coverage, so I selected a comprehensive read.",
      "source": "model_generated"
    },
    {
      "stage": "answer_synthesis",
      "text": "I organized the answer by architecture, execution flow, risks, and next steps.",
      "source": "provider_reasoning_summary"
    }
  ]
}
```

Closed fields:

- `stage`: `retrieval_planning | answer_synthesis`;
- `text`: nonblank display text, maximum 500 characters per item;
- `source`: `model_generated | provider_reasoning_summary`.

The completed run response, run detail, replay response, and refresh recovery must return the same
ordered list. Absence is represented by an empty list, not placeholder prose.

## SSE and persistence contract

- `reasoning_summary_delta` is SSE-only and carries `stage`, `delta`, and a per-stage `sequence`.
- `reasoning_summary_generated` is the proposed persisted, refresh-safe event containing the final
  bounded item for one stage.
- `run_completed.reasoning_summaries` is authoritative for the completed streamed response.
- `answer_delta` remains final-answer text only. Summary text must never be concatenated into the
  reply or copied into assistant-message content.
- The frontend may render summary deltas before answer deltas when the provider supplies them, but
  must not assume every model, effort, or run produces a summary.

The existing stream adapter filters control-model tokens and extracts `AIMessage.text`, which
excludes reasoning blocks. The implementation needs an explicit summary-block/event adapter rather
than weakening that filter.

## Safety and honesty boundaries

- Never request, store, stream, or expose raw reasoning text or encrypted reasoning as display text.
- Never call the field `chain_of_thought`, `internal_thoughts`, or an equivalent claim.
- Treat summaries as untrusted model output under the same conversation authorization as the final
  answer.
- Forbid system/developer prompt text, credentials, provider traces, hidden system-KB identity,
  unauthorized source metadata, and raw document passages.
- Keep citations and coverage separate; a summary is not evidence.
- Preserve the model-authored text rather than localizing it after generation. The request should
  provide the intended display locale or language context.
- Bound item count and length before persistence and serialization.
- Summary generation consumes provider output budget and must enter the future platform usage
  ledger; it is not free merely because it is UI metadata.
- The UI renders this as quiet quoted prose beneath the verified steps, without another visible
  label or explanatory disclaimer. Placement and neutral styling distinguish it from
  `agent_trace`; it still never inherits verified status treatment.

## Implemented sequence and evidence

1. Mocked provider compatibility tests prove that `reasoning.summary="auto"` reaches the
   Responses request and that summary blocks cannot leak into `reply`.
2. Run one bounded credentialed provider spike to record the current final and streaming block/event
   shapes without storing sensitive prompts or output in the repository.
3. Add closed Pydantic contracts for summary items and the two proposed event payloads.
4. Add Luna's bounded retrieval-planning display field without changing tool authorization.
5. Extract Sol provider summaries separately from final answer text.
6. Persist completed items, rebuild them on refresh/replay, and stream typed deltas.
7. Add redaction, prompt-injection, system-knowledge, authorization, length, ordering, nullable, and
   deterministic-mode tests.
8. A live local OpenAPI document was served at `http://127.0.0.1:8017/openapi.json` before the
   sibling frontend models were updated.

## Definition of done

- Summary text differs meaningfully across representative focused, comprehensive, web/general, and
  uncertainty-heavy requests.
- The original answer text is byte-for-byte free of reasoning-summary blocks.
- Refresh and replay preserve the same completed summaries.
- Unsupported/empty/filtered summaries degrade to an empty list without failing the answer.
- Raw chain-of-thought, prompts, provider traces, hidden provenance, credentials, and document body
  text cannot cross the typed boundary.
- `agent_trace` remains the verified record and is visibly distinct from model-authored summaries.
- Offline tests require no OpenAI key; one credentialed smoke records only safe pass/fail evidence.
