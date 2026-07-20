#!/usr/bin/env bash
set -euo pipefail

REPO="blooddrunk/vps-traffic-alert"
BRANCH="${VPS_TRAFFIC_ALERT_BRANCH:-main}"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"
UPDATE_ONLY=false

if [[ "${1:-}" == "--update-only" ]]; then
  UPDATE_ONLY=true
fi

say() {
  printf '%s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

confirm_tty() {
  local label="$1" default_answer="${2:-n}" answer
  if [[ "$default_answer" == "y" ]]; then
    read -r -p "$label [Y/n]: " answer < /dev/tty
    answer="${answer:-y}"
  else
    read -r -p "$label [y/N]: " answer < /dev/tty
    answer="${answer:-n}"
  fi
  [[ "$answer" =~ ^[Yy]$ ]]
}

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  fail "Run as root: curl -fsSL $RAW_BASE/install.sh | sudo bash"
fi

command -v systemctl >/dev/null 2>&1 || fail "systemd is required"

install_dependencies() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y vnstat python3 curl ca-certificates iproute2
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y vnstat python3 curl ca-certificates iproute
  elif command -v yum >/dev/null 2>&1; then
    yum install -y epel-release >/dev/null 2>&1 || true
    yum install -y vnstat python3 curl ca-certificates iproute
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm vnstat python curl ca-certificates iproute2
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install vnstat python3 curl ca-certificates iproute2
  else
    fail "Unsupported package manager. Install vnstat, python3, curl, and iproute2 manually."
  fi
}

download() {
  local source_path="$1"
  local destination="$2"
  local mode="$3"
  local temporary
  temporary=$(mktemp)
  curl -fsSL "$RAW_BASE/$source_path" -o "$temporary"
  [[ -s "$temporary" ]] || fail "Downloaded file is empty: $source_path"
  install -D -m "$mode" "$temporary" "$destination"
  rm -f "$temporary"
}

patch_management_command() {
  local target="/usr/local/bin/vps-traffic-alert"
  python3 - "$target" <<'PY_PATCH'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
needle = '''  confirm "Send a Telegram test message now?" y && python3 "$ENGINE" test || true
}'''
replacement = '''  confirm "Send a Telegram test message now?" y && python3 "$ENGINE" test || true

  if confirm "Enable automatic checks every 15 minutes now?" y; then
    cmd_enable
  else
    cmd_disable
    say "Automatic checks remain disabled. Enable them later with: sudo $PROGRAM enable"
  fi
}'''
if needle not in text:
    raise SystemExit("Could not patch configure flow: expected block not found")
text = text.replace(needle, replacement, 1)
text = text.replace('VERSION="0.2.0"', 'VERSION="0.2.1"', 1)
path.write_text(text, encoding="utf-8")
PY_PATCH
  chmod 755 "$target"
}

install_dependencies

systemctl enable --now vnstat >/dev/null 2>&1 || true
install -d -m 755 /usr/local/lib/vps-traffic-alert
install -d -m 700 /etc/vps-traffic-alert /var/lib/vps-traffic-alert

download "src/traffic_alert.py" "/usr/local/lib/vps-traffic-alert/traffic_alert.py" 755
download "vps-traffic-alert" "/usr/local/bin/vps-traffic-alert" 755
patch_management_command
download "systemd/vps-traffic-alert.service" "/etc/systemd/system/vps-traffic-alert.service" 644
download "systemd/vps-traffic-alert.timer" "/etc/systemd/system/vps-traffic-alert.timer" 644
download "config.example.json" "/usr/local/lib/vps-traffic-alert/config.example.json" 644

systemctl daemon-reload

if [[ "$UPDATE_ONLY" == true ]]; then
  if systemctl is-enabled vps-traffic-alert.timer >/dev/null 2>&1; then
    systemctl restart vps-traffic-alert.timer
  fi
  say "VPS Traffic Alert updated successfully"
  exit 0
fi

say "VPS Traffic Alert installed successfully"
say ""

if [[ -f /etc/vps-traffic-alert/config.json ]]; then
  say "Existing configuration detected and preserved."
  if [[ -r /dev/tty ]]; then
    if confirm_tty "Enable automatic checks every 15 minutes now?" y; then
      /usr/local/bin/vps-traffic-alert enable
    else
      /usr/local/bin/vps-traffic-alert disable
      say "Automatic checks remain disabled. Enable them later with: sudo vps-traffic-alert enable"
    fi
  else
    say "No interactive terminal detected. Automatic-check state was preserved."
  fi
  say "Run: sudo vps-traffic-alert status"
  exit 0
fi

if [[ -r /dev/tty ]]; then
  /usr/local/bin/vps-traffic-alert configure < /dev/tty
  /usr/local/bin/vps-traffic-alert check
else
  say "No interactive terminal detected. Configure later with:"
  say "  sudo vps-traffic-alert configure"
fi
