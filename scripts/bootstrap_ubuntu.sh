#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: curl ... | sudo bash" >&2
  exit 2
fi

REPO_URL="${REPO_URL:-https://github.com/Kirill-NMF/stud_roadmap_bot.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/stud_roadmap_bot}"
BRANCH="${BRANCH:-main}"

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git ca-certificates
else
  echo "This bootstrap currently supports Ubuntu/Debian servers with apt-get." >&2
  exit 2
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
  rm -rf "$INSTALL_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

bash "$INSTALL_DIR/scripts/install_vps.sh"

cat <<'EOF'

Bootstrap complete.

Next commands:

sudo nano /etc/zoom-audio-pipeline/pipeline.env
sudo roadmap-pipeline-doctor
sudo systemctl enable --now telegram-roadmap-webhook.service notion-webhook-receiver.service notion-pipeline-poll.timer

Then configure HTTPS reverse proxy and Telegram/Notion webhook URLs.
See docs/HANDOFF_DEPLOY.md in the repository.
EOF

