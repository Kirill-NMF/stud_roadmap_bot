# Stage Close Gate

Use this before treating a non-trivial, production-facing, or risky change as
complete. A change is not done until this report is written.

If the work touched Telegram, Notion, VPS/systemd/Caddy, webhooks, secrets/env,
cleanup, provider routing, or public delivery, include evidence for the relevant
security/idempotency gates. Do not hide skipped gates inside a generic "tests
passed" line.

## Checklist

- Scope: work stayed inside the approved task.
- Contract: changed stage has explicit input, output, status, log, and failure behavior.
- Idempotency: repeated Notion events, Telegram callbacks, or script runs are safe.
- Security: no secrets or signed URLs were printed, committed, or added to docs.
- Testing: relevant checks actually ran and results are known.
- VPS state: deployed scripts/services match local source when deployment happened.
- Telegram behavior: real/fake smoke ran when bot behavior changed.
- Gemini behavior: chain artifacts and model/wrapper were checked when rewrite changed.
- Repo hygiene: intended files only; generated/runtime artifacts kept out of docs unless intentionally stored.
- Checkpoint: update `docs/current-checkpoint.md` if project state, runtime paths, models, or operational steps changed.
- Rollback: commit hash, deployment point, or concrete revert path is known.

## Report Shape

```text
Stage close:
- Scope: passed / blocked / skipped
- Contract: passed / blocked / skipped
- Security: passed / blocked / skipped
- Tests: exact commands and results
- VPS: deployed / not applicable
- Telegram: checked / not applicable
- Gemini: checked / not applicable
- Checkpoint: updated / unchanged
- Rollback:
- Residual risk:
- Next step:
```

Use `not applicable` when a gate truly does not apply. Use `skipped` only when
a useful gate was not run and give the reason.

For Telegram bot behavior changes, include:

```text
Telegram boundary:
- New intake still requires audio/document:
- Voice/text corrections only target pending review:
- Duplicate update/callback behavior:
- User-visible confirmation checked:
```
