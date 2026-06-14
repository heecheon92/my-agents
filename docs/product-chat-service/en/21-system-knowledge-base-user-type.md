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
- `/auth/me` exposes `user_type` and `can_manage_system_knowledge`.
- Root/system users can create, list, read, rename, delete, upload/create documents,
  edit documents, and ingest documents in system KBs.
- Normal users and guests cannot list or manage system KBs and receive concealed
  not-found responses for guessed system KB/document IDs.
- Authenticated chat retrieval includes standard system KBs ambiently for all users,
  including guests.
- Public run metadata keeps selected/resolved personal/group KB IDs separate from
  ambient system KB IDs.

## Boundary notes

- System knowledge is public to authenticated chat users. Do not upload secrets.
- System knowledge appears as authorized retrieval context and citations, not as
  stored user memory.
- Personal, group, publish-review, hidden staging, explicit document-permission, and
  guest-limit rules remain separate regression boundaries.
