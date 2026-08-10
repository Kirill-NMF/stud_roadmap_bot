# Handoff Deploy Guide

This is the shortest production path for a non-developer on a fresh Ubuntu VPS.

## What The Person Needs

- Ubuntu/Debian VPS with root/sudo access.
- A domain or subdomain pointed to the VPS IP.
- Telegram bot token from BotFather.
- Random Telegram webhook secret string.
- Notion internal integration token.
- Notion page URL shared with that integration.
- OpenRouter API key.

## One-Command Bootstrap

Run on the VPS:

```bash
curl -fsSL https://raw.githubusercontent.com/Kirill-NMF/stud_roadmap_bot/main/scripts/bootstrap_ubuntu.sh | sudo bash
```

Then fill secrets:

```bash
sudo nano /etc/zoom-audio-pipeline/pipeline.env
```

Required values:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_SECRET=...
TELEGRAM_CHAT_ID=...
NOTION_API_KEY=...
NOTION_TARGET=https://www.notion.so/...
OPENROUTER_API_KEY=...
```

Check installation:

```bash
sudo roadmap-pipeline-doctor
```

Enable services:

```bash
sudo systemctl enable --now telegram-roadmap-webhook.service notion-webhook-receiver.service notion-pipeline-poll.timer
```

## HTTPS And Webhook URLs

Use `deploy/Caddyfile.example` as a template.

Replace `roadmap.example.com` with the real domain, then install it:

```bash
sudo cp /opt/stud_roadmap_bot/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Public URLs:

- Telegram webhook: `https://YOUR_DOMAIN/roadmap-telegram/webhook`
- Telegram health: `https://YOUR_DOMAIN/roadmap-telegram/health`
- Notion webhook: `https://YOUR_DOMAIN/notion/webhook`
- Notion health: `https://YOUR_DOMAIN/notion/health`
- Roadmap reader: `https://YOUR_DOMAIN/roadmap-reader/...`

Register Telegram webhook:

```bash
sudo TELEGRAM_BOT_TOKEN="$(grep '^TELEGRAM_BOT_TOKEN=' /etc/zoom-audio-pipeline/pipeline.env | cut -d= -f2-)" \
  /usr/local/bin/telegram-roadmap-notify \
  --env-file /etc/zoom-audio-pipeline/pipeline.env \
  --set-webhook "https://YOUR_DOMAIN/roadmap-telegram/webhook"
```

Register Notion webhook in the Notion integration settings with:

```text
https://YOUR_DOMAIN/notion/webhook
```

The first Notion webhook verification request should be accepted by the receiver automatically. The poll timer is also enabled as a fallback, so uploaded Notion files are still picked up if a webhook event is delayed.

## First Smoke Test

1. Send an audio file to the Telegram bot.
2. Bot should reply that the file was accepted.
3. Wait for verification message with buttons `Открыть` and `Согласен`.
4. Click `Согласен`.
5. Wait for final article, HTML, and PDF.

Logs:

```bash
sudo tail -f /var/log/zoom-audio-pipeline/runner.log
sudo tail -f /var/log/zoom-audio-pipeline/events.jsonl
sudo tail -f /var/log/zoom-audio-pipeline/telegram-webhook.log
```

## Main Commands For Support

```bash
sudo systemctl status telegram-roadmap-webhook.service
sudo systemctl status notion-webhook-receiver.service
sudo systemctl status notion-pipeline-poll.timer
sudo systemctl start notion-pipeline-poll.service
sudo roadmap-pipeline-doctor
```

## Docker Note

Docker is not the primary deploy path yet.

The current recommended path is systemd + Caddy because the pipeline needs:

- persistent local state;
- PDF rendering through `wkhtmltopdf`;
- public static roadmap files;
- Telegram and Notion webhooks;
- timer fallback through systemd.

Docker can be added later, but it needs its own clean container smoke test before it should be handed to a non-developer.

