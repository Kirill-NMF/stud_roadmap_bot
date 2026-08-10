# Conflict Matrix

Use this before running any deployment test on a VPS that already hosts production projects.

## Shared Production Keys

| Area | Passive check safe? | Full staging safe? | Risk | Rule |
| --- | --- | --- | --- | --- |
| Telegram bot token | Yes | No | `setWebhook` can replace the production webhook. | Do not call `setWebhook` with the production bot during staging. |
| Telegram chat id | Yes | Limited | Test messages can confuse the teacher chat. | Only send explicit test messages if the user approves. |
| Notion integration token | Yes | Limited | Test writes can create archive noise or duplicate processing. | Prefer read checks only unless using a test page. |
| Production Notion page | Yes, read-only | No | Staging can create duplicate pages/files. | Use a separate Notion test page for write tests. |
| OpenRouter key | Yes | Yes | Cost and rate limits. | Use tiny test calls; do not run long E2E unless approved. |
| Caddy live config | No | No | Replacing `/etc/caddy/Caddyfile` can affect other projects. | Use generated staging Caddyfile and `caddy validate`; no reload. |
| systemd live units | No | No | Unit overwrite/restart can affect production pipeline. | Render units into staging root; do not install to `/etc/systemd/system`. |
| `/usr/local/bin` | No | No | Overwrites live pipeline commands. | In staging, install into staging bin dir. |
| `/var/lib/zoom-audio-pipeline` | No | No | Can mix state/runs with production. | Use `/tmp/...` or `/var/lib/zoom-audio-pipeline-test`. |
| `/var/www/roadmap-reader` | No | No | Can expose test artifacts under production reader. | Use `/tmp/.../www` or `/var/www/roadmap-reader-test`. |

## Safe Test Levels

### Level 1: Static Local Test

Safe on developer machine.

- Unit tests.
- Security grep.
- Template rendering tests.
- Wizard stdin simulation.

### Level 2: VPS Dry-Run

Safe on production VPS if flags are respected.

- Clone/update repo into staging directory.
- Render env/Caddy/systemd files into staging root.
- Run `doctor` in dry-run mode.
- Validate generated Caddyfile with `caddy validate`.
- Check installed commands exist.

Must not:

- register webhooks;
- reload Caddy;
- enable/restart services;
- write production state.

### Level 3: Passive API Checks

Usually safe with production keys.

- Telegram `getMe`.
- Notion page read/access check.
- OpenRouter tiny request.

Must not:

- send production chat messages unless approved;
- upload test files to production Notion;
- call Telegram `setWebhook`.

### Level 4: Isolated Full E2E

Safe only with separate test resources.

- Test Telegram bot.
- Test Notion page.
- Test domain/subdomain.
- Test ports and directories.

This is the first level where we can test:

- audio sent to bot;
- verification received;
- approval clicked;
- final HTML/PDF received.

### Level 5: Production Cutover

Only after Level 4 passes.

- Register production Telegram webhook.
- Register production Notion webhook.
- Enable production services.
- Send one real short test audio.

