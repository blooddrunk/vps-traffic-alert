# Telegram controller

这是多 VPS 控制器。它运行在一台常在线的 Linux 主机上，通过 SSH 查询各台
`vps-traffic-alert` agent 的 `status --json`，再通过 Telegram 提供菜单、日报和
七日历史。agent 不需要开放新的监听端口，也不需要保存 controller 的凭据。

> 同一个 Bot Token 只能运行一个 controller。把 `vps-traffic-alert` 安装到各台
> VPS 只会安装 agent，不会自动启动 Telegram 命令机器人。

## 最快方式：一键安装 controller

在准备作为 controller 的常在线 Linux 主机上执行：

```bash
curl -fsSL https://raw.githubusercontent.com/blooddrunk/vps-traffic-alert/main/telegram-bot/install.sh | sudo bash
```

向导会完成以下工作：

- 安装 Python、`venv`、SSH client 等依赖；
- 创建没有登录 shell 的 `vps-traffic-bot` 专用用户；
- 安装虚拟环境、controller 程序和 systemd units；
- 生成 `/home/vps-traffic-bot/.ssh/id_ed25519`；
- 交互式收集 Bot Token、允许的 Chat ID 和多台 VPS 的 SSH 信息；
- 生成 `/etc/vps-traffic-alert/controller.json` 与 `/etc/vps-traffic-alert/bot.env`；
- 启用 `vps-traffic-bot.service` 和 `vps-traffic-report.timer`。

向导不会自动修改远端 VPS 的 `authorized_keys`。它会打印 controller 公钥和受限
授权命令，请在每台 agent 上审核后执行。这样既保留了“一键安装”的便利，也不会
让 Telegram 或一个安装脚本未经确认获得远程系统管理权限。

## 更新 controller

在 controller 主机上执行：

```bash
curl -fsSL https://raw.githubusercontent.com/blooddrunk/vps-traffic-alert/main/telegram-bot/update.sh | sudo bash
```

更新脚本无需交互，会更新 controller 程序、Python 依赖和 systemd units，并重启
`vps-traffic-bot.service`；如果日报 timer 已启用或正在运行，也会一并重启。它会保留：

- `/etc/vps-traffic-alert/controller.json`
- `/etc/vps-traffic-alert/bot.env`
- `/home/vps-traffic-bot/.ssh/` 下的 SSH key、`known_hosts` 和其他 SSH 配置

不要使用 `sudo vps-traffic-alert update` 更新 controller；该命令是当前主机上
agent 的更新命令。如果一台主机同时运行 agent 和 controller，修复后的 agent 更新
也会保留 controller 所需的配置目录权限。

## 按步骤配置

### 1. 配置每台 agent

在每台被监控的 VPS 上执行：

```bash
curl -fsSL https://raw.githubusercontent.com/blooddrunk/vps-traffic-alert/main/install.sh | sudo bash
sudo vps-traffic-alert configure
sudo vps-traffic-alert status --json
```

如果这台 VPS 只作为 agent 使用，在配置流程中对可选的 Telegram 阈值通知选择
`N` 即可。controller 使用 SSH 查询，不要求每台 VPS 都配置 Bot Token。

### 2. 给 controller 授权只读查询

一键向导会显示类似下面的内容。以 controller 配置中的 SSH 用户登录对应 agent，
把向导输出的完整一行加入 `~/.ssh/authorized_keys`：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
grep -qxF 'command="/usr/local/bin/vps-traffic-alert status --json",restrict ssh-ed25519 AAAA... vps-traffic-bot' ~/.ssh/authorized_keys 2>/dev/null || printf '%s\n' 'command="/usr/local/bin/vps-traffic-alert status --json",restrict ssh-ed25519 AAAA... vps-traffic-bot' >> ~/.ssh/authorized_keys
```

上面的 `AAAA...` 必须替换成向导实际生成的完整公钥，不能直接复制示例。对于
较老的 OpenSSH，如果不支持 `restrict`，可改用等价的：
`no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty`。

### 3. 配置 Bot Token、Chat ID 和 VPS

向导会询问这些值。VPS 的 `name` 必须与 agent 上的 `server_name` 完全一致；
`host`、`user`、`port` 和 `identity_file` 是从 controller 到 agent 的 SSH
连接参数。多个 VPS 会生成多个 `servers` 项。

如果首次安装时还不知道 Chat ID，可以把允许 Chat ID 留空。controller 启动后，
给机器人发送：

```text
/chatid
```

这个命令不需要先通过白名单认证。把返回的数字加入
`/etc/vps-traffic-alert/controller.json` 的 `allowed_chat_ids`，例如：

```json
{
  "token_env": "VPS_TRAFFIC_BOT_TOKEN",
  "allowed_chat_ids": [123456789, -1001234567890],
  "history_path": "/var/lib/vps-traffic-alert/history.jsonl",
  "servers": [
    {
      "name": "NoSla",
      "host": "1.2.3.4",
      "user": "root",
      "port": 22,
      "identity_file": "/home/vps-traffic-bot/.ssh/id_ed25519"
    },
    {
      "name": "Racknerd",
      "host": "2.3.4.5",
      "user": "monitor",
      "port": 22,
      "identity_file": "/home/vps-traffic-bot/.ssh/id_ed25519"
    }
  ]
}
```

修改后重启：

```bash
sudo systemctl restart vps-traffic-bot.service
```

### 4. 验证 SSH 和服务

每台 agent 都要先以 controller 的 service 用户建立一次交互式 SSH 连接。确认
fingerprint 无误后输入 `yes`，不要在这一步使用 `BatchMode`：

```bash
sudo -u vps-traffic-bot ssh \
  -p 22 -i /home/vps-traffic-bot/.ssh/id_ed25519 root@1.2.3.4 \
  'vps-traffic-alert status --json'
```

确认 host key 后，再执行 controller 实际使用的非交互测试：

```bash
sudo -u vps-traffic-bot ssh -o BatchMode=yes -o ConnectTimeout=10 \
  -p 22 -i /home/vps-traffic-bot/.ssh/id_ed25519 root@1.2.3.4 \
  'vps-traffic-alert status --json'
```

SSH 测试必须输出一个 `schema_version: 1` 的 JSON 对象。controller 使用
`BatchMode=yes`，不能在 Telegram 查询时回答 host key 提示；如果跳过首次确认，
Bot 通常会返回 SSH exit status `255`。也可以使用经过核验的 `ssh-keyscan` 预置
host key，但不要未经核验直接信任扫描结果。

```bash
sudo systemctl is-active vps-traffic-bot.service
sudo systemctl is-enabled vps-traffic-report.timer
```

如果要求密码，说明公钥或 `authorized_keys` 尚未正确配置；如果交互式连接无法
建立，先检查 SSH 用户、端口和 agent 防火墙。

## 手动安装

如果不能使用一键向导，也可以手动完成：

```bash
sudo useradd --system --create-home vps-traffic-bot
sudo install -d -o vps-traffic-bot -g vps-traffic-bot /opt/vps-traffic-bot
sudo cp bot.py requirements.txt /opt/vps-traffic-bot/
sudo -u vps-traffic-bot python3 -m venv /opt/vps-traffic-bot/venv
sudo -u vps-traffic-bot /opt/vps-traffic-bot/venv/bin/pip install \
  -r /opt/vps-traffic-bot/requirements.txt
sudo install -d -o root -g vps-traffic-bot -m 750 /etc/vps-traffic-alert
sudo cp config.example.json /etc/vps-traffic-alert/controller.json
```

手动编辑 `controller.json`，再把 token 写入环境文件。controller 用户需要读取
token，因此文件必须是 root 所有、`vps-traffic-bot` 组可读的 `0640`，不能使用
`root:root 0600`：

```bash
printf '%s\n' 'VPS_TRAFFIC_BOT_TOKEN=replace-me' | \
  sudo tee /etc/vps-traffic-alert/bot.env >/dev/null
sudo chown root:vps-traffic-bot /etc/vps-traffic-alert/bot.env
sudo chmod 640 /etc/vps-traffic-alert/bot.env
sudo chown root:vps-traffic-bot /etc/vps-traffic-alert/controller.json
sudo chmod 640 /etc/vps-traffic-alert/controller.json
```

复制 `vps-traffic-bot.service`、`vps-traffic-report.service` 和
`vps-traffic-report.timer` 到 `/etc/systemd/system` 后启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vps-traffic-bot.service vps-traffic-report.timer
```

## Telegram 命令

- `/start`：打开内联菜单；
- `/status`：选择 VPS；
- `/status NoSla`：直接查询指定 VPS，名称包含空格也支持；
- `/report`：查询所有 VPS 的当前汇总，不写入日报历史；
- `/history NoSla`：查看最近七天的日报流量增量；至少需要两次成功的日报快照；
- `/chatid`：显示当前数字 Chat ID，用于首次配置白名单。

只有 `allowed_chat_ids` 中的 ID 会收到业务响应。SSH 使用 BatchMode 和超时，
单台 VPS 不可用时不会阻塞其他 VPS 的查询。

## 故障排查

```bash
# controller 是否在运行
sudo systemctl status vps-traffic-bot.service --no-pager

# 查看 bot 错误
sudo journalctl -u vps-traffic-bot.service -n 50 --no-pager

# 查看日报 timer
sudo systemctl status vps-traffic-report.timer --no-pager

# 手动发送一份日报并查看错误
sudo systemctl start vps-traffic-report.service
sudo journalctl -u vps-traffic-report.service -n 50 --no-pager
```

如果日志出现 `Conflict: terminated by other getUpdates request`，说明同一个 Bot
Token 有另一个 polling controller 正在运行，请停掉重复实例。不要公开
`bot.env`、BotFather token，或包含 token 的 Telegram API URL。
