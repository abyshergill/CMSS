"""CSV-injection guard, brute-force policy, SVG charts, statistics. Pure Python."""
import unittest
from datetime import date, datetime, timedelta

from core import charts, safe, stats


class CsvInjection(unittest.TestCase):
    def test_formula_prefixes_are_neutralised(self):
        for bad in ("=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\t=x", "\r=x", "=HYPERLINK(\"http://evil\")"):
            self.assertTrue(safe.csv_safe(bad).startswith("'"), bad)

    def test_normal_values_untouched(self):
        for ok in ("Replace belt", "", "2026-10-27", 5, None, "a=b"):
            self.assertEqual(safe.csv_safe(ok), ok)


class BruteForce(unittest.TestCase):
    def test_policy(self):
        self.assertFalse(safe.is_locked(4, 0)); self.assertTrue(safe.is_locked(5, 0))
        self.assertFalse(safe.is_locked(0, 19)); self.assertTrue(safe.is_locked(0, 20))

    def test_retry_after(self):
        now = datetime(2026, 10, 27, 12, 0, 0)
        times = [now - timedelta(minutes=m) for m in (1, 5, 14)]
        self.assertAlmostEqual(safe.retry_after_seconds(times, now, 900), 61, delta=2)
        self.assertEqual(safe.retry_after_seconds([now - timedelta(minutes=20)], now, 900), 0)
        self.assertEqual(safe.retry_after_seconds([], now, 900), 0)


class Charts(unittest.TestCase):
    def test_text_is_escaped_everywhere(self):
        evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
        for svg in (charts.donut([(evil, 3, "c1")], center_text=evil, label=evil), charts.bars([(evil, 2)], label=evil),
                    charts.hbars([(evil, 2)], label=evil), charts.ring(50, label=evil)):
            self.assertNotIn("<script", svg); self.assertNotIn("<img", svg)
            self.assertNotIn('onerror=alert(2)>', svg)

    def test_css_class_is_escaped_too(self):
        svg = charts.donut([("a", 1, '"><script>')])
        self.assertNotIn("<script", svg)

    def test_empty_and_zero_data_do_not_crash(self):
        self.assertIn("<svg", charts.donut([])); self.assertIn("<svg", charts.donut([("a", 0, "c1")]))
        self.assertIn("<svg", charts.bars([])); self.assertIn("<svg", charts.bars([("a", 0)]))
        self.assertIn("<svg", charts.hbars([]))

    def test_bad_numbers_are_treated_as_zero(self):
        svg = charts.bars([("a", float("nan")), ("b", float("inf")), ("c", -5), ("d", 2)])
        self.assertNotIn("nan", svg.lower()); self.assertNotIn("inf", svg.lower())

    def test_donut_segments_add_up_to_100(self):
        svg = charts.donut([("a", 1, "c1"), ("b", 3, "c2")])
        self.assertIn('stroke-dasharray="25.000 75.000"', svg); self.assertIn('stroke-dasharray="75.000 25.000"', svg)

    def test_ring_is_clamped(self):
        self.assertIn("100%", charts.ring(250)); self.assertIn("0%", charts.ring(-5))

    def test_no_scripts_or_inline_styles_in_output(self):
        for svg in (charts.donut([("a", 1, "c1")], "x"), charts.bars([("a", 1)]), charts.hbars([("a", 1)]), charts.ring(10)):
            self.assertNotIn("<script", svg); self.assertNotIn("style=", svg); self.assertNotIn("javascript:", svg)


class Stats(unittest.TestCase):
    T = date(2026, 10, 27)   # a Tuesday

    def test_weekly_buckets(self):
        days = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 19), date(2026, 9, 1), date(2026, 10, 28)]
        b = stats.weekly_buckets(days, self.T, weeks=3)
        self.assertEqual([n for _, n in b], [0, 1, 2])           # 28 Oct is in the future -> ignored, 1 Sep too old
        self.assertEqual(b[-1][0], date(2026, 10, 26))

    def test_on_time_split(self):
        d = date(2026, 10, 1)
        self.assertEqual(stats.on_time_split([(d, d), (d, d + timedelta(days=1)), (d + timedelta(days=2), d)]), (2, 1))

    def test_top_counts_and_percent(self):
        self.assertEqual(stats.top_counts(["a", "b", "a", "", None], limit=2, blank="x"), [("a", 2), ("x", 2)])
        self.assertEqual(stats.percent(1, 3), 33); self.assertEqual(stats.percent(0, 0), 0)

    def test_weighted_progress_judges_work_by_effort_not_task_count(self):
        p = stats.weighted_progress([(1, True), (1, True), (8, False)])
        self.assertEqual((p["total"], p["done"], p["pct"]), (3, 2, 20))        # 2 of 3 tasks, but only 2 of 10 points
        self.assertEqual(stats.weighted_progress([])["pct"], 0)
        self.assertEqual(stats.weighted_progress([(5, True)])["pct"], 100)


if __name__ == "__main__":
    unittest.main()
