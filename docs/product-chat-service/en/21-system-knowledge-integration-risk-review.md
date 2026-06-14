# System knowledge integration risk review

This checklist supports the system knowledge base + `user_type` rollout. It is a
handoff artifact for the backend, frontend, and verification lanes; it does not create new
product scope beyond the consensus plan.

## Contract boundaries to preserve

- **System knowledge is public authenticated retrieval context.** It may answer
  project-fact questions for registered users and guests, but it is not user memory.
- **Management visibility is separate from chat retrieval.** Normal users and guests can
  receive system context in chat, but must not enumerate system KB names or IDs through
  source-management surfaces.
- **`user_type` mutation is operator-script-only.** Public API requests, profile updates,
  and frontend forms must not accept or persist `user_type`.
- **`root` and `system` are equivalent in v1.** Frontend policy should prefer the derived
  `can_manage_system_knowledge` capability over hardcoding raw enum checks.
- **Personal and group boundaries remain unchanged.** The feature must not weaken owner
  scoping, accepted-member group scoping, published-personal-KB behavior, document
  permissions, or guest limits.

## Integration risk checklist

Use this before final integration sign-off:

1. **Public metadata non-enumeration**
   - Normal and guest `/knowledge-bases` responses exclude system rows.
   - Normal and guest direct guesses for system KB/document IDs use the same concealed
     unauthorized style as adjacent existing routes.
   - Conversation/run public metadata does not expose ambient system KB names or IDs unless
     the contract deliberately adds a safe field.
2. **Direct document-route scope awareness**
   - Nested system document routes are root/system-only.
   - Direct `/documents/{id}` read/edit/delete/ingest behavior recognizes parent KB scope
     and does not treat system documents as ordinary owner-personal documents.
   - Global document lists remain personal/group oriented for normal and guest users.
3. **Guest promotion refusal**
   - The operator script refuses guest account promotion to `root` or `system` by default.
   - Guest `/auth/me` remains non-privileged while still allowing ambient system retrieval
     in chat.
4. **Personal/group regression preservation**
   - Personal KB owner-only create/list/document-write behavior still passes.
   - Group KB visibility and publish-review flows remain membership/invitation scoped.
   - Hidden team-upload-staging KBs remain excluded from ordinary list and retrieval
     surfaces.
5. **Evidence/source honesty**
   - System KB snippets are labeled as retrieved project knowledge, not memory.
   - General assistant docs and prompts keep memory, document retrieval, and conflicts as
     separate source channels.

## Suggested verification bundle

Run the backend gate after lane integration:

```bash
uv run pytest -q
uv run ruff check . --no-cache
uv run ruff format --check .
git diff --check
```

If frontend changes are integrated in the sibling repository, also run the frontend gate
from that repository using the current `package.json` command names.
