#!/usr/bin/env bash
set -euo pipefail

REPO="blooddrunk/vps-traffic-alert"
BRANCH="${VPS_TRAFFIC_ALERT_BRANCH:-main}"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"

curl -fsSL "$RAW_BASE/install-core.sh" | bash -s -- "$@"
