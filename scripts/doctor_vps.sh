#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${PIPELINE_ENV_FILE:-/etc/zoom-audio-pipeline/pipeline.env}"
FAILED=0

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf 'ok   %s\n' "$name"
  else
    printf 'fail %s\n' "$name"
    FAILED=1
  fi
}

require_file() {
  local path="$1"
  [[ -s "$path" ]]
}

require_command() {
  command -v "$1" >/dev/null 2>&1
}

require_env_key() {
  local key="$1"
  grep -Eq "^(export[[:space:]]+)?${key}=.+" "$ENV_FILE"
}

check "env file exists" require_file "$ENV_FILE"
check "python3" require_command python3
check "ffmpeg" require_command ffmpeg
check "wkhtmltopdf" require_command wkhtmltopdf
check "roadmap-markdown-to-html" require_command roadmap-markdown-to-html

for path in \
  /usr/local/bin/notion-pull-audio \
  /usr/local/bin/notion-webhook-receiver \
  /usr/local/bin/process-new-audio \
  /usr/local/bin/process-approved-roadmaps \
  /usr/local/bin/telegram-roadmap-webhook \
  /usr/local/bin/telegram-roadmap-notify \
  /usr/local/bin/openrouter-roadmap-generate \
  /usr/local/bin/generate-verification-with-openrouter \
  /usr/local/bin/generate-article-with-openrouter \
  /usr/local/bin/generate-article-with-gemini-rewrite \
  /usr/local/bin/openrouter-gemini-chat-chain; do
  check "$path" test -x "$path"
done

for path in \
  /opt/zoom-audio-pipeline/prompts/consultation_verification_prompt.md \
  /opt/zoom-audio-pipeline/prompts/consultation_article_prompt.md \
  /opt/zoom-audio-pipeline/prompts/roadmap_enhancement_options.md; do
  check "$path" require_file "$path"
done

for dir in \
  /var/lib/zoom-audio-pipeline/inbox \
  /var/lib/zoom-audio-pipeline/runs \
  /var/log/zoom-audio-pipeline \
  /var/www/roadmap-reader; do
  check "$dir writable" test -w "$dir"
done

if [[ -s "$ENV_FILE" ]]; then
  for key in TELEGRAM_BOT_TOKEN NOTION_API_KEY NOTION_TARGET OPENROUTER_API_KEY; do
    check "env $key configured" require_env_key "$key"
  done
fi

check "telegram-roadmap-webhook unit" systemctl cat telegram-roadmap-webhook.service
check "notion-webhook-receiver unit" systemctl cat notion-webhook-receiver.service
check "notion-pipeline-poll timer unit" systemctl cat notion-pipeline-poll.timer

if [[ "$FAILED" -eq 0 ]]; then
  echo "doctor ok"
else
  echo "doctor failed"
fi

exit "$FAILED"
