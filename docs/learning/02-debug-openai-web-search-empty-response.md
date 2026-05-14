---
created: 2026-05-14
updated: 2026-05-14
status: active
topics:
  - debugging
  - openai-responses-api
  - web-search
  - cli
  - deterministic-fallback
related_code:
  - my_agents/agents/general_assistant/responders.py
  - my_agents/agents/general_assistant/graph.py
  - my_agents/cli.py
  - tests/test_responders.py
  - tests/test_cli.py
---

# Debug note: OpenAI web search returned no final text

## Summary

While testing the terminal CLI with OpenAI hosted `web_search`, the graph did not crash after the latest fallback work, but the assistant still failed to produce a useful final answer.

User prompt example:

```text
hey, can you google about latest meeting with Trump and Xi?
```

The assistant returned a deterministic failure message instead of an answer because the OpenAI Responses API response contained tool and reasoning items but no final text message.

## What failed

The raw CLI debug response showed:

```json
{
  "response_metadata": {
    "status": "incomplete",
    "incomplete_details": {
      "reason": "max_output_tokens"
    }
  },
  "usage_metadata": {
    "output_tokens": 300,
    "output_token_details": {
      "reasoning": 300
    }
  }
}
```

The important signal is:

```text
status=incomplete
reason=max_output_tokens
output_tokens=300
reasoning=300
```

This means the model used the full configured output token budget while reasoning and running web search. It did not have enough remaining output budget to produce final answer text.

## Why `ToolNode` was not the fix

This was not a missing LangGraph `ToolNode` issue.

```mermaid
flowchart TD
    User["User asks latest/current question"] --> Graph["LangGraph response node"]
    Graph --> Provider["OpenAIResponseProvider"]
    Provider --> HostedTool["OpenAI hosted web_search"]
    HostedTool --> OpenAI["OpenAI Responses API"]
    OpenAI --> Incomplete["Incomplete response: max_output_tokens"]
    Incomplete --> Fallback["Deterministic failure explanation"]
```

`ToolNode` is needed for local/client-side tools that LangGraph must execute. OpenAI hosted tools such as `web_search` run server-side inside the OpenAI Responses API request. Therefore `responders.py` is still the correct first location for hosted-tool binding.

## Fix applied

We made the failure path deterministic and user-facing instead of letting the CLI/API crash or return a vague extraction error.

The responder now:

1. tries to extract meaningful text from the OpenAI/LangChain response;
2. detects empty text responses;
3. inspects `response_metadata.status` and `response_metadata.incomplete_details`;
4. explains the concrete reason when the response ended because of `max_output_tokens`;
5. in CLI debug mode, appends the raw response object for inspection.

The specific user-facing failure now explains that:

- the OpenAI response ended before producing final text;
- the reason was `max_output_tokens`;
- web search can consume output tokens for reasoning/search before answer generation;
- the user can increase `MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS` or ask a narrower question.

## Why this matters

For an assistant, a failed model call is still part of the user experience. The agent should not only fail safely; it should explain what happened and what the user or developer can do next.

This establishes a durable behavior pattern:

```text
model returns useful text
  -> return useful text

model returns no useful text but has known failure metadata
  -> return deterministic explanation with concrete reason

CLI debug mode is enabled
  -> include deterministic explanation plus raw response object
```

## Tests added

Tests now cover:

- route-aware web search binding;
- `general_assistant` web search only when the latest user message indicates current/recent/web/source-backed need;
- extraction from Responses API-style content blocks;
- fallback for tool-call-only responses;
- explicit explanation for `status=incomplete` and `reason=max_output_tokens`;
- CLI debug mode including raw response object.

## Confirmed fix

After reproducing the empty-answer path, increasing the OpenAI output token cap fixed the live web-search response. The project default and `.env.example` now use:

```text
MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS=1200
```

This is intentionally higher than the original `300` token cap because hosted web search can spend output tokens on reasoning/search before final answer generation.

## Follow-up risk

A higher output cap can increase cost and latency for OpenAI-backed requests. If that becomes a problem, make the cap route-aware instead of dropping the global default back to a value that breaks web-search answers.

## Revision history

- 2026-05-14: Created debug note after OpenAI hosted web search returned an incomplete response with `max_output_tokens` before final answer text.
- 2026-05-14: Confirmed that raising `MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS` fixed the live web-search response and updated the project default to `1200`.
