#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE="/var/lib/zoom-audio-pipeline/runner.lock"
LOG_DIR="/var/log/zoom-audio-pipeline"
INBOX_DIR="/var/lib/zoom-audio-pipeline/inbox"
STATE_FILE="/var/lib/zoom-audio-pipeline/notion-pull-state.json"
ENV_FILE="${PIPELINE_ENV_FILE:-/etc/zoom-audio-pipeline/pipeline.env}"

mkdir -p "$(dirname "$LOCK_FILE")" "$LOG_DIR" "$INBOX_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

PIPELINE_PYTHON="${PIPELINE_PYTHON:-python3}"
TRANSCRIPTION_PROVIDER="${TRANSCRIPTION_PROVIDER:-openrouter}"
OPENROUTER_STT_MODEL="${OPENROUTER_STT_MODEL:-openai/whisper-large-v3-turbo}"
OPENROUTER_STT_RETRIES="${OPENROUTER_STT_RETRIES:-3}"
OPENROUTER_STT_RETRY_DELAY="${OPENROUTER_STT_RETRY_DELAY:-3}"
OPENROUTER_STT_COMPRESS_THRESHOLD_MB="${OPENROUTER_STT_COMPRESS_THRESHOLD_MB:-20}"
OPENROUTER_STT_FALLBACK="${OPENROUTER_STT_FALLBACK:-local}"
LOCAL_STT_MODEL="${LOCAL_STT_MODEL:-tiny}"
LOCAL_STT_DEVICE="${LOCAL_STT_DEVICE:-cpu}"
LOCAL_STT_COMPUTE_TYPE="${LOCAL_STT_COMPUTE_TYPE:-int8}"
LOCAL_STT_LANGUAGE="${LOCAL_STT_LANGUAGE:-ru}"
TRANSCRIPTION_STALE_AFTER_SEC="${TRANSCRIPTION_STALE_AFTER_SEC:-900}"
VERIFICATION_SCRIPT="${VERIFICATION_SCRIPT:-/usr/local/bin/generate-verification-with-openrouter}"
ARTICLE_SCRIPT="${ARTICLE_SCRIPT:-/usr/local/bin/generate-article-with-gemini-rewrite}"
ARTICLE_DRAFT_SCRIPT="${ARTICLE_DRAFT_SCRIPT:-/usr/local/bin/generate-article-with-openrouter}"
export CODEX_ARTICLE_SCRIPT="${CODEX_ARTICLE_SCRIPT:-$ARTICLE_DRAFT_SCRIPT}"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -Is) runner already active" >> "$LOG_DIR/runner.log"
  exit 0
fi

{
  echo "$(date -Is) telegram-notion-archive-worker start"
  /usr/local/bin/telegram-notion-archive-worker --env-file "$ENV_FILE" || true
  echo "$(date -Is) telegram-notion-archive-worker done"
  echo "$(date -Is) notion-pull-audio start"
  /usr/local/bin/notion-pull-audio --env-file "$ENV_FILE" --output-dir "$INBOX_DIR" --state-file "$STATE_FILE"
  echo "$(date -Is) notion-pull-audio done"
  echo "$(date -Is) process-new-audio start"
  "$PIPELINE_PYTHON" /usr/local/bin/process-new-audio \
    --transcription-provider "$TRANSCRIPTION_PROVIDER" \
    --openrouter-model "$OPENROUTER_STT_MODEL" \
    --openrouter-retries "$OPENROUTER_STT_RETRIES" \
    --openrouter-retry-delay "$OPENROUTER_STT_RETRY_DELAY" \
    --openrouter-compress-threshold-mb "$OPENROUTER_STT_COMPRESS_THRESHOLD_MB" \
    --openrouter-fallback "$OPENROUTER_STT_FALLBACK" \
    --model "$LOCAL_STT_MODEL" \
    --device "$LOCAL_STT_DEVICE" \
    --compute-type "$LOCAL_STT_COMPUTE_TYPE" \
    --language "$LOCAL_STT_LANGUAGE" \
    --transcribing-stale-after-sec "$TRANSCRIPTION_STALE_AFTER_SEC" \
    --verification-script "$VERIFICATION_SCRIPT" \
    --notify-script /usr/local/bin/telegram-roadmap-notify
  echo "$(date -Is) process-new-audio done"
  echo "$(date -Is) process-approved-roadmaps start"
  "$PIPELINE_PYTHON" /usr/local/bin/process-approved-roadmaps --article-script "$ARTICLE_SCRIPT"
  echo "$(date -Is) process-approved-roadmaps done"
  echo "$(date -Is) telegram-intake-cleanup start"
  /usr/local/bin/telegram-intake-cleanup --apply || true
  echo "$(date -Is) telegram-intake-cleanup done"
} >> "$LOG_DIR/runner.log" 2>&1
