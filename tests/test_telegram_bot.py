import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "telegram-bot" / "bot.py"


def load_bot_module():
    """Load bot.py without requiring the optional Telegram package in CI."""
    telegram_module = types.ModuleType("telegram")
    telegram_module.BotCommand = object
    telegram_module.InlineKeyboardButton = object
    telegram_module.InlineKeyboardMarkup = object
    telegram_module.Update = object

    extensions_module = types.ModuleType("telegram.ext")
    extensions_module.Application = object
    extensions_module.CallbackQueryHandler = object
    extensions_module.CommandHandler = object
    extensions_module.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)

    module_name = "telegram_bot"
    saved_modules = {
        name: sys.modules.get(name)
        for name in ("telegram", "telegram.ext", module_name)
    }
    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.ext"] = extensions_module
    try:
        spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved_modules.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


MODULE = load_bot_module()


class ControllerTests(unittest.TestCase):
    def test_default_history_path_matches_agent_data_root(self):
        controller = MODULE.Controller({"servers": [{"name": "NoSla", "host": "example"}]})
        self.assertEqual(
            controller.history_path,
            Path("/var/lib/vps-traffic-alert/history.jsonl"),
        )

    def test_query_uses_status_json_over_ssh(self):
        payload = {"schema_version": 1, "server": {"name": "NoSla"}}
        controller = MODULE.Controller(
            {"servers": [{"name": "NoSla", "host": "example", "user": "monitor"}]}
        )
        result = SimpleNamespace(stdout=json.dumps(payload))

        with patch.object(MODULE.subprocess, "run", return_value=result) as run:
            self.assertEqual(controller.query(controller.server("NoSla")), payload)

        command = run.call_args.args[0]
        self.assertIn("monitor@example", command)
        self.assertEqual(command[-1], "vps-traffic-alert status --json")

    def test_history_uses_status_date_and_skips_malformed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.jsonl"
            controller = MODULE.Controller(
                {
                    "history_path": str(history_path),
                    "servers": [{"name": "NoSla", "host": "example"}],
                }
            )
            controller.append_history(
                {
                    "timestamp": "2026-08-03T09:00:00+08:00",
                    "server": {"name": "NoSla"},
                    "traffic": {"used_gb": 356.42},
                }
            )
            with history_path.open("a", encoding="utf-8") as handle:
                handle.write("not-json\n")
                handle.write('{"server":"NoSla","date":"2026-08-04"}\n')

            records = controller.history("NoSla")

        self.assertEqual(records, [{"date": "2026-08-03", "server": "NoSla", "used_gb": 356.42}])


class HistoryCommandTests(unittest.TestCase):
    def test_history_command_requests_baseline_plus_seven_days(self):
        class FakeController:
            def __init__(self):
                self.calls = []

            def history(self, name, days=7):
                self.calls.append((name, days))
                return [
                    {"date": f"2026-08-{day:02d}", "used_gb": float(day)}
                    for day in range(1, 9)
                ]

        class FakeMessage:
            def __init__(self):
                self.text = ""

            async def reply_text(self, text):
                self.text = text

        message = FakeMessage()
        update = SimpleNamespace(
            message=message,
            effective_message=message,
            effective_chat=SimpleNamespace(id=123),
        )
        controller = FakeController()
        context = SimpleNamespace(
            args=["NoSla"],
            bot_data={
                "config": {"allowed_chat_ids": [123]},
                "controller": controller,
            },
        )

        asyncio.run(MODULE.history_command(update, context))

        self.assertEqual(controller.calls, [("NoSla", 8)])
        self.assertEqual(sum(" +" in line for line in message.text.splitlines()), 7)

    def test_cycle_reset_counts_current_snapshot_as_today(self):
        self.assertEqual(MODULE.traffic_delta(4.2, 356.42), 4.2)
        self.assertAlmostEqual(MODULE.traffic_delta(360.0, 356.42), 3.58)


if __name__ == "__main__":
    unittest.main()
