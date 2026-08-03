#!/usr/bin/env python3
"""Telegram controller for one or more VPS Traffic Alert agents."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Server:
    name: str
    host: str
    user: str = "root"
    port: int = 22
    identity_file: str | None = None


class ControllerError(RuntimeError):
    pass


class Controller:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.servers = [Server(**item) for item in config.get("servers", [])]
        if not self.servers:
            raise ControllerError("At least one server must be configured")
        self.history_path = Path(
            config.get("history_path", "/var/lib/vps-traffic-bot/history.jsonl")
        )

    def server(self, name: str) -> Server:
        try:
            return next(item for item in self.servers if item.name == name)
        except StopIteration as exc:
            raise ControllerError(f"Unknown server: {name}") from exc

    def query(self, server: Server) -> dict[str, Any]:
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-p", str(server.port),
        ]
        if server.identity_file:
            command += ["-i", server.identity_file]
        command += [f"{server.user}@{server.host}", "vps-traffic-alert status --json"]
        try:
            result = subprocess.run(
                command, check=True, capture_output=True, text=True, timeout=30
            )
            payload = json.loads(result.stdout)
        except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise ControllerError(f"Could not query {server.name}: {exc}") from exc
        if payload.get("schema_version") != 1:
            raise ControllerError(f"{server.name} returned an unsupported schema")
        return payload

    def query_all(self) -> list[tuple[Server, dict[str, Any] | Exception]]:
        results = []
        for server in self.servers:
            try:
                results.append((server, self.query(server)))
            except Exception as exc:  # retain other servers when one VPS is down
                results.append((server, exc))
        return results

    def append_history(self, payload: dict[str, Any]) -> None:
        record = {
            "date": date.today().isoformat(),
            "server": payload["server"]["name"],
            "used_gb": payload["traffic"]["used_gb"],
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def history(self, server_name: str, days: int = 7) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        records: dict[str, dict[str, Any]] = {}
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("server") == server_name:
                records[item["date"]] = item
        return sorted(records.values(), key=lambda item: item["date"])[-days:]


def format_status(payload: dict[str, Any]) -> str:
    billing, traffic, threshold = (
        payload["billing"], payload["traffic"], payload["threshold"]
    )
    next_alert = (
        f"{threshold['next']}%\n{threshold['remaining_gb']:g}GB remaining"
        if threshold["next"] is not None else "All configured thresholds reached"
    )
    return (
        f"🖥 {payload['server']['name']}\n\n"
        f"Billing cycle:\n{billing['cycle_start']} - {billing['cycle_end']}\n\n"
        f"Traffic:\n{traffic['used_gb']:g}GB / {billing['quota_gb']:g}GB\n\n"
        f"Usage:\n{traffic['usage_percent']:g}%\n\n"
        f"Remaining:\n{traffic['remaining_gb']:g}GB\n\n"
        f"Next alert:\n{next_alert}"
    )


def format_report(
    results: list[tuple[Server, dict[str, Any] | Exception]],
    deltas: dict[str, float] | None = None,
) -> str:
    lines = ["📅 VPS Traffic Report", ""]
    for server, result in results:
        if isinstance(result, Exception):
            lines += [server.name, f"Unavailable: {result}", ""]
            continue
        traffic, billing = result["traffic"], result["billing"]
        lines += [
            server.name,
            f"{traffic['used_gb']:g}GB / {billing['quota_gb']:g}GB",
            f"{traffic['usage_percent']:g}%",
        ]
        if deltas is not None:
            delta = deltas.get(server.name)
            lines.append("Today: no previous snapshot" if delta is None else f"Today: +{delta:g}GB")
        lines.append("")
    return "\n".join(lines).rstrip()


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Current Status", callback_data="menu:status")],
        [InlineKeyboardButton("🖥 VPS List", callback_data="menu:list")],
        [InlineKeyboardButton("📅 Daily Report", callback_data="menu:report")],
    ])


def server_keyboard(controller: Controller) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(item.name, callback_data=f"server:{item.name}")]
        for item in controller.servers
    ])


def authorized(config: dict[str, Any], update: Update) -> bool:
    allowed = {int(value) for value in config.get("allowed_chat_ids", [])}
    return bool(update.effective_chat and update.effective_chat.id in allowed)


async def reject_unauthorized(update: Update) -> None:
    """Explain silent command failures and provide the ID needed for configuration."""
    if update.effective_message and update.effective_chat:
        await update.effective_message.reply_text(
            "This chat is not authorized for VPS Traffic Alert.\n"
            f"Chat ID: {update.effective_chat.id}\n\n"
            "Add this number to allowed_chat_ids in controller.json, then restart "
            "vps-traffic-bot.service."
        )


async def chat_id_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    if update.effective_message and update.effective_chat:
        await update.effective_message.reply_text(
            f"Chat ID: {update.effective_chat.id}"
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(context.bot_data["config"], update):
        await reject_unauthorized(update)
        return
    if update.message:
        await update.message.reply_text(
            "🚀 VPS Traffic Alert\n\nSelect action:", reply_markup=main_keyboard()
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(context.bot_data["config"], update) or not update.message:
        if update.message:
            await reject_unauthorized(update)
        return
    if context.args:
        try:
            payload = await asyncio.to_thread(
                context.bot_data["controller"].query,
                context.bot_data["controller"].server(" ".join(context.args)),
            )
            await update.message.reply_text(format_status(payload))
        except ControllerError as exc:
            await update.message.reply_text(f"⚠️ {exc}")
    else:
        await update.message.reply_text("Select a VPS:", reply_markup=server_keyboard(context.bot_data["controller"]))


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(context.bot_data["config"], update) or not update.message:
        if update.message:
            await reject_unauthorized(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /history SERVER")
        return
    name = " ".join(context.args)
    records = context.bot_data["controller"].history(name)
    if len(records) < 2:
        await update.message.reply_text(f"Not enough history for {name} yet.")
        return
    lines = [f"{name} — last 7 days", ""]
    previous = records[0]
    for item in records[1:]:
        lines.append(f"{item['date']} +{max(0, item['used_gb'] - previous['used_gb']):g}GB")
        previous = item
    await update.message.reply_text("\n".join(lines))


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(context.bot_data["config"], update) or not update.message:
        if update.message:
            await reject_unauthorized(update)
        return
    results = await asyncio.to_thread(context.bot_data["controller"].query_all)
    await update.message.reply_text(format_report(results))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not authorized(context.bot_data["config"], update):
        return
    await query.answer()
    controller = context.bot_data["controller"]
    if query.data in {"menu:status", "menu:list"}:
        await query.edit_message_text("Select a VPS:", reply_markup=server_keyboard(controller))
    elif query.data == "menu:report":
        results = await asyncio.to_thread(controller.query_all)
        await query.edit_message_text(format_report(results))
    elif query.data and query.data.startswith("server:"):
        try:
            payload = await asyncio.to_thread(controller.query, controller.server(query.data[7:]))
            await query.edit_message_text(format_status(payload))
        except ControllerError as exc:
            await query.edit_message_text(f"⚠️ {exc}")


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Open the VPS Traffic Alert menu"),
        BotCommand("status", "Show status; optionally add a server name"),
        BotCommand("report", "Show the current multi-VPS report"),
        BotCommand("history", "Show seven-day history for a server"),
        BotCommand("chatid", "Show this Telegram chat ID"),
    ])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    error_info = (type(error), error, error.__traceback__) if error else None
    LOGGER.error("Telegram update failed: %r", update, exc_info=error_info)


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


async def send_daily(config: dict[str, Any], controller: Controller, token: str) -> None:
    application = Application.builder().token(token).build()
    results = await asyncio.to_thread(controller.query_all)
    deltas: dict[str, float] = {}
    for server, result in results:
        if isinstance(result, dict):
            history = controller.history(server.name, days=1)
            if history:
                deltas[server.name] = max(
                    0, result["traffic"]["used_gb"] - history[-1]["used_gb"]
                )
            controller.append_history(result)
    async with application.bot:
        for chat_id in config["allowed_chat_ids"]:
            await application.bot.send_message(
                chat_id=chat_id, text=format_report(results, deltas)
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--daily-report", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get(config.get("token_env", "VPS_TRAFFIC_BOT_TOKEN"))
    if not token:
        raise SystemExit("Telegram token environment variable is not set")
    controller = Controller(config)
    if args.daily_report:
        asyncio.run(send_daily(config, controller, token))
        return
    application = Application.builder().token(token).post_init(post_init).build()
    application.bot_data.update(config=config, controller=controller)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("chatid", chat_id_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_error_handler(error_handler)
    application.run_polling()


if __name__ == "__main__":
    main()
