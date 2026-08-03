# Telegram controller

The controller queries agents over SSH, presents an inline Telegram menu, sends
daily aggregate reports, and keeps a small JSON Lines history. Agents expose no
ports and need no controller credentials.

> Updating `vps-traffic-alert` on each monitored VPS only installs the **agent**.
> Telegram commands work only after one machine has been configured to run this
> controller continuously. Do not run a controller on every VPS.

## Why `/start` does nothing

The agent's original threshold notifications only call Telegram's `sendMessage`
API; they do not listen for commands. `/start` is handled by the separate
`vps-traffic-bot.service`. If it produces no reply, check these in order:

1. The controller has been installed on one always-on Linux machine.
2. `vps-traffic-bot.service` is active and using the same BotFather token as the
   bot to which `/start` was sent.
3. Your numeric chat ID appears in `allowed_chat_ids`.
4. The controller user can SSH non-interactively to every configured agent.

After the controller is running, send `/chatid` to the bot. This command works
even before authorization. Add the returned number to `allowed_chat_ids`, restart
the service, and send `/start` again.

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

Edit `/etc/vps-traffic-alert/controller.json`. `name` is the exact name accepted
by `/status NAME`; `host`, `user`, `port`, and `identity_file` describe the SSH
connection from the controller to that agent:

```json
{
  "token_env": "VPS_TRAFFIC_BOT_TOKEN",
  "allowed_chat_ids": [123456789],
  "history_path": "/var/lib/vps-traffic-bot/history.jsonl",
  "servers": [
    {"name": "NoSla", "host": "1.2.3.4", "user": "root"}
  ]
}
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

Verify the controller immediately:

```bash
sudo systemctl status vps-traffic-bot.service --no-pager
sudo journalctl -u vps-traffic-bot.service -n 50 --no-pager
sudo -u vps-traffic-bot ssh -o BatchMode=yes root@1.2.3.4 \
  'vps-traffic-alert status --json'
```

The SSH test must print one JSON object. If it asks for a password or host-key
confirmation, configure the controller user's key and `known_hosts` first. If the
service log reports `Conflict: terminated by other getUpdates request`, another
copy of the controller is polling the same Telegram bot token; stop the duplicate.

The timer uses the controller host's timezone. Adjust `OnCalendar` if 09:00 is
not the desired report time.

## Commands

- `/start` — open the interactive menu. **Current Status** and **VPS List** open
  the server picker; **Daily Report** queries all configured servers.
- `/status` — open the server picker.
- `/status NoSla` — immediately query the server whose configured name is
  `NoSla`. Names containing spaces are supported.
- `/report` — query every VPS and display the current aggregate report. This does
  not write a daily history snapshot.
- `/history NoSla` — show changes between the latest seven daily snapshots. At
  least two successful scheduled reports are required before a delta is shown.
- `/chatid` — show the current numeric chat ID; useful during initial setup.

The bot registers these commands with Telegram when the service starts, so they
also appear in Telegram's `/` command menu.

Only IDs in `allowed_chat_ids` receive responses. SSH uses batch mode and a
10-second connection timeout so an unavailable VPS does not block the report.

## Troubleshooting checklist

```bash
# Is the controller actually running?
sudo systemctl is-active vps-traffic-bot.service

# Why did it stop or reject an update?
sudo journalctl -u vps-traffic-bot.service -f

# Does the token work? (do not paste the output publicly)
set -a; . /etc/vps-traffic-alert/bot.env; set +a
curl -s "https://api.telegram.org/bot${VPS_TRAFFIC_BOT_TOKEN}/getMe"

# Can the service account query an agent without interaction?
sudo -u vps-traffic-bot ssh -o BatchMode=yes root@1.2.3.4 \
  'vps-traffic-alert status --json'

# Send one report now and expose configuration/SSH errors in the terminal
sudo systemctl start vps-traffic-report.service
sudo journalctl -u vps-traffic-report.service -n 50 --no-pager
```

Never post `bot.env`, the BotFather token, or the output URL containing that token.
