# Deployment And Handoff

Use this playbook when a small agent must be deployable by a non-developer.

## Principle

Do not package unknown behavior. First make the current production-style flow
autonomous, tested, and observable. Package only after a full E2E gate is green.

## Recommended Order

1. Production contract audit.
2. Autonomous runtime path, without interactive development tools.
3. Naming contract fix.
4. Full E2E on the current VPS mode.
5. Repository cleanup and extraction.
6. Installer and doctor script.
7. Staging/dry-run install test.
8. Optional Docker/Compose layer.
9. Fresh install release gate.

## Installer Shape

For a non-developer, prefer one command that starts a guided setup:

```bash
curl -fsSL https://example.com/install.sh | sudo bash
```

The installer or setup wizard should ask for:

- domain;
- bot token or service token;
- webhook secret, with generation option;
- target chat/page/workspace id;
- model/API key;
- public base URL;
- whether to register webhooks now.

The summary must not print secrets.

## Dry-Run And Staging

Add safe flags:

```text
--dry-run
--staging-root /tmp/project-install-test
--no-systemd
--no-caddy-reload
--no-webhooks
--passive-api-checks
```

These allow testing on a production VPS without stealing webhooks or overwriting
live files.

## Conflict Matrix

Before deployment tests, document shared production risks:

| Resource | Passive check | Full staging | Rule |
| --- | --- | --- | --- |
| Bot token | Safe for identity checks | Unsafe if webhook changes | Do not call `setWebhook` with production bot |
| Production page/db | Safe for read | Unsafe for write | Use separate test resource |
| Live reverse proxy | Unsafe to reload blindly | Unsafe | Validate generated config first |
| Live systemd units | Unsafe to overwrite | Unsafe | Render into staging first |
| Runtime state dirs | Unsafe | Unsafe | Use staging dirs |

## Doctor Script

Ship a doctor command that checks:

- env file exists and required keys are filled;
- secrets are not placeholder values;
- dependencies exist;
- runtime directories are writable;
- services are active;
- health endpoints respond;
- provider key can do a tiny safe check;
- public base URL is reachable;
- logs are writable;
- no obvious conflicting webhook is configured.

Doctor output should be concise and actionable:

```text
ok: ffmpeg found
ok: env loaded
fail: TELEGRAM_BOT_TOKEN is missing
next: edit /etc/project/project.env
```

## CI/CD Baseline

CI must not require real production keys.

Minimum useful checks:

- syntax/import checks;
- unit tests;
- contract tests;
- template rendering tests;
- secret scan;
- Docker build if Docker exists.

## Release Gate

Before handoff:

```text
fresh server or fresh folder install
env filled
doctor OK
sample input E2E OK
final artifact delivered
no duplicate run
cleanup conservative
docs match actual commands
```

Docker can be a second layer, but do not start with Docker if the current system
still has hidden runtime dependencies.

