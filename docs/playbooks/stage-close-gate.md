# Stage Close Gate

Use this before treating a non-trivial change as complete.

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
- Residual risk:
- Next step:
```

Use `not applicable` when a gate truly does not apply. Use `skipped` only when
a useful gate was not run and give the reason.

