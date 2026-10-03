# AGENTS.md — `scripts`

Follow the repository root `AGENTS.md`. This directory contains focused command
modules and the shared operator CLI, `scripts.ops`.

## Interactive CLI discoverability

Whenever adding a new script or an operator-facing action to an existing script,
explicitly consider whether it belongs in `scripts.ops --interactive`. Do not
automatically stop at creating a standalone command.

- For routine operator tasks such as account maintenance, guest trial management,
  or database operations, prefer adding both a dispatcher subcommand and an
  interactive menu entry in `scripts/ops.py` in the same change.
- Keep the operation in its focused script or service. The interactive CLI should
  collect inputs and delegate, rather than duplicate business logic.
- Preserve environment targeting, dry-run/apply behavior, existing confirmation
  requirements, cancellation, and exit codes when exposing an operation through
  the menu. Interactive mode must not silently turn a preview into a mutation.
- A developer utility, benchmark, or automation-only command may reasonably stay
  standalone. Explain that choice briefly in the handoff or PR description; do
  not omit interactive integration without considering it.
- Update `scripts/README.md` to show how operators discover and run the command.
  When adding a menu entry, extend `tests/test_ops_script.py` to verify delegation
  and the relevant input, environment, and safety behavior.

Creating an operator command does not itself authorize running it against
production or sending email.
