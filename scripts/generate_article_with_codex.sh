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
HEDGE_RESULT="$RUN_DIR/codex-article-hedge-result.json"

CODEX_BIN="${CODEX_BIN:-codex}"
MODEL="${CODEX_ARTICLE_MODEL:-gpt-5.6-terra}"
REASONING="${CODEX_ARTICLE_REASONING_EFFORT:-high}"
HEDGE_RUNNER="${CODEX_ARTICLE_HEDGE_RUNNER:-/usr/local/bin/run-codex-article-hedge}"
HEDGE_DELAY_SECONDS="${CODEX_ARTICLE_HEDGE_DELAY_SECONDS:-65}"
FALLBACK_AFTER_SECONDS="${CODEX_ARTICLE_FALLBACK_AFTER_SECONDS:-145}"
PRIMARY_TIMEOUT_SECONDS="${CODEX_ARTICLE_PRIMARY_TIMEOUT_SECONDS:-900}"
HEDGE_TIMEOUT_SECONDS="${CODEX_ARTICLE_HEDGE_TIMEOUT_SECONDS:-${CODEX_ARTICLE_TIMEOUT_SECONDS:-80}}"
FALLBACK_TIMEOUT_SECONDS="${CODEX_ARTICLE_FALLBACK_TIMEOUT_SECONDS:-900}"
POLL_INTERVAL_SECONDS="${CODEX_ARTICLE_POLL_INTERVAL_SECONDS:-0.25}"
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

if [[ ! -x "$FALLBACK_SCRIPT" ]]; then
  echo "OpenRouter article fallback is not executable: $FALLBACK_SCRIPT" >&2
  exit 2
fi
if [[ ! -f "$HEDGE_RUNNER" ]]; then
  echo "Codex article hedge runner was not found: $HEDGE_RUNNER" >&2
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
  "article_hedge_status=waiting" \
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

set +e
python3 "$HEDGE_RUNNER" "$RUN_DIR" \
  --prompt "$PROMPT" \
  --output "$OUT" \
  --last-output "$LAST" \
  --result "$HEDGE_RESULT" \
  --codex-bin "$CODEX_BIN" \
  --model "$MODEL" \
  --reasoning "$REASONING" \
  --fallback-script "$FALLBACK_SCRIPT" \
  --hedge-delay "$HEDGE_DELAY_SECONDS" \
  --fallback-after "$FALLBACK_AFTER_SECONDS" \
  --primary-timeout "$PRIMARY_TIMEOUT_SECONDS" \
  --hedge-timeout "$HEDGE_TIMEOUT_SECONDS" \
  --fallback-timeout "$FALLBACK_TIMEOUT_SECONDS" \
  --poll-interval "$POLL_INTERVAL_SECONDS" \
  --minimum-bytes "$MIN_OUTPUT_BYTES"
RACE_EXIT=$?
set -e

if [[ "$RACE_EXIT" -ne 0 ]] || [[ ! -s "$OUT" ]] || [[ ! -s "$HEDGE_RESULT" ]]; then
  DURATION="$(( $(date +%s) - STARTED_AT_EPOCH ))"
  python3 - "$STATUS" "$HEDGE_RESULT" "$DURATION" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

status_path, result_path = map(Path, sys.argv[1:3])
duration = int(sys.argv[3])
try:
    status = json.loads(status_path.read_text(encoding="utf-8"))
except Exception:
    status = {}
try:
    result = json.loads(result_path.read_text(encoding="utf-8"))
except Exception:
    result = {}
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
status.update({
    "article_status": "failed",
    "article_generation_provider": "failed",
    "article_generation_duration_seconds": duration,
    "article_codex_attempts": int(result.get("codex_attempts_started", 0)),
    "article_hedge_status": "failed",
    "article_fallback_started": bool(result.get("fallback_started", False)),
    "article_codex_last_error": result.get("last_error", "hedge_runner_failed"),
    "article_failed_at": now,
    "article_pipeline_updated_at": now,
})
temporary = status_path.with_suffix(status_path.suffix + ".tmp")
temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(status_path)
PY
  echo "Codex hedge and OpenRouter fallback returned no valid article" >&2
  exit 1
fi

if command -v "$MARKDOWN_TO_HTML" >/dev/null 2>&1; then
  "$MARKDOWN_TO_HTML" "$OUT" -o "$HTML_OUT"
fi

python3 - "$STATUS" "$HEDGE_RESULT" "$OUT" "$HTML_OUT" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

status_path, result_path, article_path, html_path = map(Path, sys.argv[1:])
try:
    status = json.loads(status_path.read_text(encoding="utf-8"))
except Exception:
    status = {}
result = json.loads(result_path.read_text(encoding="utf-8"))
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
provider = result["provider"]
status.update({
    "article_status": "done",
    "article_generation_provider": provider,
    "article_generation_duration_seconds": int(round(float(result["duration_seconds"]))),
    "article_codex_attempts": int(result["codex_attempts_started"]),
    "article_hedge_status": "won" if result["winner"] == "codex_hedge" else "completed",
    "article_hedge_winner": result["winner"],
    "article_fallback_started": bool(result["fallback_started"]),
    "article_log": result["winner_log"],
    "article_done_at": now,
    "article": str(article_path),
    "article_bytes": article_path.stat().st_size,
})
if html_path.exists():
    status["html"] = str(html_path)
    status["html_bytes"] = html_path.stat().st_size
if provider == "openrouter_fallback":
    status["article_fallback_reason"] = "codex_no_valid_result_before_fallback"
else:
    status.pop("article_fallback_reason", None)
status.pop("article_codex_last_error", None)
status.pop("article_failed_at", None)
status["article_pipeline_updated_at"] = now
temporary = status_path.with_suffix(status_path.suffix + ".tmp")
temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(status_path)
PY

printf '%s\n' "$OUT"
if [[ -s "$HTML_OUT" ]]; then
  printf '%s\n' "$HTML_OUT"
fi
