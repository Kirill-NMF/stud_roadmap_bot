#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: generate-verification-with-openrouter RUN_DIR" >&2
  exit 2
fi

SCRIPT="${OPENROUTER_ROADMAP_GENERATOR:-/usr/local/bin/openrouter-roadmap-generate}"
exec "$SCRIPT" "$1" \
  --mode verification \
  --model "${OPENROUTER_VERIFICATION_MODEL:-${OPENROUTER_ROADMAP_MODEL:-openai/gpt-5.5}}" \
  --verification-prompt "${CODEX_VERIFICATION_PROMPT:-/opt/zoom-audio-pipeline/prompts/consultation_verification_prompt.md}"
