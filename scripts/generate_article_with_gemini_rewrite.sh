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
GEMINI_DIR="$RUN_DIR/gemini-rewrite"
GEMINI_FINAL="$GEMINI_DIR/final.md"
GEMINI_LOG="$RUN_DIR/gemini-rewrite.log"
GEMINI_VALIDATE_LOG="$RUN_DIR/gemini-rewrite-validate.log"

CODEX_ARTICLE_SCRIPT="${CODEX_ARTICLE_SCRIPT:-/usr/local/bin/generate-article-with-codex}"
GEMINI_REWRITE_SCRIPT="${GEMINI_REWRITE_SCRIPT:-/usr/local/bin/openrouter-gemini-chat-chain}"
GEMINI_MODEL="${GEMINI_REWRITE_MODEL:-google/gemini-2.5-pro}"
GEMINI_TIMEOUT_SECONDS="${GEMINI_REWRITE_TIMEOUT_SECONDS:-1200}"
GEMINI_PRODUCTION_SAFE="${GEMINI_REWRITE_PRODUCTION_SAFE:-1}"
MARKDOWN_TO_HTML="${ROADMAP_MARKDOWN_TO_HTML:-roadmap-markdown-to-html}"

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
    "gemini_rewrite_status=failed" \
    "gemini_rewrite_failed_reason=$message" \
    "gemini_rewrite_log=$GEMINI_LOG" \
    "gemini_rewrite_validate_log=$GEMINI_VALIDATE_LOG"
}

update_status "article_pipeline=codex_then_gemini_rewrite" "article_status=started"

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

update_status \
  "article_status=rewriting" \
  "article_draft_status=done" \
  "article_draft=$DRAFT" \
  "gemini_rewrite_status=started" \
  "gemini_rewrite_model=$GEMINI_MODEL" \
  "gemini_rewrite_dir=$GEMINI_DIR"

mkdir -p "$GEMINI_DIR"

GEMINI_ARGS=("$DRAFT" --save-dir "$GEMINI_DIR" -m "$GEMINI_MODEL")
if [[ "$GEMINI_PRODUCTION_SAFE" != "0" ]]; then
  GEMINI_ARGS+=(--production-safe)
fi

if ! timeout "$GEMINI_TIMEOUT_SECONDS" "$GEMINI_REWRITE_SCRIPT" "${GEMINI_ARGS[@]}" > "$GEMINI_LOG" 2>&1; then
  fail_status "gemini_rewrite_command_failed"
  echo "Gemini rewrite failed; see $GEMINI_LOG" >&2
  exit 1
fi

if [[ ! -s "$GEMINI_FINAL" ]]; then
  fail_status "gemini_final_missing"
  echo "Gemini rewrite did not produce $GEMINI_FINAL" >&2
  exit 1
fi

if ! python3 - "$DRAFT" "$GEMINI_FINAL" > "$GEMINI_VALIDATE_LOG" 2>&1 <<'PY'
import re
import sys
from pathlib import Path

draft_path = Path(sys.argv[1])
final_path = Path(sys.argv[2])
draft = draft_path.read_text(encoding="utf-8")
final = final_path.read_text(encoding="utf-8")

def headings(markdown: str) -> list[str]:
    return [line.strip() for line in markdown.splitlines() if re.match(r"^#{1,6}\s+\S", line.strip())]

draft_headings = headings(draft)
final_headings = headings(final)
if draft_headings != final_headings:
    print("heading mismatch", file=sys.stderr)
    print("draft:", draft_headings, file=sys.stderr)
    print("final:", final_headings, file=sys.stderr)
    raise SystemExit(1)

if "|" in draft and "|" not in final:
    print("draft has a Markdown table but final does not", file=sys.stderr)
    raise SystemExit(1)

markers = [
    "Progress.me",
    "YouTube",
    "A0",
    "A1",
    "A2",
    "B1",
    "B2",
]
for marker in markers:
    if marker in draft and marker not in final:
        print(f"required marker disappeared: {marker}", file=sys.stderr)
        raise SystemExit(1)

p_code_pattern = re.compile(r"\bP(?:1[0-4]|[1-9])\b")
draft_p_codes = set(p_code_pattern.findall(draft))
final_p_codes = set(p_code_pattern.findall(final))
extra_p_codes = final_p_codes - draft_p_codes
if extra_p_codes:
    print(f"unexpected P-codes added: {sorted(extra_p_codes)}", file=sys.stderr)
    raise SystemExit(1)

if len(final.strip()) < max(400, int(len(draft.strip()) * 0.45)):
    print("final rewrite is unexpectedly short", file=sys.stderr)
    raise SystemExit(1)

print("ok")
PY
then
  fail_status "gemini_validation_failed"
  echo "Gemini rewrite validation failed; see $GEMINI_VALIDATE_LOG" >&2
  exit 1
fi

cp "$GEMINI_FINAL" "$ARTICLE"

if command -v "$MARKDOWN_TO_HTML" >/dev/null 2>&1; then
  "$MARKDOWN_TO_HTML" "$ARTICLE" -o "$ARTICLE_HTML"
fi

python3 - "$STATUS" "$ARTICLE" "$ARTICLE_HTML" "$DRAFT" "$GEMINI_FINAL" "$GEMINI_DIR" "$GEMINI_MODEL" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

status_path, article_path, html_path, draft_path, gemini_final, gemini_dir, model = sys.argv[1:]
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
