# Gates And Testing

Use this playbook to select the smallest set of checks that proves a change.

## Gate Matrix Template

| Touched area | Required focus | Gates |
| --- | --- | --- |
| Intake/webhook | duplicate events, payload parsing, state claim | unit, idempotency, fake smoke |
| File processing | large files, partial files, output artifacts | contract test, failure test |
| Model/API wrapper | prompt/config, provider response, validation | wrapper smoke, artifact check |
| Review/approval UI | message text, buttons, callbacks, corrections | unit, real-service smoke |
| Delivery | public files, document send, retry | render smoke, delivery smoke |
| Runtime services | systemd/process manager, ports, logs | VPS smoke, doctor |
| Deployment | install scripts, env rendering, dry-run | staging install test |
| Secrets | env, logs, reports, public artifacts | security scan |
| Docs/checkpoint | commands, paths, current truth | freshness check |

## Testing Layers

Use layers in this order:

1. Static checks: syntax, import, lint if available.
2. Unit/contract tests: pure functions and status transitions.
3. Fake pipeline smoke: temp dirs and fake artifacts.
4. Provider smoke: tiny safe API calls.
5. VPS smoke: services, timers, health endpoints, logs.
6. Real-service smoke: actual bot/webhook/file flow.
7. Full E2E: one sample input from intake to final delivery.

Do not use a real-service smoke when a deterministic local test proves the
change. Do use it when the change touches real callbacks, buttons, webhooks, or
delivery.

## Failure Tests

Design failure checks before declaring a pipeline reliable:

- upload failed but processing succeeded;
- processing failed but archive succeeded;
- notify failed after output was created;
- duplicate input arrived;
- webhook retried the same event;
- user clicked the approval button twice;
- cleanup ran before archive completed;
- provider returned empty or malformed output;
- public file was generated but not sent.

## Full E2E Gate

Before packaging or handoff, one realistic input must reach the final user
output:

```text
input -> verification/review -> approval/revision -> generation -> rewrite ->
HTML/PDF or equivalent final artifact -> delivery
```

Record:

- input name;
- run id/path;
- status fields;
- final artifact names;
- delivery evidence;
- elapsed time by stage if available.

## Stage-Close Gate

Do not call non-trivial work complete until this is known:

```text
Scope: passed / skipped with reason
Contract: passed / skipped with reason
Idempotency: passed / skipped with reason
Security: passed / skipped with reason
Tests: exact command and result
Real-service smoke: passed / not applicable / skipped with reason
Checkpoint: updated / unchanged with reason
Residual risk:
```

