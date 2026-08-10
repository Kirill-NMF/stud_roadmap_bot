# Deployment Testing Notes

This folder stores the agreed deployment-testing design for the roadmap pipeline.

Goal: later test the install/setup flow as if a non-developer deploys it on a VPS, without breaking the current production pipeline or other projects on the same server.

## Current Decision

Use a staged installation test first, not a production overwrite.

Recommended modes:

- `--dry-run`: validate inputs and print planned changes without writing production files.
- `--staging-root /tmp/stud-roadmap-install-test`: write generated files into an isolated tree.
- `--no-systemd`: do not write to `/etc/systemd/system`.
- `--no-caddy-reload`: do not replace or reload the live Caddy config.
- `--no-webhooks`: do not register Telegram or Notion webhooks.
- `--passive-api-checks`: only check that API keys are syntactically valid and can answer safe read/test requests.

## Why Not Use The Same Bot For Full Staging E2E

Telegram bots can have only one active webhook. If a staging install calls `setWebhook` with the same bot token, it can steal messages from the production instance.

So with the same production keys we can safely test:

- env rendering;
- Caddyfile rendering;
- systemd unit rendering;
- command availability;
- filesystem permissions;
- Telegram `getMe`;
- Notion access checks;
- OpenRouter tiny test call;
- local dry-run/staging file layout.

With the same production keys we must not test:

- Telegram `setWebhook`;
- real Telegram audio intake;
- real Notion webhook registration;
- writing test archive pages into the production Notion page;
- writing into production `/var/lib/zoom-audio-pipeline`;
- writing into production `/var/www/roadmap-reader`;
- restarting or overwriting unrelated Caddy/systemd configs.

## What Full E2E Requires

For a full safe E2E install test, prepare isolated resources:

- separate Telegram test bot token;
- separate Notion test page shared with the integration;
- separate test domain/subdomain, for example `roadmap-test.example.com`;
- separate ports, for example:
  - Telegram webhook test port: `8892`;
  - Notion webhook test port: `8891`;
- separate paths:
  - state: `/var/lib/zoom-audio-pipeline-test`;
  - logs: `/var/log/zoom-audio-pipeline-test`;
  - public files: `/var/www/roadmap-reader-test`.

## Recommended Next Implementation

Add an interactive setup command:

```bash
sudo /opt/stud_roadmap_bot/scripts/setup_wizard.py setup
```

It should ask:

- domain;
- Telegram bot token;
- Telegram webhook secret, with Enter generating a random secret;
- Telegram chat id, optional at first;
- Notion token;
- Notion page URL;
- OpenRouter API key;
- whether to register webhooks now.

Then it should:

- write `/etc/zoom-audio-pipeline/pipeline.env` with mode `0600`;
- generate Caddyfile from template;
- validate Caddy config before applying;
- install/reload systemd units;
- optionally register Telegram webhook;
- print exact Notion webhook URL and manual Notion UI steps;
- run `roadmap-pipeline-doctor`;
- print the first smoke-test checklist.

