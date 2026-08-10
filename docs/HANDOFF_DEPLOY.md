# Full Deploy Guide For A Non-Developer

This guide explains how to deploy the student roadmap bot on a fresh Ubuntu VPS.

The result:

- teacher sends Zoom/audio file to Telegram bot;
- bot starts processing;
- bot sends a verification page with `Открыть` and `Согласен`;
- teacher approves or sends voice/text corrections;
- bot returns final article as a beautiful HTML page and PDF;
- audio is archived to Notion.

## 0. What You Need Before Starting

You need:

- a VPS with Ubuntu or Debian;
- root/sudo access to that VPS;
- a domain or subdomain, for example `roadmap.example.com`;
- DNS `A` record pointing that domain to the VPS IP;
- Telegram bot token;
- Notion integration token;
- Notion page URL where audio archive pages will be created;
- OpenRouter API key.

Recommended VPS minimum:

- 2 CPU;
- 4 GB RAM;
- 20 GB disk.

## 1. Prepare DNS

In your domain/DNS panel create:

```text
Type: A
Name: roadmap
Value: YOUR_VPS_IP
```

Example:

```text
roadmap.example.com -> 123.123.123.123
```

Wait until the domain opens from your computer. It can take from a few minutes to a few hours.

## 2. Create Telegram Bot

1. Open Telegram.
2. Find `@BotFather`.
3. Send `/newbot`.
4. Follow the steps and copy the bot token.

It looks like:

```text
1234567890:AA...
```

Do not publish this token anywhere.

## 3. Create Telegram Webhook Secret

Create any long random string. Example:

```text
roadmap_very_secret_2026_change_me
```

This is not your bot token. It is just an extra secret between Telegram and your server.

## 4. Get Your Telegram Chat ID

The simplest way:

1. Install the project first.
2. Start the bot service.
3. Send any message to your bot.
4. Run on the VPS:

```bash
sudo /usr/local/bin/telegram-roadmap-notify \
  --env-file /etc/zoom-audio-pipeline/pipeline.env \
  --get-updates
```

Find:

```json
"chat": {"id": 123456789}
```

Use that number as `TELEGRAM_CHAT_ID`.

## 5. Create Notion Integration

1. Go to Notion integrations page.
2. Create a new internal integration.
3. Copy the integration token.

It usually starts with:

```text
ntn_...
```

Then open the Notion page where audio files should be archived.

1. Click `...`.
2. Click connections/integrations.
3. Add your integration to this page.
4. Copy the Notion page URL.

Use that URL as `NOTION_TARGET`.

## 6. Create OpenRouter Key

1. Open OpenRouter.
2. Create API key.
3. Copy it.

It usually starts with:

```text
sk-or-...
```

Use it as `OPENROUTER_API_KEY`.

## 7. Install The Project On VPS

SSH into the VPS and run:

```bash
curl -fsSL https://raw.githubusercontent.com/Kirill-NMF/stud_roadmap_bot/main/scripts/bootstrap_ubuntu.sh | sudo bash
```

This installs:

- repository into `/opt/stud_roadmap_bot`;
- system scripts into `/usr/local/bin`;
- prompts into `/opt/zoom-audio-pipeline/prompts`;
- state folders into `/var/lib/zoom-audio-pipeline`;
- logs into `/var/log/zoom-audio-pipeline`;
- systemd services/timer;
- required packages like Python, ffmpeg, wkhtmltopdf and Caddy.

## 8. Fill The Env File

Open:

```bash
sudo nano /etc/zoom-audio-pipeline/pipeline.env
```

Fill these required values:

```bash
TELEGRAM_BOT_TOKEN=PASTE_TELEGRAM_BOT_TOKEN_HERE
TELEGRAM_WEBHOOK_SECRET=PASTE_RANDOM_SECRET_HERE
TELEGRAM_CHAT_ID=PASTE_YOUR_TELEGRAM_CHAT_ID_HERE

NOTION_API_KEY=PASTE_NOTION_TOKEN_HERE
NOTION_TARGET=PASTE_NOTION_PAGE_URL_HERE

OPENROUTER_API_KEY=PASTE_OPENROUTER_KEY_HERE

ROADMAP_PUBLIC_BASE_URL=https://YOUR_DOMAIN/roadmap-reader
ROADMAP_PUBLIC_ROOT=/var/www/roadmap-reader
```

Leave these defaults unless you know what you are changing:

```bash
OPENROUTER_STT_MODEL=openai/whisper-large-v3-turbo
OPENROUTER_ROADMAP_MODEL=openai/gpt-5.5
OPENROUTER_ARTICLE_MODEL=openai/gpt-5.5
OPENROUTER_VERIFICATION_MODEL=openai/gpt-5.5
GEMINI_REWRITE_MODEL=google/gemini-2.5-pro

PIPELINE_PYTHON=python3
TRANSCRIPTION_PROVIDER=openrouter
OPENROUTER_STT_RETRIES=3
OPENROUTER_STT_RETRY_DELAY=3
OPENROUTER_STT_COMPRESS_THRESHOLD_MB=20
OPENROUTER_STT_FALLBACK=local

VERIFICATION_SCRIPT=/usr/local/bin/generate-verification-with-openrouter
ARTICLE_SCRIPT=/usr/local/bin/generate-article-with-gemini-rewrite
ARTICLE_DRAFT_SCRIPT=/usr/local/bin/generate-article-with-openrouter
GEMINI_REWRITE_TIMEOUT_SECONDS=1200
```

Save in nano:

- press `Ctrl+O`;
- press `Enter`;
- press `Ctrl+X`.

## 9. Configure Caddy HTTPS

Copy the example Caddy config:

```bash
sudo cp /opt/stud_roadmap_bot/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
```

Replace:

```text
roadmap.example.com
```

with your real domain.

Then reload Caddy:

```bash
sudo systemctl reload caddy
```

Check:

```bash
curl https://YOUR_DOMAIN/health
curl https://YOUR_DOMAIN/roadmap-telegram/health
curl https://YOUR_DOMAIN/notion/health
```

Expected result for health checks:

```json
{"ok": true}
```

or simple:

```text
ok
```

## 10. Check Installation

Run:

```bash
sudo roadmap-pipeline-doctor
```

At the end you want:

```text
doctor ok
```

If it says `fail`, fix the shown line before continuing.

## 11. Enable Services

Run:

```bash
sudo systemctl enable --now telegram-roadmap-webhook.service notion-webhook-receiver.service notion-pipeline-poll.timer
```

Check:

```bash
sudo systemctl status telegram-roadmap-webhook.service
sudo systemctl status notion-webhook-receiver.service
sudo systemctl status notion-pipeline-poll.timer
```

All should be active.

## 12. Register Telegram Webhook

Run:

```bash
sudo /usr/local/bin/telegram-roadmap-notify \
  --env-file /etc/zoom-audio-pipeline/pipeline.env \
  --set-webhook "https://YOUR_DOMAIN/roadmap-telegram/webhook"
```

Expected result:

```json
{"ok": true, ...}
```

## 13. Register Notion Webhook

In Notion integration settings, add webhook URL:

```text
https://YOUR_DOMAIN/notion/webhook
```

When Notion sends the verification request, the server saves the verification token automatically into:

```text
/etc/zoom-audio-pipeline/notion-webhook.env
```

The timer fallback is also enabled, so the pipeline can still poll Notion even if webhook events are delayed.

## 14. First Real Test

Use Telegram first. This is the easiest test.

1. Open your Telegram bot.
2. Send a short audio file.
3. Bot should answer: file accepted / pipeline started.
4. Wait for verification.
5. Click `Открыть` to inspect the verification page.
6. Click `Согласен`.
7. Wait for final article message.
8. Bot should send:
   - link/button to beautiful HTML;
   - HTML file;
   - PDF file.

## 15. Logs If Something Is Slow

Open these logs:

```bash
sudo tail -f /var/log/zoom-audio-pipeline/runner.log
```

In another terminal:

```bash
sudo tail -f /var/log/zoom-audio-pipeline/events.jsonl
```

Telegram webhook logs:

```bash
sudo tail -f /var/log/zoom-audio-pipeline/telegram-webhook.log
```

Notion webhook logs:

```bash
sudo tail -f /var/log/zoom-audio-pipeline/notion-webhook.log
```

## 16. Common Problems

### Bot does not answer

Check service:

```bash
sudo systemctl status telegram-roadmap-webhook.service
```

Check webhook:

```bash
sudo /usr/local/bin/telegram-roadmap-notify \
  --env-file /etc/zoom-audio-pipeline/pipeline.env \
  --get-updates
```

### `roadmap-pipeline-doctor` says an env key is missing

Open env file:

```bash
sudo nano /etc/zoom-audio-pipeline/pipeline.env
```

Fill the missing key. Do not leave required keys empty.

### Generated HTML button opens wrong domain

Check:

```bash
grep ROADMAP_PUBLIC_BASE_URL /etc/zoom-audio-pipeline/pipeline.env
```

It must be:

```bash
ROADMAP_PUBLIC_BASE_URL=https://YOUR_DOMAIN/roadmap-reader
```

Restart services after changing env:

```bash
sudo systemctl restart telegram-roadmap-webhook.service notion-webhook-receiver.service
```

### Notion archive does not appear

Check:

```bash
sudo tail -f /var/log/zoom-audio-pipeline/events.jsonl
```

Look for:

```text
notion_archive_upload_done
```

Also check that the Notion page is shared with the integration.

### Processing is slow

Long audio can take several minutes. Watch:

```bash
sudo tail -f /var/log/zoom-audio-pipeline/runner.log
```

Expected stages:

- `telegram-notion-archive-worker start/done`;
- `notion-pull-audio start/done`;
- `process-new-audio start/done`;
- `process-approved-roadmaps start/done`.

## 17. Updating Later

Run:

```bash
cd /opt/stud_roadmap_bot
sudo git pull
sudo bash scripts/install_vps.sh
sudo roadmap-pipeline-doctor
sudo systemctl restart telegram-roadmap-webhook.service notion-webhook-receiver.service
```

## 18. Docker Note

Docker is not the main handoff path yet.

The current production path is:

```text
Ubuntu VPS + systemd + Caddy
```

This path is closer to the tested VPS setup because the project needs:

- persistent state;
- Telegram webhook;
- Notion webhook;
- polling fallback timer;
- public static HTML files;
- PDF rendering through `wkhtmltopdf`.

Docker can be added later, but it should only be handed to a non-developer after a separate clean Docker smoke test.

