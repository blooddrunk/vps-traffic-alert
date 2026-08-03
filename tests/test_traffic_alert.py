import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "src" / "traffic_alert.py"
SPEC = importlib.util.spec_from_file_location("traffic_alert", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CycleBoundsTests(unittest.TestCase):
    def test_cycle_after_reset_day(self):
        start, end = MODULE.cycle_bounds(datetime(2026, 7, 20, 12, 0), 7)
        self.assertEqual(str(start), "2026-07-07")
        self.assertEqual(str(end), "2026-08-07")

    def test_cycle_before_reset_day(self):
        start, end = MODULE.cycle_bounds(datetime(2026, 7, 5, 12, 0), 7)
        self.assertEqual(str(start), "2026-06-07")
        self.assertEqual(str(end), "2026-07-07")

    def test_day_31_clamps_for_short_month(self):
        start, end = MODULE.cycle_bounds(datetime(2026, 2, 28, 12, 0), 31)
        self.assertEqual(str(start), "2026-02-28")
        self.assertEqual(str(end), "2026-03-31")

    def test_december_rollover(self):
        start, end = MODULE.cycle_bounds(datetime(2026, 12, 26, 0, 0), 26)
        self.assertEqual(str(start), "2026-12-26")
        self.assertEqual(str(end), "2027-01-26")


class FormattingTests(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(MODULE.format_bytes(300_000_000_000), "300.00 GB")
        self.assertEqual(MODULE.format_bytes(5_900_000_000_000), "5.90 TB")

    def test_json_status_schema_and_next_threshold(self):
        config = {
            "server_name": "NoSla", "interface": "eth0", "quota_gb": 1000.0,
            "reset_day": 7, "timezone": "Asia/Shanghai", "traffic_mode": "total",
            "thresholds": [70, 80, 90],
        }
        payload = MODULE.make_status_payload(
            config,
            datetime(2026, 8, 3, 9, tzinfo=timezone.utc),
            datetime(2026, 7, 7).date(),
            datetime(2026, 8, 7).date(),
            356_420_000_000,
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["server"], {"name": "NoSla", "interface": "eth0"})
        self.assertEqual(payload["traffic"]["used_gb"], 356.42)
        self.assertEqual(payload["traffic"]["remaining_gb"], 643.58)
        self.assertEqual(payload["threshold"], {"next": 70, "remaining_gb": 343.58})
        self.assertIn("T", payload["timestamp"])

    def test_json_status_has_null_next_threshold_after_all_alerts(self):
        config = {
            "server_name": "full", "interface": "eth0", "quota_gb": 10.0,
            "reset_day": 1, "timezone": "UTC", "traffic_mode": "total",
            "thresholds": [70, 100],
        }
        payload = MODULE.make_status_payload(
            config, datetime.now(timezone.utc), datetime(2026, 8, 1).date(),
            datetime(2026, 9, 1).date(), 11_000_000_000,
        )
        self.assertIsNone(payload["threshold"]["next"])
        self.assertEqual(payload["traffic"]["remaining_gb"], 0)


class ConfigurationTests(unittest.TestCase):
    def test_controller_only_agent_does_not_require_telegram_credentials(self):
        config = MODULE.validate_config({
            "server_name": "agent", "interface": "eth0", "quota_gb": 100,
            "reset_day": 1, "timezone": "UTC", "agent": {"enabled": True},
        })
        self.assertEqual(config["telegram"], {"bot_token": "", "chat_id": ""})


if __name__ == "__main__":
    unittest.main()
