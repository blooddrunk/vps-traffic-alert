#!/usr/bin/env python3
"""Track VPS traffic by provider billing cycle and send Telegram alerts."""

from __future__ import annotations

import argparse
import calendar
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/vps-traffic-alert/config.json")
STATE_DIR = Path("/var/lib/vps-traffic-alert")
STATE_PATH = STATE_DIR / "state.json"
LOCK_PATH = STATE_DIR / "lock"
DECIMAL_GB = 1_000_000_000
DEFAULT_THRESHOLDS = [70, 80, 90, 95, 100]


class MonitorError(RuntimeError):
    """Expected runtime or configuration error."""


@dataclass(frozen=True)
class TrafficCounters:
    rx: int
    tx: int

    def selected(self, mode: str) -> int:
        if mode == "rx":
            return self.rx
        if mode == "tx":
            return self.tx
        if mode == "total":
            return self.rx + self.tx
        raise MonitorError(f"Unsupported traffic_mode: {mode}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise MonitorError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MonitorError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise MonitorError(f"Expected a JSON object in {path}")
    return value


def save_json_atomic(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, mode)
    os.replace(temp_path, path)


def set_process_timezone(timezone_name: str) -> None:
    zone_file = Path("/usr/share/zoneinfo") / timezone_name
    if timezone_name.startswith("/") or ".." in timezone_name.split("/"):
        raise MonitorError("timezone must be an IANA timezone name")
    if not zone_file.is_file():
        raise MonitorError(f"Unknown timezone: {timezone_name}")
    os.environ["TZ"] = timezone_name
    if not hasattr(time, "tzset"):
        raise MonitorError("This operating system does not support tzset")
    time.tzset()


def clamp_day(year: int, month: int, requested_day: int) -> int:
    return min(requested_day, calendar.monthrange(year, month)[1])


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def cycle_bounds(now: datetime, reset_day: int) -> tuple[date, date]:
    if not 1 <= reset_day <= 31:
        raise MonitorError("reset_day must be between 1 and 31")

    current_reset = date(
        now.year,
        now.month,
        clamp_day(now.year, now.month, reset_day),
    )

    if now.date() >= current_reset:
        start_year, start_month = now.year, now.month
    else:
        start_year, start_month = previous_month(now.year, now.month)

    end_year, end_month = next_month(start_year, start_month)
    start = date(
        start_year,
        start_month,
        clamp_day(start_year, start_month, reset_day),
    )
    end = date(
        end_year,
        end_month,
        clamp_day(end_year, end_month, reset_day),
    )
    return start, end


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    required = [
        "server_name",
        "interface",
        "quota_gb",
        "reset_day",
        "timezone",
        "telegram",
    ]
    missing = [key for key in required if key not in config]
    if missing:
        raise MonitorError("Missing config keys: " + ", ".join(missing))

    server_name = str(config["server_name"]).strip()
    interface = str(config["interface"]).strip()
    timezone_name = str(config["timezone"]).strip()
    traffic_mode = str(config.get("traffic_mode", "total")).strip().lower()

    if not server_name:
        raise MonitorError("server_name cannot be empty")
    if not interface:
        raise MonitorError("interface cannot be empty")
    if traffic_mode not in {"total", "rx", "tx"}:
        raise MonitorError("traffic_mode must be total, rx, or tx")

    try:
        quota_gb = float(config["quota_gb"])
        initial_used_gb = float(config.get("initial_used_gb", 0))
        reset_day = int(config["reset_day"])
    except (TypeError, ValueError) as exc:
        raise MonitorError("quota_gb, initial_used_gb, and reset_day must be numeric") from exc

    if quota_gb <= 0:
        raise MonitorError("quota_gb must be greater than zero")
    if initial_used_gb < 0:
        raise MonitorError("initial_used_gb cannot be negative")
    if not 1 <= reset_day <= 31:
        raise MonitorError("reset_day must be between 1 and 31")

    thresholds_raw = config.get("thresholds", DEFAULT_THRESHOLDS)
    if not isinstance(thresholds_raw, list) or not thresholds_raw:
        raise MonitorError("thresholds must be a non-empty list")
    try:
        thresholds = sorted({int(item) for item in thresholds_raw})
    except (TypeError, ValueError) as exc:
        raise MonitorError("thresholds must contain integers") from exc
    if thresholds[0] <= 0 or thresholds[-1] > 1000:
        raise MonitorError("thresholds must be greater than 0 and at most 1000")

    telegram = config["telegram"]
    if not isinstance(telegram, dict):
        raise MonitorError("telegram must be a JSON object")
    bot_token = str(telegram.get("bot_token", "")).strip()
    chat_id = str(telegram.get("chat_id", "")).strip()
    if not bot_token or not chat_id:
        raise MonitorError("telegram.bot_token and telegram.chat_id are required")

    set_process_timezone(timezone_name)

    normalized = dict(config)
    normalized.update(
        {
            "server_name": server_name,
            "interface": interface,
            "quota_gb": quota_gb,
            "initial_used_gb": initial_used_gb,
            "reset_day": reset_day,
            "timezone": timezone_name,
            "traffic_mode": traffic_mode,
            "thresholds": thresholds,
            "telegram": {"bot_token": bot_token, "chat_id": chat_id},
            "notify_cycle_reset": bool(config.get("notify_cycle_reset", True)),
        }
    )
    return normalized


def read_vnstat(interface: str) -> TrafficCounters:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        output = subprocess.check_output(
            ["vnstat", "--json", "-i", interface],
            text=True,
            stderr=subprocess.STDOUT,
            env=environment,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise MonitorError("vnstat is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise MonitorError(f"vnstat failed: {exc.output.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MonitorError("vnstat timed out") from exc

    try:
        payload = json.loads(output)
        interfaces = payload["interfaces"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MonitorError("Unexpected vnstat JSON output") from exc

    selected: dict[str, Any] | None = None
    for item in interfaces:
        if item.get("name") == interface:
            selected = item
            break
    if selected is None:
        raise MonitorError(f"vnstat has no data for interface {interface}")

    try:
        total = selected["traffic"]["total"]
        return TrafficCounters(rx=int(total["rx"]), tx=int(total["tx"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MonitorError("vnstat JSON does not contain total RX/TX counters") from exc


def send_telegram(config: dict[str, Any], message: str) -> None:
    telegram = config["telegram"]
    url = f"https://api.telegram.org/bot{telegram['bot_token']}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": telegram["chat_id"],
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise MonitorError(f"Telegram returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise MonitorError(f"Telegram HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise MonitorError(f"Telegram connection failed: {exc.reason}") from exc


def format_bytes(value: int) -> str:
    gb = value / DECIMAL_GB
    if gb >= 1000:
        return f"{gb / 1000:.2f} TB"
    return f"{gb:.2f} GB"


def usage_percent(used_bytes: int, quota_bytes: int) -> float:
    return used_bytes / quota_bytes * 100 if quota_bytes > 0 else 0.0


def make_summary(
    config: dict[str, Any],
    cycle_start: date,
    cycle_end: date,
    used_bytes: int,
) -> str:
    quota_bytes = int(config["quota_gb"] * DECIMAL_GB)
    remaining = max(0, quota_bytes - used_bytes)
    percent = usage_percent(used_bytes, quota_bytes)
    return (
        f"{config['server_name']} traffic\n"
        f"Cycle: {cycle_start.isoformat()} -> {cycle_end.isoformat()} "
        f"({config['timezone']})\n"
        f"Used: {format_bytes(used_bytes)} / {format_bytes(quota_bytes)}\n"
        f"Usage: {percent:.2f}%\n"
        f"Remaining: {format_bytes(remaining)}\n"
        f"Mode: {config['traffic_mode'].upper()}"
    )


def load_state_if_present() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    return load_json(STATE_PATH)


def projected_usage(
    config: dict[str, Any],
    state: dict[str, Any] | None,
    current_total: int,
    cycle_id: str,
) -> int:
    initial = int(config["initial_used_gb"] * DECIMAL_GB)
    if state is None:
        return initial
    if state.get("cycle_id") != cycle_id:
        return 0
    last_total = int(state.get("last_total_bytes", current_total))
    delta = max(0, current_total - last_total)
    return int(state.get("used_bytes", 0)) + delta


def run_check(config: dict[str, Any]) -> str:
    now = datetime.now()
    cycle_start, cycle_end = cycle_bounds(now, config["reset_day"])
    cycle_id = cycle_start.isoformat()
    counters = read_vnstat(config["interface"])
    current_total = counters.selected(config["traffic_mode"])
    quota_bytes = int(config["quota_gb"] * DECIMAL_GB)
    state = load_state_if_present()

    if state is None:
        used_bytes = int(config["initial_used_gb"] * DECIMAL_GB)
        current_percent = usage_percent(used_bytes, quota_bytes)
        already_alerted = [
            threshold
            for threshold in config["thresholds"]
            if current_percent >= threshold
        ]
        state = {
            "cycle_id": cycle_id,
            "last_total_bytes": current_total,
            "used_bytes": used_bytes,
            "alerted": already_alerted,
            "last_check": now.isoformat(timespec="seconds"),
        }
        save_json_atomic(STATE_PATH, state)
        send_telegram(
            config,
            "Traffic monitor initialized\n\n"
            + make_summary(config, cycle_start, cycle_end, used_bytes),
        )
        return make_summary(config, cycle_start, cycle_end, used_bytes)

    last_total = int(state.get("last_total_bytes", current_total))
    if current_total < last_total:
        delta = 0
        send_telegram(
            config,
            f"Warning: {config['server_name']} vnstat counters decreased. "
            "The database or interface may have been reset. Monitoring will continue from the new value.",
        )
    else:
        delta = current_total - last_total

    if state.get("cycle_id") != cycle_id:
        state = {
            "cycle_id": cycle_id,
            "last_total_bytes": current_total,
            "used_bytes": delta,
            "alerted": [],
            "last_check": now.isoformat(timespec="seconds"),
        }
        if config["notify_cycle_reset"]:
            send_telegram(
                config,
                "New traffic billing cycle\n\n"
                + make_summary(config, cycle_start, cycle_end, delta),
            )
    else:
        state["used_bytes"] = int(state.get("used_bytes", 0)) + delta
        state["last_total_bytes"] = current_total
        state["last_check"] = now.isoformat(timespec="seconds")

    used_bytes = int(state["used_bytes"])
    percent = usage_percent(used_bytes, quota_bytes)
    alerted = {int(item) for item in state.get("alerted", [])}

    for threshold in config["thresholds"]:
        if percent >= threshold and threshold not in alerted:
            send_telegram(
                config,
                f"Traffic alert: {config['server_name']} reached {threshold}%\n\n"
                + make_summary(config, cycle_start, cycle_end, used_bytes),
            )
            alerted.add(threshold)

    state["alerted"] = sorted(alerted)
    save_json_atomic(STATE_PATH, state)
    return make_summary(config, cycle_start, cycle_end, used_bytes)


def run_status(config: dict[str, Any]) -> str:
    now = datetime.now()
    cycle_start, cycle_end = cycle_bounds(now, config["reset_day"])
    cycle_id = cycle_start.isoformat()
    counters = read_vnstat(config["interface"])
    current_total = counters.selected(config["traffic_mode"])
    state = load_state_if_present()
    used_bytes = projected_usage(config, state, current_total, cycle_id)
    summary = make_summary(config, cycle_start, cycle_end, used_bytes)
    if state and state.get("last_check"):
        summary += f"\nLast check: {state['last_check']}"
    else:
        summary += "\nState: not initialized"
    return summary


def with_lock(action: Any) -> Any:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MonitorError("Another monitor process is already running") from exc
        return action()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["check", "status", "test", "validate-config"],
    )
    parser.add_argument("--config", default=str(CONFIG_PATH))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = validate_config(load_json(Path(args.config)))

    if args.command == "validate-config":
        print("Configuration is valid")
        return 0
    if args.command == "test":
        send_telegram(config, f"Telegram test succeeded for {config['server_name']}")
        print("Telegram test message sent")
        return 0
    if args.command == "status":
        print(with_lock(lambda: run_status(config)))
        return 0
    if args.command == "check":
        print(with_lock(lambda: run_check(config)))
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MonitorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
