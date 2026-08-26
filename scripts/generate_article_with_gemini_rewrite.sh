#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: generate-article-with-gemini-rewrite RUN_DIR" >&2
  exit 2
fi

RUN_DIR="$1"
STATUS="$RUN_DIR/status.json"
ARTICLE="$RUN_DIR/roadmap-article.md"
ARTICLE_HTML="$RUN_DIR/roadmap-article.html"
DRAFT="$RUN_DIR/roadmap-article-draft.md"
DRAFT_HTML="$RUN_DIR/roadmap-article-draft.html"
NOTES="$RUN_DIR/teacher-notes.md"
GEMINI_DIR="$RUN_DIR/gemini-rewrite"
GEMINI_FINAL="$GEMINI_DIR/final.md"
GEMINI_LOG="$RUN_DIR/gemini-rewrite.log"
GEMINI_VALIDATE_LOG="$RUN_DIR/gemini-rewrite-validate.log"
GEMINI_VALIDATE_REPORT="$RUN_DIR/gemini-rewrite-validation.json"

CODEX_ARTICLE_SCRIPT="${CODEX_ARTICLE_SCRIPT:-/usr/local/bin/generate-article-with-codex}"
GEMINI_REWRITE_SCRIPT="${GEMINI_REWRITE_SCRIPT:-/usr/local/bin/openrouter-gemini-chat-chain}"
GEMINI_MODEL="${GEMINI_REWRITE_MODEL:-google/gemini-2.5-pro}"
GEMINI_TIMEOUT_SECONDS="${GEMINI_REWRITE_TIMEOUT_SECONDS:-1200}"
GEMINI_MAX_TOKENS="${GEMINI_REWRITE_MAX_TOKENS:-9000}"
GEMINI_PRODUCTION_SAFE="${GEMINI_REWRITE_PRODUCTION_SAFE:-1}"
MARKDOWN_TO_HTML="${ROADMAP_MARKDOWN_TO_HTML:-roadmap-markdown-to-html}"
GEMINI_VALIDATOR="${GEMINI_REWRITE_VALIDATOR:-/usr/local/bin/validate-gemini-rewrite}"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "run dir does not exist: $RUN_DIR" >&2
  exit 2
fi

if [[ ! -x "$CODEX_ARTICLE_SCRIPT" ]]; then
  echo "codex article script is not executable: $CODEX_ARTICLE_SCRIPT" >&2
  exit 2
fi

if [[ ! -x "$GEMINI_REWRITE_SCRIPT" ]]; then
  echo "Gemini rewrite script is not executable: $GEMINI_REWRITE_SCRIPT" >&2
  exit 2
fi

if [[ ! -x "$GEMINI_VALIDATOR" ]]; then
  echo "Gemini validator is not executable: $GEMINI_VALIDATOR" >&2
  exit 2
fi

update_status() {
  python3 - "$STATUS" "$@" <<'PY'
import json, sys
from datetime import datetime, timezone

path = sys.argv[1]
pairs = sys.argv[2:]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    data = {}

now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
for pair in pairs:
    key, value = pair.split("=", 1)
    if value == "__DELETE__":
        data.pop(key, None)
    else:
        data[key] = value
data["article_pipeline_updated_at"] = now

with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

fail_status() {
  local message="$1"
  update_status \
    "article_status=failed" \
    "article_done_at=__DELETE__" \
    "gemini_rewrite_status=failed" \
    "gemini_rewrite_done_at=__DELETE__" \
    "gemini_rewrite_failed_reason=$message" \
    "gemini_rewrite_log=$GEMINI_LOG" \
    "gemini_rewrite_validate_log=$GEMINI_VALIDATE_LOG"
}

update_status \
  "article_pipeline=codex_then_gemini_rewrite" \
  "article_status=started" \
  "article_done_at=__DELETE__" \
  "article_failed_at=__DELETE__" \
  "gemini_rewrite_done_at=__DELETE__" \
  "gemini_rewrite_failed_reason=__DELETE__"

if [[ -s "$DRAFT" && ( ! -s "$NOTES" || "$DRAFT" -nt "$NOTES" ) ]]; then
  cp "$DRAFT" "$ARTICLE"
  if [[ -s "$DRAFT_HTML" ]]; then
    cp "$DRAFT_HTML" "$ARTICLE_HTML"
  fi
  update_status "article_draft_reused=true"
else
  "$CODEX_ARTICLE_SCRIPT" "$RUN_DIR"

  if [[ ! -s "$ARTICLE" ]]; then
    fail_status "codex_article_missing"
    echo "Codex article did not produce $ARTICLE" >&2
    exit 1
  fi

  cp "$ARTICLE" "$DRAFT"
  if [[ -s "$ARTICLE_HTML" ]]; then
    cp "$ARTICLE_HTML" "$DRAFT_HTML"
  fi
fi

update_status \
  "article_status=rewriting" \
  "article_draft_status=done" \
  "article_draft=$DRAFT" \
  "gemini_rewrite_status=started" \
  "gemini_rewrite_model=$GEMINI_MODEL" \
  "gemini_rewrite_dir=$GEMINI_DIR"

mkdir -p "$GEMINI_DIR"

FORCE_GEMINI_RETRY="$(python3 - "$STATUS" <<'PY'
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    data = {}
print("1" if data.get("gemini_force_retry") else "0")
PY
)"

if [[ -s "$GEMINI_FINAL" && "$GEMINI_FINAL" -nt "$DRAFT" && "$FORCE_GEMINI_RETRY" != "1" ]]; then
  update_status "gemini_rewrite_reused=true"
else
  update_status "gemini_force_retry=__DELETE__" "gemini_rewrite_reused=__DELETE__"
  GEMINI_ARGS=("$DRAFT" --save-dir "$GEMINI_DIR" -m "$GEMINI_MODEL")
  GEMINI_ARGS+=(--max-tokens "$GEMINI_MAX_TOKENS")
  if [[ "$GEMINI_PRODUCTION_SAFE" != "0" ]]; then
    GEMINI_ARGS+=(--production-safe)
  fi

  if ! timeout "$GEMINI_TIMEOUT_SECONDS" "$GEMINI_REWRITE_SCRIPT" "${GEMINI_ARGS[@]}" > "$GEMINI_LOG" 2>&1; then
    fail_status "gemini_rewrite_command_failed"
    echo "Gemini rewrite failed; see $GEMINI_LOG" >&2
    exit 1
  fi
fi

if [[ ! -s "$GEMINI_FINAL" ]]; then
  fail_status "gemini_final_missing"
  echo "Gemini rewrite did not produce $GEMINI_FINAL" >&2
  exit 1
fi

if ! "$GEMINI_VALIDATOR" "$DRAFT" "$GEMINI_FINAL" --report "$GEMINI_VALIDATE_REPORT" > "$GEMINI_VALIDATE_LOG" 2>&1; then
  fail_status "gemini_validation_failed"
  update_status \
    "article_status=recovery_required" \
    "article_recovery_status=awaiting_choice" \
    "article_recovery_reason=gemini_validation_failed" \
    "article_validation_report=$GEMINI_VALIDATE_REPORT" \
    "article_retry_pending=false" \
    "article_next_retry_at=__DELETE__" \
    "article_next_retry_at_epoch=__DELETE__"
  echo "Gemini rewrite validation failed; see $GEMINI_VALIDATE_LOG" >&2
  exit 3
fi

cp "$GEMINI_FINAL" "$ARTICLE"

if command -v "$MARKDOWN_TO_HTML" >/dev/null 2>&1; then
  if ! "$MARKDOWN_TO_HTML" "$ARTICLE" -o "$ARTICLE_HTML"; then
    fail_status "article_html_render_failed"
    update_status \
      "article_status=recovery_required" \
      "article_recovery_status=awaiting_choice" \
      "article_recovery_reason=article_html_render_failed" \
      "article_retry_pending=false" \
      "article_next_retry_at=__DELETE__" \
      "article_next_retry_at_epoch=__DELETE__"
    echo "Final article HTML rendering failed" >&2
    exit 3
  fi
fi

python3 - "$STATUS" "$ARTICLE" "$ARTICLE_HTML" "$DRAFT" "$GEMINI_FINAL" "$GEMINI_DIR" "$GEMINI_MODEL" "$GEMINI_VALIDATE_REPORT" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

status_path, article_path, html_path, draft_path, gemini_final, gemini_dir, model, validation_report = sys.argv[1:]
try:
    with open(status_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    data = {}

now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
data["article_status"] = "done"
data["article_done_at"] = now
data["article_source"] = "gemini_rewrite"
data["article"] = article_path
data["article_bytes"] = os.path.getsize(article_path)
data["article_draft_status"] = "done"
data["article_draft"] = draft_path
data["article_draft_bytes"] = os.path.getsize(draft_path)
data["gemini_rewrite_status"] = "done"
data["gemini_rewrite_done_at"] = now
data["gemini_rewrite_model"] = model
data["gemini_rewrite_dir"] = gemini_dir
data["gemini_rewrite_final"] = gemini_final
data["gemini_rewrite_final_bytes"] = os.path.getsize(gemini_final)
try:
    with open(validation_report, "r", encoding="utf-8") as handle:
        validation = json.load(handle)
except Exception:
    validation = {"status": "unknown", "warnings": []}
data["article_validation_status"] = validation.get("status", "unknown")
data["article_validation_warnings"] = validation.get("warnings", [])
data["article_validation_report"] = validation_report
data["article_retry_pending"] = False
data.pop("article_recovery_status", None)
data.pop("article_recovery_reason", None)
data.pop("article_recovery_action", None)
data.pop("article_recovery_notified_at", None)
data.pop("gemini_force_retry", None)
data.pop("article_failed_at", None)
data.pop("article_last_error_at", None)
data.pop("gemini_rewrite_failed_reason", None)
if os.path.exists(html_path):
    data["html"] = html_path
    data["html_bytes"] = os.path.getsize(html_path)

with open(status_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY

printf '%s\n' "$ARTICLE"
if [[ -s "$ARTICLE_HTML" ]]; then
  printf '%s\n' "$ARTICLE_HTML"
fi
