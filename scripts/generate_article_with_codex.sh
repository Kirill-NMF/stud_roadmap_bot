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
LOG="$RUN_DIR/codex-article.log"
LAST="$RUN_DIR/codex-article-last-message.md"
STATUS="$RUN_DIR/status.json"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "run dir does not exist: $RUN_DIR" >&2
  exit 2
fi

if [[ ! -s "$TRANSCRIPT" ]]; then
  echo "transcript not found or empty: $TRANSCRIPT" >&2
  exit 2
fi

if [[ ! -s "$VERIFICATION" ]]; then
  echo "verification not found or empty: $VERIFICATION" >&2
  exit 2
fi

if [[ ! -s "$PROMPT_TEMPLATE" ]]; then
  echo "prompt template not found or empty: $PROMPT_TEMPLATE" >&2
  exit 2
fi

python3 - "$STATUS" <<'PY'
import json, sys
from datetime import datetime, timezone
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
data["article_status"] = "started"
data["article_started_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
for key in ("article_failed_at", "article_done_at", "article", "article_bytes", "html", "html_bytes"):
    data.pop(key, None)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

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

CODEX_INPUT="$(cat "$PROMPT")"

if ! timeout "${CODEX_ARTICLE_TIMEOUT_SECONDS:-900}" codex --disable shell_tool -a never exec \
  --skip-git-repo-check \
  --cd "$RUN_DIR" \
  --sandbox read-only \
  --output-last-message "$LAST" \
  "$CODEX_INPUT" </dev/null > "$LOG" 2>&1; then
  python3 - "$STATUS" "$LOG" <<'PY'
import json, sys
from datetime import datetime, timezone
status_path, log_path = sys.argv[1], sys.argv[2]
try:
    with open(status_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
data["article_status"] = "failed"
data["article_failed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
data["article_log"] = log_path
for key in ("article_done_at", "article", "article_bytes", "html", "html_bytes"):
    data.pop(key, None)
with open(status_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  echo "codex article generation failed; see $LOG" >&2
  exit 1
fi

if [[ ! -s "$LAST" ]]; then
  echo "codex did not produce a final message; see $LOG" >&2
  exit 1
fi

cp "$LAST" "$OUT"

if command -v roadmap-markdown-to-html >/dev/null 2>&1; then
  roadmap-markdown-to-html "$OUT" -o "$HTML_OUT"
fi

python3 - "$STATUS" "$OUT" "$HTML_OUT" <<'PY'
import json, os, sys
from datetime import datetime, timezone
status_path, out_path, html_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(status_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
data["article_status"] = "done"
data["article_done_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
data["article"] = out_path
data["article_bytes"] = os.path.getsize(out_path)
if os.path.exists(html_path):
    data["html"] = html_path
    data["html_bytes"] = os.path.getsize(html_path)
data.pop("article_failed_at", None)
data.pop("article_log", None)
with open(status_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

printf '%s\n' "$OUT"
if [[ -s "$HTML_OUT" ]]; then
  printf '%s\n' "$HTML_OUT"
fi
