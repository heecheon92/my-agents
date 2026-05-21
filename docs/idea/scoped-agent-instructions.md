# Scoped agent instructions idea

This note captures the product idea behind adding `AGENTS.md`-style instructions to the service itself.

## Idea

Add durable Markdown instructions that can be configured per user, group/workspace, and eventually conversation. These instructions should shape how the assistant and future agents interact with the user, similar to how repo-local `AGENTS.md` shapes coding-agent behavior.

## Names to consider

User-facing names:

- Instructions
- Workspace Instructions
- Agent Instructions

Backend/domain names:

- Instruction Profile
- Scoped Instruction Profile
- Agent Operating Policy
- Context Policy

Recommended naming:

- User-facing: **Instructions** or **Workspace Instructions**
- Backend/domain: **Instruction Profile**

## Conceptual precedence

Hard app and safety rules always stay above product-configurable instructions. Within configurable instructions, group/workspace guidance should override personal guidance when they conflict.

```text
Application / system safety rules
↓
Service-level developer policy
↓
Group / workspace instructions
↓
Personal user instructions
↓
Conversation-specific instructions
↓
Current user message
```

Runtime assembly can concatenate lower-priority instructions first and higher-priority instructions later, while labeling each section clearly:

```md
# Personal Instructions
Prefer concise Korean answers.

# Group Instructions
These override personal instructions when they conflict.
All legal/tax answers must include checklist format and cite uploaded documents when available.
```

In that example, the final behavior should preserve the personal style preference when possible, but the group-specific legal/tax response contract wins when applicable.

## Model sketch

```text
instruction_profiles
- id
- scope_type: user | group | conversation
- scope_id
- title
- body_markdown
- priority
- enabled
- created_by
- updated_by
- created_at
- updated_at
```

## Runtime assembler

```text
InstructionAssembler
1. Load app/base instruction.
2. Load user's personal instruction.
3. Load active group/workspace instruction.
4. Load conversation instruction, if present.
5. Validate, bound length, and sanitize for unsafe claims.
6. Produce explicit ordered instruction stack for conversation runs and future retrieval-agent calls.
```

## Non-negotiable safety rule

Instructions can guide tone, format, domain assumptions, and workflow, but they cannot:

- grant access to documents or tools;
- bypass group/document permissions;
- reveal hidden prompts or system policies;
- override app safety/security rules;
- disable audit or observability requirements.

## Why this fits the product

The backend already has users, groups, permissions, retrieval, conversations, and agent activity events. Scoped instruction profiles would let each user or group define a durable operating style while keeping the security boundary app-owned. This is especially useful once the retrieval-agent track exists, because retrieval planning, context packing, answer format, and citation expectations can all inherit the same scoped instruction stack.
