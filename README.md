# VPS Traffic Alert

一个轻量、独立的 VPS 套餐流量监控工具。它使用 `vnStat` 读取公网网卡累计流量，按照服务商的自定义账单周期统计 `RX + TX`，并在达到指定比例时发送 Telegram 通知。

它适合作为 Beszel 的补充：

- **Beszel**：CPU、内存、磁盘、在线状态和实时网络速率
- **VPS Traffic Alert**：按服务商充值日累计本周期流量，并在 70% / 80% / 90% / 95% / 100% 告警

## 功能

- 自定义套餐额度，例如 `300GB`、`1000GB`、`5.9TB`
- 自定义每月重置日，支持 1–31 日
- 自定义 IANA 时区，例如 `Asia/Shanghai`、`America/Los_Angeles`、`Etc/GMT+8`
- 默认统计公网网卡 `RX + TX` 总流量
- 每个阈值在同一账单周期内只通知一次
- 支持当前周期初始已用流量，避免中途安装造成少算
- 使用 systemd timer 每 15 分钟检查一次
- 无第三方 Python 包依赖
- 无参数运行时显示交互式快捷菜单
- 保留完整子命令，适合自动化和脚本调用
- 提供稳定的 JSON 状态接口，可由远程控制器通过 SSH 查询
- 可选 Telegram 控制器支持多 VPS、内联菜单、日报和七日历史

## 支持环境

- Linux + systemd
- Debian / Ubuntu、Fedora / RHEL 系、Arch Linux、openSUSE
- Python 3
- vnStat

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/blooddrunk/vps-traffic-alert/main/install.sh | sudo bash
```

安装器会：

1. 安装 `vnstat`、`python3`、`curl` 和 `iproute2`
2. 自动识别公网网卡
3. 交互式询问套餐额度、充值日、时区和 Telegram 配置
4. 创建并启用 systemd timer
5. 执行第一次检查

> 使用 `curl | sudo bash` 时，交互输入会从 `/dev/tty` 读取。

## 快捷菜单

安装完成后直接运行：

```bash
vps-traffic-alert
```

普通用户运行时，程序会自动通过 `sudo` 重新打开菜单。也可以显式运行：

```bash
sudo vps-traffic-alert menu
```

菜单包含：

```text
  1) View current traffic status
  2) Check traffic now
  3) Test Telegram notification
  4) Configure monitoring
  5) Enable automatic checks
  6) Disable automatic checks
  7) View recent logs
  8) Show configuration
  9) Reset current cycle state
 10) Update from GitHub
 11) Uninstall
  0) Exit
```

每次操作完成后会返回主菜单。原有命令行子命令仍然可用。

## 常用命令

```bash
sudo vps-traffic-alert configure     # 重新配置
sudo vps-traffic-alert status        # 查看当前周期流量和定时器状态
sudo vps-traffic-alert status --json # 仅输出机器可读 JSON
sudo vps-traffic-alert check         # 立即检查一次
sudo vps-traffic-alert test          # 测试 Telegram 通知
sudo vps-traffic-alert enable        # 启用自动检查
sudo vps-traffic-alert disable       # 禁用自动检查
sudo vps-traffic-alert logs          # 查看最近日志
sudo vps-traffic-alert show-config   # 查看脱敏后的配置
sudo vps-traffic-alert reset         # 重置本地账单周期状态
sudo vps-traffic-alert update        # 从 GitHub 更新
sudo vps-traffic-alert uninstall     # 卸载，保留配置和状态
sudo vps-traffic-alert uninstall --purge  # 完全卸载
```

`status --json` 保持 agent 无数据库、无监听端口、无第三方依赖。输出遵循
`schema_version: 1`，包含服务器、账单周期、流量、下一个阈值以及带时区时间戳：

```json
{
  "schema_version": 1,
  "server": {"name": "NoSla", "interface": "eth0"},
  "billing": {"quota_gb": 1000, "reset_day": 7, "timezone": "Asia/Shanghai", "cycle_start": "2026-07-07", "cycle_end": "2026-08-07"},
  "traffic": {"mode": "total", "used_gb": 356.42, "remaining_gb": 643.58, "usage_percent": 35.64},
  "threshold": {"next": 70, "remaining_gb": 343.58},
  "timestamp": "2026-08-03T09:00:00+08:00"
}
```

## 多 VPS Telegram 控制器

可选控制器位于 [`telegram-bot/`](telegram-bot/README.md)。它通过 SSH 执行
`vps-traffic-alert status --json`，因此 agent 不开放新端口。控制器提供：

> 仅更新各 VPS 上的 agent 不会启动 Telegram 命令机器人；必须另选一台常在线
> 主机，按照控制器安装文档运行 `vps-traffic-bot.service`。

- `/start` 内联操作菜单和 VPS 列表
- `/status NoSla` 即时查询
- 每天 09:00 的 systemd timer 聚合报告
- `/history NoSla` 最近七天日报流量增量
- `/chatid` 显示需要加入白名单的 Telegram Chat ID

控制器是唯一需要 `python-telegram-bot` 的组件。Bot Token 从环境变量读取，示例
配置中不包含密钥。若只将 VPS 用作 agent，可以省略 `telegram` 配置；原有独立
Telegram 阈值通知配置仍完全兼容。

首次配置仅作为 agent 使用的 VPS 时，在配置流程中对可选的 Telegram 阈值通知
选择 `N` 即可，不需要填写 Bot Token 或 Chat ID；控制器仍可通过 SSH 查询该 VPS。

## 已安装用户升级

旧版本用户执行：

```bash
sudo vps-traffic-alert update
```

然后直接运行：

```bash
vps-traffic-alert
```

## 三台 VPS 的配置参考

### NoSla

```text
Server name: NoSla
Monthly quota: 1000GB
Billing cycle reset day: 7
Billing timezone: Asia/Shanghai
```

### Bandwagonhost

```text
Server name: Bandwagonhost
Monthly quota: 300GB
Billing cycle reset day: 26
Billing timezone: Etc/GMT+8
```

`Etc/GMT+8` 表示固定 UTC-8，也就是不随夏令时变化的 PST。若服务商后台实际按洛杉矶当地时间并随夏令时变化，请改用 `America/Los_Angeles`。

### RackNerd

```text
Server name: Racknerd
Monthly quota: 5.9TB
Billing cycle reset day: 21
Billing timezone: Etc/GMT+8
```

请以服务商后台显示的实际额度为准。如果后台写的是 `6000GB`，不要配置成 `5.9TB`。

## Telegram 准备

1. 在 Telegram 中联系 `@BotFather` 创建机器人并取得 Bot Token。
2. 先给机器人发送一条消息。
3. 获取 Chat ID。
4. 在安装或 `configure` 时填入 Token 和 Chat ID。

配置文件权限为 `600`，位置：

```text
/etc/vps-traffic-alert/config.json
```

## 数据与准确性

状态文件位置：

```text
/var/lib/vps-traffic-alert/state.json
```

工具使用十进制单位：

```text
1 GB = 1,000,000,000 bytes
1 TB = 1,000 GB
```

默认统计模式为：

```text
Total = RX + TX
```

本工具读取操作系统公网网卡数据，服务商后台可能因为以下原因存在少量差异：

- 服务商只计算单向流量
- 服务商采用 GiB/TiB 而不是 GB/TB
- 虚拟化层或网络封装开销
- 账单周期边界处最多约 15 分钟的检查误差
- vnStat 数据库被删除、重建或公网网卡发生变化

因此告警应作为防止超额的主动提醒，服务商后台仍是最终计费依据。

## 手动配置

示例配置见 [`config.example.json`](config.example.json)。关键字段：

```json
{
  "server_name": "NoSla",
  "interface": "eth0",
  "quota_gb": 1000,
  "reset_day": 7,
  "timezone": "Asia/Shanghai",
  "initial_used_gb": 0,
  "traffic_mode": "total",
  "thresholds": [70, 80, 90, 95, 100]
}
```

修改后运行：

```bash
sudo vps-traffic-alert reset
sudo vps-traffic-alert check
```

## 与 Beszel 的关系

本项目不会修改 Beszel，也不会把数据写进 Beszel 数据库。推荐分工：

| 项目 | 工具 |
|---|---|
| CPU / 内存 / 磁盘 | Beszel |
| VPS 或 Agent 离线 | Beszel |
| 实时网络速度 | Beszel |
| 自定义充值周期累计流量 | VPS Traffic Alert |
| 流量分级通知 | VPS Traffic Alert |

## License

MIT
