#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: generate-article-with-codex RUN_DIR" >&2
  exit 2
fi

RUN_DIR="$1"
TRANSCRIPT="$RUN_DIR/transcript.md"
VERIFICATION="$RUN_DIR/verification.md"
NOTES="$RUN_DIR/teacher-notes.md"
PROMPT_TEMPLATE="${CODEX_ARTICLE_PROMPT:-/opt/zoom-audio-pipeline/prompts/consultation_article_prompt.md}"
ENHANCEMENTS="${ROADMAP_ENHANCEMENTS_PROMPT:-/opt/zoom-audio-pipeline/prompts/roadmap_enhancement_options.md}"
OUT="$RUN_DIR/roadmap-article.md"
HTML_OUT="$RUN_DIR/roadmap-article.html"
PROMPT="$RUN_DIR/codex-article.prompt.md"
LAST="$RUN_DIR/codex-article-last-message.md"
STATUS="$RUN_DIR/status.json"
LOCK="$RUN_DIR/codex-article.lock"

CODEX_BIN="${CODEX_BIN:-codex}"
MODEL="${CODEX_ARTICLE_MODEL:-gpt-5.6-terra}"
REASONING="${CODEX_ARTICLE_REASONING_EFFORT:-high}"
ATTEMPTS="${CODEX_ARTICLE_ATTEMPTS:-2}"
TIMEOUT_SECONDS="${CODEX_ARTICLE_TIMEOUT_SECONDS:-80}"
RETRY_DELAY_SECONDS="${CODEX_ARTICLE_RETRY_DELAY_SECONDS:-3}"
MIN_OUTPUT_BYTES="${CODEX_ARTICLE_MIN_OUTPUT_BYTES:-200}"
FALLBACK_SCRIPT="${CODEX_ARTICLE_FALLBACK_SCRIPT:-/usr/local/bin/generate-article-with-openrouter}"
MARKDOWN_TO_HTML="${ROADMAP_MARKDOWN_TO_HTML:-roadmap-markdown-to-html}"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "run dir does not exist: $RUN_DIR" >&2
  exit 2
fi

for required in "$TRANSCRIPT" "$VERIFICATION" "$PROMPT_TEMPLATE"; do
  if [[ ! -s "$required" ]]; then
    echo "required article input not found or empty: $required" >&2
    exit 2
  fi
done

if [[ ! "$ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || [[ ! "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || \
   [[ ! "$RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]] || [[ ! "$MIN_OUTPUT_BYTES" =~ ^[1-9][0-9]*$ ]]; then
  echo "invalid Codex article retry, timeout, delay, or output-size setting" >&2
  exit 2
fi

if [[ ! -x "$FALLBACK_SCRIPT" ]]; then
  echo "OpenRouter article fallback is not executable: $FALLBACK_SCRIPT" >&2
  exit 2
fi

exec 8>"$LOCK"
if ! flock -n 8; then
  echo "article generation is already active for $RUN_DIR" >&2
  exit 75
fi

update_status() {
  python3 - "$STATUS" "$@" <<'PY'
import json
import sys
from datetime import datetime, timezone

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    data = {}

integer_keys = {"article_codex_attempts", "article_generation_duration_seconds", "article_bytes", "html_bytes"}
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
for pair in sys.argv[2:]:
    key, value = pair.split("=", 1)
    if value == "__DELETE__":
        data.pop(key, None)
    elif value == "__NOW__":
        data[key] = now
    elif key in integer_keys:
        data[key] = int(value)
    else:
        data[key] = value

data["article_pipeline_updated_at"] = now
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

STARTED_AT_EPOCH="$(date +%s)"
update_status \
  "article_status=started" \
  "article_generation_provider=codex_cli_pending" \
  "article_generation_model=$MODEL" \
  "article_generation_reasoning_effort=$REASONING" \
  "article_codex_attempts=0" \
  "article_started_at=__NOW__" \
  "article_fallback_reason=__DELETE__" \
  "article_codex_last_error=__DELETE__" \
  "article_failed_at=__DELETE__" \
  "article_done_at=__DELETE__" \
  "article=__DELETE__" \
  "article_bytes=__DELETE__" \
  "html=__DELETE__" \
  "html_bytes=__DELETE__"

{
  cat "$PROMPT_TEMPLATE"
  printf '\n\n# Первичный анализ и верификация\n\n'
  cat "$VERIFICATION"
  printf '\n\n# Правки преподавателя\n\n'
  if [[ -s "$NOTES" ]]; then
    cat "$NOTES"
  else
    printf 'Правок преподавателя нет.\n'
  fi
  if [[ -s "$ENHANCEMENTS" ]]; then
    printf '\n\n# Справочник PDF-опций P1-P14\n\n'
    cat "$ENHANCEMENTS"
    printf '\n\nИспользуй эти опции только если преподаватель явно подтвердил соответствующий P-код в правках.\n'
  fi
  printf '\n\n# Транскрипт консультации\n\n'
  cat "$TRANSCRIPT"
} > "$PROMPT"

LAST_REASON="codex_cli_failed"
SUCCESS=0
for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
  ATTEMPT_OUT="$(mktemp "$RUN_DIR/.codex-article-attempt.XXXXXX.md")"
  ATTEMPT_LOG="$RUN_DIR/codex-article-attempt-${attempt}.log"

  set +e
  timeout "$TIMEOUT_SECONDS" "$CODEX_BIN" --disable shell_tool -a never exec \
    --skip-git-repo-check \
    --ephemeral \
    --model "$MODEL" \
    -c "model_reasoning_effort=\"$REASONING\"" \
    --cd "$RUN_DIR" \
    --sandbox read-only \
    --output-last-message "$ATTEMPT_OUT" \
    - < "$PROMPT" > "$ATTEMPT_LOG" 2>&1
  EXIT_CODE=$?
  set -e

  if [[ "$EXIT_CODE" -eq 0 ]] && [[ -s "$ATTEMPT_OUT" ]] && \
     [[ "$(wc -c < "$ATTEMPT_OUT")" -ge "$MIN_OUTPUT_BYTES" ]]; then
    mv -f "$ATTEMPT_OUT" "$OUT"
    cp "$OUT" "$LAST"
    SUCCESS=1
    update_status "article_codex_attempts=$attempt" "article_log=$ATTEMPT_LOG"
    break
  fi

  if [[ "$EXIT_CODE" -ne 0 ]]; then
    LAST_REASON="codex_cli_exit_$EXIT_CODE"
  elif [[ ! -s "$ATTEMPT_OUT" ]]; then
    LAST_REASON="codex_cli_empty_output"
  else
    LAST_REASON="codex_cli_obviously_truncated_output"
  fi
  rm -f "$ATTEMPT_OUT"
  update_status "article_codex_attempts=$attempt" "article_log=$ATTEMPT_LOG"
  if [[ "$attempt" -lt "$ATTEMPTS" ]] && [[ "$RETRY_DELAY_SECONDS" -gt 0 ]]; then
    sleep "$RETRY_DELAY_SECONDS"
  fi
done

if [[ "$SUCCESS" -eq 1 ]]; then
  if command -v "$MARKDOWN_TO_HTML" >/dev/null 2>&1; then
    "$MARKDOWN_TO_HTML" "$OUT" -o "$HTML_OUT"
  fi
  DURATION="$(( $(date +%s) - STARTED_AT_EPOCH ))"
  STATUS_ARGS=(
    "article_status=done" \
    "article_generation_provider=codex_cli" \
    "article_generation_duration_seconds=$DURATION" \
    "article_fallback_reason=__DELETE__" \
    "article_failed_at=__DELETE__" \
    "article_done_at=__NOW__" \
    "article=$OUT" \
    "article_bytes=$(wc -c < "$OUT")"
  )
  if [[ -s "$HTML_OUT" ]]; then
    STATUS_ARGS+=("html=$HTML_OUT" "html_bytes=$(wc -c < "$HTML_OUT")")
  fi
  update_status "${STATUS_ARGS[@]}"
else
  FALLBACK_REASON="codex_cli_failed_after_${ATTEMPTS}_attempts"
  update_status \
    "article_generation_provider=openrouter_fallback_pending" \
    "article_fallback_reason=$FALLBACK_REASON" \
    "article_codex_last_error=$LAST_REASON"

  if ! "$FALLBACK_SCRIPT" "$RUN_DIR"; then
    DURATION="$(( $(date +%s) - STARTED_AT_EPOCH ))"
    update_status \
      "article_status=failed" \
      "article_generation_provider=failed" \
      "article_generation_duration_seconds=$DURATION" \
      "article_failed_at=__NOW__"
    echo "Codex CLI and OpenRouter article fallback both failed" >&2
    exit 1
  fi
  if [[ ! -s "$OUT" ]]; then
    update_status \
      "article_status=failed" \
      "article_generation_provider=failed" \
      "article_failed_at=__NOW__"
    echo "OpenRouter article fallback returned no article" >&2
    exit 1
  fi
  DURATION="$(( $(date +%s) - STARTED_AT_EPOCH ))"
  update_status \
    "article_status=done" \
    "article_generation_provider=openrouter_fallback" \
    "article_generation_duration_seconds=$DURATION" \
    "article_fallback_reason=$FALLBACK_REASON" \
    "article_done_at=__NOW__" \
    "article=$OUT" \
    "article_bytes=$(wc -c < "$OUT")"
fi

printf '%s\n' "$OUT"
if [[ -s "$HTML_OUT" ]]; then
  printf '%s\n' "$HTML_OUT"
fi
