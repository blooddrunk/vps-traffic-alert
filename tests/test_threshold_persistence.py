import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "src" / "traffic_alert.py"
SPEC = importlib.util.spec_from_file_location("traffic_alert_persistence", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ThresholdPersistenceTests(unittest.TestCase):
    def test_successful_threshold_is_saved_before_later_notification_failure(self):
        config = {
            "server_name": "test-vps",
            "interface": "eth0",
            "quota_gb": 100,
            "reset_day": 1,
            "timezone": "UTC",
            "initial_used_gb": 0,
            "traffic_mode": "total",
            "thresholds": [70, 80, 90],
            "notify_cycle_reset": False,
            "telegram": {"bot_token": "token", "chat_id": "chat"},
        }

        cycle_start, _ = MODULE.cycle_bounds(MODULE.datetime.now(), 1)

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "cycle_id": cycle_start.isoformat(),
                        "last_total_bytes": 0,
                        "used_bytes": 0,
                        "alerted": [],
                        "last_check": "",
                    }
                ),
                encoding="utf-8",
            )

            calls = []

            def fake_send(_config, message):
                calls.append(message)
                if "reached 80%" in message:
                    raise MODULE.MonitorError("simulated Telegram failure")

            with (
                patch.object(MODULE, "STATE_PATH", state_path),
                patch.object(
                    MODULE,
                    "read_vnstat",
                    return_value=MODULE.TrafficCounters(
                        rx=81 * MODULE.DECIMAL_GB,
                        tx=0,
                    ),
                ),
                patch.object(MODULE, "send_telegram", side_effect=fake_send),
            ):
                with self.assertRaises(MODULE.MonitorError):
                    MODULE.run_check(config)

            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["alerted"], [70])
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
