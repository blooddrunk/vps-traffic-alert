import importlib.util
import sys
import unittest
from datetime import datetime
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


if __name__ == "__main__":
    unittest.main()
