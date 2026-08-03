# Telegram controller

The controller queries agents over SSH, presents an inline Telegram menu, sends
daily aggregate reports, and keeps a small JSON Lines history. Agents expose no
ports and need no controller credentials.

## Install

Create a dedicated user and virtual environment, then copy the included units:

```bash
sudo useradd --system --create-home vps-traffic-bot
sudo install -d -o vps-traffic-bot -g vps-traffic-bot /opt/vps-traffic-bot
sudo cp bot.py requirements.txt /opt/vps-traffic-bot/
sudo -u vps-traffic-bot python3 -m venv /opt/vps-traffic-bot/venv
sudo -u vps-traffic-bot /opt/vps-traffic-bot/venv/bin/pip install -r /opt/vps-traffic-bot/requirements.txt
sudo cp config.example.json /etc/vps-traffic-alert/controller.json
```

Put the token in a root-owned environment file; never add it to JSON or Git:

```bash
echo 'VPS_TRAFFIC_BOT_TOKEN=replace-me' | sudo tee /etc/vps-traffic-alert/bot.env
sudo chmod 600 /etc/vps-traffic-alert/bot.env
```

Add the controller user's public SSH key to every agent. Restricting that key in
`authorized_keys` to the status command is recommended:

```text
command="/usr/local/bin/vps-traffic-alert status --json",restrict ssh-ed25519 AAAA... bot
```

Copy `vps-traffic-bot.service`, `vps-traffic-report.service`, and
`vps-traffic-report.timer` to `/etc/systemd/system`, then enable them:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vps-traffic-bot.service vps-traffic-report.timer
```

The timer uses the controller host's timezone. Adjust `OnCalendar` if 09:00 is
not the desired report time.

## Commands

- `/start` — interactive menu
- `/status [SERVER]` — select or directly query a VPS
- `/history SERVER` — traffic deltas from the latest seven daily snapshots

Only IDs in `allowed_chat_ids` receive responses. SSH uses batch mode and a
10-second connection timeout so an unavailable VPS does not block the report.
