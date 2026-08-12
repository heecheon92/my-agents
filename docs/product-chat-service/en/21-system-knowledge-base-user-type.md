# System knowledge base and user type contract

This slice adds project-level system knowledge without treating it as user memory.

## Contract

- `users.user_type` is separate from `account_type`.
  - `account_type` remains `registered` or `guest`.
  - `user_type` is `normal`, `root`, or `system`.
- `root` and `system` are equivalent system-knowledge managers in this version.
- User-type mutation is operator-script-only:

```bash
uv run python -m scripts.set_user_type --email owner@example.com --user-type root --dry-run
uv run python -m scripts.ops account set-user-type --email owner@example.com --user-type system
```

There is no public API route that assigns or changes `user_type`.

## System knowledge behavior

- System KBs use `scope = "system"`, `group_id = null`, and `purpose = "standard"`.
- `owner_user_id` records the privileged creator for audit; ownership is not the public
  retrieval rule.
- Auth user responses (`/auth/me`, login/signup envelopes, invitation signup) omit
  `user_type` and `can_manage_system_knowledge` for normal users and guests.
- Root/system users receive `user_type` plus `can_manage_system_knowledge: true` so
  the UI can show system-source management without exposing a negative role signal
  for everyone else.
- Root/system users can create, list, read, rename, delete, upload/create documents,
  edit documents, and ingest documents in system KBs.
- Normal users and guests cannot list or manage system KBs and receive concealed
  not-found responses for guessed system KB/document IDs.
- Authenticated chat retrieval includes standard system KBs ambiently for all users,
  including guests.
- Public run metadata and citations expose only user-visible personal/group sources.
  Ambient system KB IDs, counts, document IDs, chunk IDs, filenames, snippets, and
  citation entries are omitted.

## Boundary notes

- System knowledge may influence answers for authenticated chat users, so do not upload
  secrets or facts that users must never receive.
- System knowledge is injected as internal retrieval context, not stored user memory.
  Its provenance remains available in internal run/citation audit records but is not
  returned through user-facing run, event, or citation responses.
- Personal, group, publish-review, hidden staging, explicit document-permission, and
  guest-limit rules remain separate regression boundaries.
