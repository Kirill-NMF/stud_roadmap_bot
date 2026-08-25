#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: generate-article-with-openrouter RUN_DIR" >&2
  exit 2
fi

SCRIPT="${OPENROUTER_ROADMAP_GENERATOR:-/usr/local/bin/openrouter-roadmap-generate}"
MAX_TOKENS="${OPENROUTER_ARTICLE_MAX_TOKENS:-10000}"
exec "$SCRIPT" "$1" \
  --mode article \
  --model "${OPENROUTER_ARTICLE_MODEL:-${OPENROUTER_ROADMAP_MODEL:-openai/gpt-5.5}}" \
  --max-tokens "$MAX_TOKENS" \
  --article-prompt "${CODEX_ARTICLE_PROMPT:-/opt/zoom-audio-pipeline/prompts/consultation_article_prompt.md}" \
  --enhancements "${ROADMAP_ENHANCEMENTS_PROMPT:-/opt/zoom-audio-pipeline/prompts/roadmap_enhancement_options.md}"
