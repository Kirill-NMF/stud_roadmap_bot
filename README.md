# Roadmap Audio Pipeline

Pipeline for turning Zoom/Telegram/Notion audio consultations into student-facing English roadmap articles.

Flow:

1. Audio arrives from Telegram bot or from a configured Notion page.
2. VPS transcribes it through OpenRouter Whisper.
3. Verification Markdown is generated and sent to the teacher in Telegram.
4. Teacher clicks `Согласен` or sends text/voice corrections.
5. Article Markdown is generated, rewritten through Gemini via OpenRouter, rendered to HTML/PDF, and sent back to Telegram.
6. Telegram-origin audio is also archived to Notion and cleaned locally only after archive + audio processing are safe.

## Install On VPS

For handoff to another person, start with [docs/HANDOFF_DEPLOY.md](docs/HANDOFF_DEPLOY.md).

Shortest bootstrap on a fresh Ubuntu/Debian VPS:

```bash
curl -fsSL https://raw.githubusercontent.com/Kirill-NMF/stud_roadmap_bot/main/scripts/bootstrap_ubuntu.sh | sudo bash
```

Manual install after cloning:

```bash
sudo bash scripts/install_vps.sh
sudo nano /etc/zoom-audio-pipeline/pipeline.env
sudo roadmap-pipeline-doctor
sudo systemctl enable --now telegram-roadmap-webhook.service notion-webhook-receiver.service notion-pipeline-poll.timer
```

Required secrets in `/etc/zoom-audio-pipeline/pipeline.env`:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_CHAT_ID`
- `NOTION_API_KEY`
- `NOTION_TARGET`
- `OPENROUTER_API_KEY`

Do not commit real env files.

## Default LLM Mode

Fresh installs default to autonomous OpenRouter generation:

- verification: `/usr/local/bin/generate-verification-with-openrouter`
- article draft: `/usr/local/bin/generate-article-with-openrouter`
- final rewrite: `/usr/local/bin/generate-article-with-gemini-rewrite`

To keep a legacy Codex CLI draft stage, set:

```bash
VERIFICATION_SCRIPT=/usr/local/bin/generate-verification-with-codex
ARTICLE_DRAFT_SCRIPT=/usr/local/bin/generate-article-with-codex
```

## Runtime Paths

- data: `/var/lib/zoom-audio-pipeline`
- logs: `/var/log/zoom-audio-pipeline`
- public reader: `/var/www/roadmap-reader`
- prompts: `/opt/zoom-audio-pipeline/prompts`

Useful checks:

```bash
sudo roadmap-pipeline-doctor
sudo systemctl status telegram-roadmap-webhook.service notion-pipeline-poll.timer
sudo tail -f /var/log/zoom-audio-pipeline/runner.log
sudo tail -f /var/log/zoom-audio-pipeline/events.jsonl
```

## Local Tests

On this Windows workspace:

```powershell
& 'C:\Users\bests\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\roadmap_pipeline_tests.py
```

Current expected result: all tests pass.
