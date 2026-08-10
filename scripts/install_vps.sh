#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install_vps.sh" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="/usr/local/bin"
OPT_DIR="/opt/zoom-audio-pipeline"
ENV_DIR="/etc/zoom-audio-pipeline"
DATA_DIR="/var/lib/zoom-audio-pipeline"
LOG_DIR="/var/log/zoom-audio-pipeline"
PUBLIC_DIR="/var/www/roadmap-reader"
LOCAL_BOT_API_DIR="/var/lib/telegram-bot-api"

install -d "$BIN_DIR" "$OPT_DIR/prompts" "$ENV_DIR" "$DATA_DIR/inbox" "$DATA_DIR/runs" "$LOG_DIR" "$PUBLIC_DIR" "$LOCAL_BOT_API_DIR"

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3 ffmpeg wkhtmltopdf caddy ca-certificates
  if apt-cache show telegram-bot-api >/dev/null 2>&1; then
    apt-get install -y telegram-bot-api
  else
    echo "telegram-bot-api package is not available via apt; install the binary before enabling telegram-bot-api-local.service." >&2
  fi
fi

install -m 0755 "$ROOT_DIR/scripts/notion_pull_audio.py" "$BIN_DIR/notion-pull-audio"
install -m 0755 "$ROOT_DIR/scripts/notion_webhook_receiver.py" "$BIN_DIR/notion-webhook-receiver"
install -m 0755 "$ROOT_DIR/scripts/notion_pipeline_runner.sh" "$BIN_DIR/notion-pipeline-runner"
install -m 0755 "$ROOT_DIR/scripts/process_new_audio.py" "$BIN_DIR/process-new-audio"
install -m 0755 "$ROOT_DIR/scripts/process_approved_roadmaps.py" "$BIN_DIR/process-approved-roadmaps"
install -m 0755 "$ROOT_DIR/scripts/telegram_roadmap_notify.py" "$BIN_DIR/telegram-roadmap-notify"
install -m 0755 "$ROOT_DIR/scripts/telegram_roadmap_webhook.py" "$BIN_DIR/telegram-roadmap-webhook"
install -m 0755 "$ROOT_DIR/scripts/telegram_notion_archive_worker.py" "$BIN_DIR/telegram-notion-archive-worker"
install -m 0755 "$ROOT_DIR/scripts/telegram_intake_cleanup.py" "$BIN_DIR/telegram-intake-cleanup"
install -m 0755 "$ROOT_DIR/scripts/transcribe_telegram_voice.py" "$BIN_DIR/transcribe-telegram-voice"
install -m 0755 "$ROOT_DIR/scripts/openrouter_roadmap_generate.py" "$BIN_DIR/openrouter-roadmap-generate"
install -m 0755 "$ROOT_DIR/scripts/generate_verification_with_openrouter.sh" "$BIN_DIR/generate-verification-with-openrouter"
install -m 0755 "$ROOT_DIR/scripts/generate_article_with_openrouter.sh" "$BIN_DIR/generate-article-with-openrouter"
install -m 0755 "$ROOT_DIR/scripts/generate_article_with_gemini_rewrite.sh" "$BIN_DIR/generate-article-with-gemini-rewrite"
install -m 0755 "$ROOT_DIR/scripts/doctor_vps.sh" "$BIN_DIR/roadmap-pipeline-doctor"
install -m 0755 "$ROOT_DIR/skills/english-roadmap-rewrite/scripts/openrouter_gemini_chat_chain.py" "$BIN_DIR/openrouter-gemini-chat-chain"
install -m 0755 "$ROOT_DIR/skills/english-roadmap-rewrite/scripts/roadmap_markdown_to_html.py" "$BIN_DIR/roadmap-markdown-to-html"

install -m 0644 "$ROOT_DIR/scripts/consultation_verification_prompt.md" "$OPT_DIR/prompts/consultation_verification_prompt.md"
install -m 0644 "$ROOT_DIR/scripts/consultation_article_prompt.md" "$OPT_DIR/prompts/consultation_article_prompt.md"
install -m 0644 "$ROOT_DIR/docs/roadmap_enhancement_options.md" "$OPT_DIR/prompts/roadmap_enhancement_options.md"

if [[ ! -f "$ENV_DIR/pipeline.env" ]]; then
  install -m 0600 "$ROOT_DIR/.env.example" "$ENV_DIR/pipeline.env"
  echo "Created $ENV_DIR/pipeline.env from template. Fill secrets before enabling services."
fi

install -m 0644 "$ROOT_DIR/deploy/systemd/notion-pipeline-poll.service" /etc/systemd/system/notion-pipeline-poll.service
install -m 0644 "$ROOT_DIR/deploy/systemd/notion-pipeline-poll.timer" /etc/systemd/system/notion-pipeline-poll.timer
install -m 0644 "$ROOT_DIR/deploy/systemd/telegram-bot-api-local.service" /etc/systemd/system/telegram-bot-api-local.service
install -m 0644 "$ROOT_DIR/deploy/systemd/telegram-roadmap-webhook.service" /etc/systemd/system/telegram-roadmap-webhook.service
install -m 0644 "$ROOT_DIR/deploy/systemd/notion-webhook-receiver.service" /etc/systemd/system/notion-webhook-receiver.service

python3 -m py_compile \
  "$BIN_DIR/notion-pull-audio" \
  "$BIN_DIR/notion-webhook-receiver" \
  "$BIN_DIR/process-new-audio" \
  "$BIN_DIR/process-approved-roadmaps" \
  "$BIN_DIR/telegram-roadmap-notify" \
  "$BIN_DIR/telegram-roadmap-webhook" \
  "$BIN_DIR/telegram-notion-archive-worker" \
  "$BIN_DIR/telegram-intake-cleanup" \
  "$BIN_DIR/openrouter-roadmap-generate"

systemctl daemon-reload

echo "Installed roadmap pipeline."
echo "Next:"
echo "1. Edit $ENV_DIR/pipeline.env"
echo "2. Run: roadmap-pipeline-doctor"
echo "3. For large Telegram files, set TELEGRAM_API_BASE_URL=http://127.0.0.1:8081 and enable telegram-bot-api-local.service"
echo "4. Enable: systemctl enable --now telegram-roadmap-webhook.service notion-pipeline-poll.timer"
