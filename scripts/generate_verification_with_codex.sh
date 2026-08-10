#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: generate-verification-with-codex RUN_DIR" >&2
  exit 2
fi

RUN_DIR="$1"
TRANSCRIPT="$RUN_DIR/transcript-plain.txt"
if [[ ! -s "$TRANSCRIPT" ]]; then
  TRANSCRIPT="$RUN_DIR/transcript.md"
fi
PROMPT_TEMPLATE="${CODEX_VERIFICATION_PROMPT:-/opt/zoom-audio-pipeline/prompts/consultation_verification_prompt.md}"
OUT="$RUN_DIR/verification.md"
PROMPT="$RUN_DIR/codex-verification.prompt.md"
LOG="$RUN_DIR/codex-verification.log"
LAST="$RUN_DIR/codex-verification-last-message.md"
STATUS="$RUN_DIR/status.json"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "run dir does not exist: $RUN_DIR" >&2
  exit 2
fi

if [[ ! -s "$TRANSCRIPT" ]]; then
  echo "transcript not found or empty: $TRANSCRIPT" >&2
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
data["verification_status"] = "started"
data["verification_started_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
for key in ("verification_failed_at", "verification_done_at", "verification", "verification_bytes"):
    data.pop(key, None)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

{
  cat "$PROMPT_TEMPLATE"
  printf '\n\n# Транскрипт консультации\n\n'
  cat "$TRANSCRIPT"
} > "$PROMPT"

CODEX_INPUT="$(cat "$PROMPT")"

if ! timeout "${CODEX_VERIFICATION_TIMEOUT_SECONDS:-900}" codex --disable shell_tool -a never exec \
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
data["verification_status"] = "failed"
data["verification_failed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
data["verification_log"] = log_path
for key in ("verification_done_at", "verification", "verification_bytes"):
    data.pop(key, None)
with open(status_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  echo "codex verification failed; see $LOG" >&2
  exit 1
fi

if [[ ! -s "$LAST" ]]; then
  echo "codex did not produce a final message; see $LOG" >&2
  exit 1
fi

cp "$LAST" "$OUT"

python3 - "$STATUS" "$OUT" <<'PY'
import json, os, sys
from datetime import datetime, timezone
status_path, out_path = sys.argv[1], sys.argv[2]
try:
    with open(status_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
data["verification_status"] = "done"
data["verification_done_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
data["verification"] = out_path
data["verification_bytes"] = os.path.getsize(out_path)
data.pop("verification_failed_at", None)
data.pop("verification_log", None)
with open(status_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

printf '%s\n' "$OUT"
