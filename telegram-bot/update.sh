#!/usr/bin/env bash
set -euo pipefail

REPO="blooddrunk/vps-traffic-alert"
BRANCH="${VPS_TRAFFIC_ALERT_BRANCH:-main}"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  printf 'ERROR: Run as root: curl -fsSL %s/telegram-bot/update.sh | sudo bash\n' "$RAW_BASE" >&2
  exit 1
fi

curl -fsSL "$RAW_BASE/telegram-bot/install.sh" | bash -s -- --update-only
