#!/usr/bin/env bash
set -euo pipefail

# Install the Telegram controller on one always-on Linux host.
# This script intentionally does not modify remote VPS SSH configuration.

REPO="blooddrunk/vps-traffic-alert"
BRANCH="${VPS_TRAFFIC_ALERT_BRANCH:-main}"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"

BOT_USER="vps-traffic-bot"
BOT_GROUP="vps-traffic-bot"
APP_DIR="/opt/vps-traffic-bot"
CONFIG_DIR="/etc/vps-traffic-alert"
CONFIG_FILE="$CONFIG_DIR/controller.json"
ENV_FILE="$CONFIG_DIR/bot.env"
HISTORY_PATH="/var/lib/vps-traffic-alert/history.jsonl"

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run as root: curl -fsSL $RAW_BASE/telegram-bot/install.sh | sudo bash"
}

require_tty() {
  [[ -r /dev/tty ]] || fail "This installer needs an interactive terminal. Run it directly from a terminal."
}

prompt_tty() {
  local label="$1" default_value="${2:-}" answer
  if [[ -n "$default_value" ]]; then
    read -r -p "$label [$default_value]: " answer < /dev/tty
    printf '%s' "${answer:-$default_value}"
  else
    read -r -p "$label: " answer < /dev/tty
    printf '%s' "$answer"
  fi
}

prompt_secret_tty() {
  local answer
  read -r -s -p "$1: " answer < /dev/tty
  printf '\n' >&2
  printf '%s' "$answer"
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

install_dependencies() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y ca-certificates curl openssh-client python3 python3-venv
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl openssh-clients python3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl openssh-clients python3
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm ca-certificates curl openssh python
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install ca-certificates curl openssh-clients python3
  else
    fail "Unsupported package manager. Install systemd, python3, python3-venv, curl, and an SSH client manually."
  fi
}

ensure_prerequisites() {
  command -v systemctl >/dev/null 2>&1 || fail "systemd is required"
  command -v runuser >/dev/null 2>&1 || fail "runuser is required to run the controller as a dedicated user"
  install_dependencies
  command -v python3 >/dev/null 2>&1 || fail "python3 is not installed"
  command -v ssh >/dev/null 2>&1 || fail "An SSH client is not installed"
  python3 -m venv --help >/dev/null 2>&1 || fail "python3-venv is required to create the controller virtual environment"
}

ensure_bot_user() {
  if ! getent group "$BOT_GROUP" >/dev/null 2>&1; then
    groupadd --system "$BOT_GROUP"
  fi
  if ! id "$BOT_USER" >/dev/null 2>&1; then
    local nologin
    nologin=$(command -v nologin || printf '%s' /usr/sbin/nologin)
    useradd --system --create-home --home-dir "/home/$BOT_USER" --gid "$BOT_GROUP" --shell "$nologin" "$BOT_USER"
  fi
  BOT_GROUP=$(id -gn "$BOT_USER")
}

bot_home() {
  getent passwd "$BOT_USER" | awk -F: '{print $6}'
}

run_as_bot() {
  local home
  home=$(bot_home)
  runuser -u "$BOT_USER" -- env HOME="$home" USER="$BOT_USER" LOGNAME="$BOT_USER" "$@"
}

download() {
  local source_path="$1" destination="$2" mode="$3" temporary
  temporary=$(mktemp)
  curl -fsSL "$RAW_BASE/$source_path" -o "$temporary"
  [[ -s "$temporary" ]] || fail "Downloaded file is empty: $source_path"
  install -o root -g "$BOT_GROUP" -m "$mode" "$temporary" "$destination"
  rm -f "$temporary"
}

install_controller_files() {
  local home
  home=$(bot_home)
  [[ -n "$home" && -d "$home" ]] || fail "Could not determine $BOT_USER home directory"

  install -d -o "$BOT_USER" -g "$BOT_GROUP" -m 750 "$APP_DIR"
  install -d -o root -g "$BOT_GROUP" -m 750 "$CONFIG_DIR"
  install -d -o "$BOT_USER" -g "$BOT_GROUP" -m 700 "$home/.ssh"

  download "telegram-bot/bot.py" "$APP_DIR/bot.py" 640
  download "telegram-bot/requirements.txt" "$APP_DIR/requirements.txt" 640
  download "telegram-bot/vps-traffic-bot.service" "/etc/systemd/system/vps-traffic-bot.service" 644
  download "telegram-bot/vps-traffic-report.service" "/etc/systemd/system/vps-traffic-report.service" 644
  download "telegram-bot/vps-traffic-report.timer" "/etc/systemd/system/vps-traffic-report.timer" 644

  if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
    run_as_bot python3 -m venv "$APP_DIR/venv"
  else
    chown -R "$BOT_USER":"$BOT_GROUP" "$APP_DIR/venv"
  fi
  run_as_bot "$APP_DIR/venv/bin/python" -m pip install \
    --disable-pip-version-check --no-input \
    -r "$APP_DIR/requirements.txt"
}

ensure_controller_key() {
  local home="$1" key_path="$home/.ssh/id_ed25519"
  if [[ ! -f "$key_path" ]]; then
    run_as_bot ssh-keygen -q -t ed25519 -N "" -C "vps-traffic-bot@$(hostname -s)" -f "$key_path"
  fi
  [[ -f "$key_path.pub" ]] || fail "Controller public key is missing: $key_path.pub"
  chown "$BOT_USER":"$BOT_GROUP" "$key_path" "$key_path.pub"
  chmod 600 "$key_path"
  chmod 644 "$key_path.pub"
  printf '%s' "$key_path"
}

read_existing_token() {
  [[ -r "$ENV_FILE" ]] || return 0
  sed -n 's/^VPS_TRAFFIC_BOT_TOKEN=//p' "$ENV_FILE" | head -n 1
}

read_existing_chat_ids() {
  [[ -r "$CONFIG_FILE" ]] || return 0
  python3 - "$CONFIG_FILE" <<'PY_CHAT_IDS'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle).get("allowed_chat_ids", [])
    print(",".join(str(item) for item in value))
except (OSError, ValueError, TypeError):
    pass
PY_CHAT_IDS
}

validate_chat_ids() {
  local raw="$1" item
  [[ -z "$raw" ]] && return 0
  IFS=',' read -r -a items <<< "$raw"
  for item in "${items[@]}"; do
    [[ "$item" =~ ^[[:space:]]*-?[0-9]+[[:space:]]*$ ]] || return 1
  done
}

validate_server_field() {
  local value="$1"
  [[ -n "$value" && "$value" != *$'\t'* && "$value" != *$'\n'* ]]
}

validate_ssh_field() {
  local value="$1"
  validate_server_field "$value" && [[ "$value" != *[[:space:]]* ]]
}

collect_servers() {
  local output_file="$1" name host user port identity
  : > "$output_file"
  while true; do
    name=$(prompt_tty "VPS name (must match server_name on the agent)")
    validate_server_field "$name" || { say "Invalid VPS name"; continue; }
    host=$(prompt_tty "SSH host or IP")
    validate_ssh_field "$host" || { say "Invalid SSH host"; continue; }
    user=$(prompt_tty "SSH user" root)
    validate_ssh_field "$user" || { say "Invalid SSH user"; continue; }
    while true; do
      port=$(prompt_tty "SSH port" 22)
      [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) && break
      say "SSH port must be between 1 and 65535"
    done
    identity=$(prompt_tty "SSH identity file" "$CONTROLLER_KEY")
    [[ "$identity" == /* ]] && validate_ssh_field "$identity" || { say "Identity file must be an absolute path without spaces"; continue; }
    if grep -Fq "$(printf '%s\t' "$name")" "$output_file"; then
      say "VPS names must be unique"
      continue
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$host" "$user" "$port" "$identity" >> "$output_file"
    confirm_tty "Add another VPS?" n || break
  done
  [[ -s "$output_file" ]] || fail "At least one VPS is required"
}

write_controller_config() {
  local server_file="$1" chat_ids="$2"
  CHAT_IDS="$chat_ids" SERVER_FILE="$server_file" CONFIG_FILE="$CONFIG_FILE" \
    HISTORY_PATH="$HISTORY_PATH" BOT_USER="$BOT_USER" python3 <<'PY_CONFIG'
import json
import os
import pwd
import tempfile
from pathlib import Path

server_file = Path(os.environ["SERVER_FILE"])
servers = []
for line in server_file.read_text(encoding="utf-8").splitlines():
    name, host, user, port, identity_file = line.split("\t")
    servers.append(
        {
            "name": name,
            "host": host,
            "user": user,
            "port": int(port),
            "identity_file": identity_file,
        }
    )

raw_chat_ids = os.environ["CHAT_IDS"].strip()
allowed_chat_ids = []
if raw_chat_ids:
    allowed_chat_ids = [int(value.strip()) for value in raw_chat_ids.split(",")]

config = {
    "token_env": "VPS_TRAFFIC_BOT_TOKEN",
    "allowed_chat_ids": allowed_chat_ids,
    "history_path": os.environ["HISTORY_PATH"],
    "servers": servers,
}

path = Path(os.environ["CONFIG_FILE"])
gid = pwd.getpwnam(os.environ["BOT_USER"]).pw_gid
fd, temporary = tempfile.mkstemp(prefix=".controller.", dir=path.parent, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary, 0, gid)
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY_CONFIG
}

write_token_file() {
  TOKEN="$1" ENV_FILE="$ENV_FILE" BOT_USER="$BOT_USER" python3 <<'PY_TOKEN'
import os
import pwd
import tempfile
from pathlib import Path

token = os.environ["TOKEN"]
if not token or "\n" in token or "\r" in token:
    raise SystemExit("Telegram token cannot be empty or contain newlines")

path = Path(os.environ["ENV_FILE"])
gid = pwd.getpwnam(os.environ["BOT_USER"]).pw_gid
fd, temporary = tempfile.mkstemp(prefix=".bot-env.", dir=path.parent, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"VPS_TRAFFIC_BOT_TOKEN={token}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary, 0, gid)
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY_TOKEN
}

print_agent_key_instructions() {
  local public_key="$1" line
  line="command=\"/usr/local/bin/vps-traffic-alert status --json\",restrict $public_key"
  say ""
  say "在每台 agent VPS 上，以对应 SSH 用户执行以下命令（只授予 status 权限）："
  say "  mkdir -p ~/.ssh && chmod 700 ~/.ssh"
  say "  touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
  say "  grep -qxF '$line' ~/.ssh/authorized_keys 2>/dev/null || printf '%s\\n' '$line' >> ~/.ssh/authorized_keys"
  say ""
  say "公钥文件：$CONTROLLER_KEY.pub"
  say "公钥内容：$public_key"
  say "请确认 agent 上已安装并配置 vps-traffic-alert，且命令路径为 /usr/local/bin/vps-traffic-alert。"
}

backup_existing_config() {
  if [[ -f "$CONFIG_FILE" ]]; then
    local backup
    backup=$(mktemp "$CONFIG_FILE.bak.XXXXXX")
    install -o root -g "$BOT_GROUP" -m 640 "$CONFIG_FILE" "$backup"
    say "Existing controller config backed up to $backup"
  fi
}

main() {
  require_root
  require_tty
  ensure_prerequisites
  ensure_bot_user
  install_controller_files

  local home existing_token token existing_chat_ids chat_ids servers_file public_key
  home=$(bot_home)
  CONTROLLER_KEY=$(ensure_controller_key "$home")
  public_key=$(<"$CONTROLLER_KEY.pub")

  existing_token=$(read_existing_token)
  if [[ -n "$existing_token" ]] && confirm_tty "Keep the existing Telegram BotFather token?" y; then
    token="$existing_token"
  else
    token=$(prompt_secret_tty "Telegram BotFather token")
  fi
  [[ -n "$token" ]] || fail "Telegram token cannot be empty"

  existing_chat_ids=$(read_existing_chat_ids)
  while true; do
    chat_ids=$(prompt_tty "Allowed Telegram chat IDs, comma-separated (optional; use /chatid later)" "$existing_chat_ids")
    validate_chat_ids "$chat_ids" && break
    say "Chat IDs must be integers, for example 123456789 or -1001234567890"
  done

  servers_file=$(mktemp)
  trap 'rm -f "$servers_file"' EXIT
  collect_servers "$servers_file"

  backup_existing_config
  write_controller_config "$servers_file" "$chat_ids"
  write_token_file "$token"

  print_agent_key_instructions "$public_key"
  say ""
  say "为了避免意外信任错误的主机，请先核对每台 agent 的 SSH host key/fingerprint，再让 controller 用户建立过一次 SSH 连接。"
  say "连接测试命令示例："
  say "  sudo -u $BOT_USER ssh -o BatchMode=yes -p 22 -i $CONTROLLER_KEY root@example.com 'vps-traffic-alert status --json'"

  systemctl daemon-reload
  if systemctl enable vps-traffic-bot.service && systemctl restart vps-traffic-bot.service; then
    say "Enabled vps-traffic-bot.service"
  else
    say "Controller service did not start; inspect: journalctl -u vps-traffic-bot.service -n 50 --no-pager"
  fi
  if systemctl enable --now vps-traffic-report.timer; then
    say "Enabled vps-traffic-report.timer"
  else
    say "Daily report timer did not start; inspect: systemctl status vps-traffic-report.timer"
  fi

  say ""
  say "控制器安装完成。"
  if [[ -z "$chat_ids" ]]; then
    say "现在给机器人发送 /chatid，取得数字 Chat ID 后写入：$CONFIG_FILE"
    say "然后执行：systemctl restart vps-traffic-bot.service"
  else
    say "给机器人发送 /start 或 /status 开始使用。"
  fi
  say "Token 和 controller.json 均以 root 所有、$BOT_GROUP 组可读的 0640 保存。"
}

main "$@"
