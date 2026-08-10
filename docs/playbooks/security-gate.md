# Security Gate

Use this for every non-trivial change touching runtime services, external APIs,
Telegram, Notion, public files, logs, or generated content.

For Telegram, Notion, VPS/systemd/Caddy, webhooks, secrets/env, cleanup, or
provider routing, this gate must be completed before implementation and again
before stage close. Do not deploy or restart production services until the
security risks and rollback point are stated in the approved plan.

## Baseline Check

- No tokens, API keys, sessions, signed URLs, `.env` files, or secrets are added to tracked files.
- Logs and final reports do not expose secrets or signed URLs.
- Runtime credentials stay on the VPS or in approved runtime storage only.
- Public HTML/PDF paths do not expose private tokens or raw signed Notion URLs.
- LLM output is treated as data; HTML rendering must remain controlled.
- Telegram callbacks and voice/text corrections update only the intended run.
- Telegram voice messages cannot become new pipeline intake files.
- Telegram audio/document intake cannot steal or overwrite an active review.
- Duplicate Notion events and repeated Telegram callbacks are idempotent.
- External calls have clear provider boundaries and failure behavior.
- Cleanup only deletes files after the pipeline no longer needs them and archive
  recovery rules are satisfied.

## Security Review Triggers

Escalate before broad changes to:

- Notion token access, signed URL handling, or file download policy.
- Telegram bot token, webhook URL, callback auth, or real test account session.
- OpenRouter/Gemini key handling or model-provider routing.
- Public reader URLs, HTML/PDF rendering, or generated content rendering.
- VPS systemd/Caddy/firewall/runtime env.
- Any new external service, paid provider, or production credential.

When a trigger applies, stop and produce a short security note:

```text
Security pre-check:
- Touched risky area:
- Intended state change:
- What must not happen:
- Idempotency/duplicate behavior:
- Secret/log exposure risk:
- Rollback point:
- Required tests:
```

## Report Shape

```text
Security:
- Baseline check: passed / blocked / skipped
- Secrets touched: no / yes, details without values
- Public output checked: yes / not applicable
- Logs checked for secret exposure: yes / not applicable
- Review trigger: no / yes
```
