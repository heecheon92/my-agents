---
created: 2026-05-15
updated: 2026-05-15
status: active
topics:
  - python
  - typing
  - function-signatures
  - dependency-injection
related_code:
  - my_agents/agents/general_assistant/classifier.py
  - my_agents/agents/general_assistant/responders.py
---

# Python syntax catch-up: `*`, `Iterable`, and `**`

This note captures three Python syntax patterns that appear in the current assistant backend and are easy to miss when reading production-shaped code.

## Where these examples appear

| Syntax | Current code example | Main idea |
| --- | --- | --- |
| Standalone `*` in parameters | `_extract_message_content(response, *, debug_empty_response=False)` | Forces later arguments to be keyword-only. |
| `Iterable[BaseMessage]` | `classify_messages(messages: Iterable[BaseMessage])` | Accepts any loopable collection, not just a `list`. |
| `**` in a call | `ChatOpenAI(**_build_chat_model_args(settings))` | Expands a dictionary into keyword arguments. |

## 1. Standalone `*` means keyword-only after this point

```python
def _extract_message_content(
    response: Any,
    *,
    debug_empty_response: bool = False,
) -> str:
    ...
```

The `*` by itself is not a parameter. It is a boundary marker.

Everything after it must be passed by name:

```python
_extract_message_content(response, debug_empty_response=True)
```

This is intentionally invalid:

```python
_extract_message_content(response, True)
```

Why this is useful:

- `True` by itself is hard to read at a call site.
- `debug_empty_response=True` explains the reason for the flag.
- It protects future readers from mixing up optional boolean/configuration arguments.

A simple mental model:

```python
def send_email(to: str, *, urgent: bool = False) -> None:
    ...

send_email("me@example.com", urgent=True)  # clear
send_email("me@example.com", True)         # rejected by Python
```

## 2. `Iterable` means “anything I can loop over”

```python
def classify_messages(messages: Iterable[BaseMessage]) -> RouteDecision:
    message_list = list(messages)
    latest_user_message = _latest_human_text(message_list)
    return classify_message(latest_user_message, message_list[:-1])
```

`Iterable[BaseMessage]` says the function accepts any object that can produce `BaseMessage` items in a `for` loop or through `list(...)`.

Valid examples include:

```python
classify_messages([msg1, msg2])      # list
classify_messages((msg1, msg2))      # tuple
classify_messages(message_generator) # generator
```

The annotation would be narrower if it said this instead:

```python
def classify_messages(messages: list[BaseMessage]) -> RouteDecision:
    ...
```

Use `Iterable[...]` when the function only needs to consume items. Use `list[...]` when it needs list-specific behavior such as indexing, mutation, or repeated access.

Important detail in the current code: `classify_messages` immediately converts the iterable to a list because it needs to reverse/search and slice the messages afterward. The public input type is still flexible, while the inside of the function gets the list behavior it needs.

## 3. `**` expands a dictionary into keyword arguments

```python
self._chat_model = chat_model or ChatOpenAI(**_build_chat_model_args(settings))
```

If `_build_chat_model_args(settings)` returns this dictionary:

```python
{
    "model": "gpt-5.5",
    "timeout": 30,
    "max_completion_tokens": 1200,
}
```

then this call:

```python
ChatOpenAI(**_build_chat_model_args(settings))
```

behaves like this:

```python
ChatOpenAI(
    model="gpt-5.5",
    timeout=30,
    max_completion_tokens=1200,
)
```

This is useful when configuration is assembled in one function but passed into another class or function as named options.

The full line also uses `or` as a fallback:

```python
self._chat_model = chat_model or ChatOpenAI(**_build_chat_model_args(settings))
```

Meaning:

```python
if chat_model:
    self._chat_model = chat_model
else:
    self._chat_model = ChatOpenAI(**_build_chat_model_args(settings))
```

In this project, that lets tests inject a fake chat model without requiring a real OpenAI call.

## Quick decision guide

```mermaid
flowchart TD
    Start["Reading or writing a function"] --> Flag{"Is this an optional flag/config after required args?"}
    Flag -->|Yes| KeywordOnly["Use `*` before the flag so calls must name it"]
    Flag -->|No| Collection{"Does the function only need to loop over input items?"}
    Collection -->|Yes| Iterable["Accept `Iterable[T]` for flexibility"]
    Collection -->|No| List["Use `list[T]` or another concrete type when concrete behavior is required"]
    Start --> Config{"Do you already have options in a dict?"}
    Config -->|Yes| Kwargs["Use `func(**options)` to pass them as keyword args"]
```

## Practice exercises

1. Find another optional boolean argument in the codebase. Would keyword-only syntax make the call site clearer?
2. Look for a function that accepts a list. Does it mutate/index the list, or could it accept an `Iterable`?
3. Print `_build_chat_model_args(settings)` in a safe test context and compare the dictionary keys with the `ChatOpenAI(...)` options.

## Revision history

- 2026-05-15: Created learning log for `Python syntax catch-up: *, Iterable, and **`.
