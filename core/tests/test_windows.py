"""When may a repeating job be marked done, year intervals, and the checkpoint helpers. Pure Python:
python -m unittest core.tests.test_windows -v"""
import unittest
from datetime import date, timedelta
from itertools import islice
from types import SimpleNamespace as NS

from core import checklist, engine, recurrence as rec


def rep(unit, every=1):
    return NS(kind="repeat", unit=unit, every=every, start_date=date(2026, 1, 1), end_date=None)


ONCE = NS(kind="once", unit="", every=None, start_date=date(2026, 1, 1), end_date=None)


class Windows(unittest.TestCase):
    def test_daily_is_that_day_only(self):
        d = date(2026, 10, 28)
        self.assertEqual(engine.window(rep("day"), d), (d, d))
        self.assertEqual(engine.finish_state(rep("day"), d, d)[0], "ok")
        self.assertEqual(engine.finish_state(rep("day"), d, d - timedelta(days=1))[0], "early")       # the day before = in advance
        self.assertEqual(engine.finish_state(rep("day"), d, d + timedelta(days=1))[0], "closed")

    def test_weekly_is_monday_to_sunday(self):
        wed = date(2026, 10, 28)                                  # a Wednesday
        self.assertEqual(wed.weekday(), 2)
        self.assertEqual(engine.window(rep("week"), wed), (date(2026, 10, 26), date(2026, 11, 1)))
        st = lambda today: engine.finish_state(rep("week"), wed, today)[0]
        self.assertEqual(st(date(2026, 10, 25)), "early")          # Sunday before the week
        self.assertEqual(st(date(2026, 10, 26)), "ok")             # Monday of that week: before the due day, but allowed
        self.assertEqual(st(date(2026, 11, 1)), "ok")              # Sunday: last day
        self.assertEqual(st(date(2026, 11, 2)), "closed")

    def test_every_two_weeks_still_uses_the_calendar_week_of_the_date(self):
        self.assertEqual(engine.window(rep("week", 2), date(2026, 11, 11)), (date(2026, 11, 9), date(2026, 11, 15)))

    def test_monthly_is_that_calendar_month(self):
        self.assertEqual(engine.window(rep("month"), date(2026, 2, 10)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(engine.window(rep("month"), date(2028, 2, 10)), (date(2028, 2, 1), date(2028, 2, 29)))        # leap year
        self.assertEqual(engine.window(rep("month", 3), date(2026, 12, 31)), (date(2026, 12, 1), date(2026, 12, 31)))   # quarterly: its month
        st = lambda today: engine.finish_state(rep("month"), date(2026, 11, 15), today)[0]
        self.assertEqual((st(date(2026, 10, 31)), st(date(2026, 11, 1)), st(date(2026, 11, 30)), st(date(2026, 12, 1))),
                         ("early", "ok", "ok", "closed"))

    def test_yearly_is_that_calendar_year(self):
        for j in (rep("year"), rep("month", 12), rep("month", 24)):                 # "every 12 months" is a yearly job too
            self.assertEqual(engine.window(j, date(2026, 5, 3)), (date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual(engine.finish_state(rep("year"), date(2027, 3, 1), date(2026, 12, 31))[0], "early")
        self.assertEqual(engine.finish_state(rep("year"), date(2026, 3, 1), date(2027, 1, 1))[0], "closed")
        self.assertEqual(engine.period_kind("month", 6), "month")

    def test_one_off_jobs_have_no_window(self):
        self.assertEqual(engine.window(ONCE, date(2026, 1, 1)), (None, None))
        self.assertEqual(engine.finish_state(ONCE, date(2030, 1, 1), date(2026, 1, 1)), ("ok", None, None))     # may be done early

    def test_window_always_contains_the_planned_day(self):
        for unit in ("day", "week", "month", "year"):
            for n in range(0, 800, 7):
                when = date(2026, 1, 1) + timedelta(days=n)
                lo, hi = engine.window(rep(unit), when)
                self.assertTrue(lo <= when <= hi, (unit, when))

    def test_words_for_the_screen(self):
        self.assertEqual([engine.PERIOD_WORDS[engine.period_kind(u, e)] for u, e in (("day", 1), ("week", 1), ("month", 1), ("year", 1))],
                         ["that day", "that week (Mon-Sun)", "that month", "that year"])


class YearInterval(unittest.TestCase):
    def test_yearly_dates_and_leap_day(self):
        got = list(islice(rec.occurrences(date(2024, 2, 29), 1, "year", date(2027, 1, 1)), 3))
        self.assertEqual(got, [date(2027, 2, 28), date(2028, 2, 29), date(2029, 2, 28)])        # no drift, 29 Feb comes back in leap years

    def test_every_two_years_and_fast_forward_matches_brute_force(self):
        anchor = date(2020, 6, 15)
        brute = [rec.nth(anchor, 2, "year", k) for k in range(40)]
        for start in (date(2020, 1, 1), date(2031, 6, 15), date(2055, 12, 1)):
            self.assertEqual(list(islice(rec.occurrences(anchor, 2, "year", start), 3)), [x for x in brute if x >= start][:3])

    def test_describe(self):
        self.assertEqual(rec.describe(1, "year"), "Every year")
        self.assertEqual(rec.describe(2, "year"), "Every 2 years")

    def test_engine_expands_a_yearly_job(self):
        j = rep("year")
        j.start_date = date(2025, 3, 10)
        got = engine.expand(j, date(2025, 1, 1), date(2028, 12, 31), date(2026, 6, 1))
        self.assertEqual([(o.when, o.status) for o in got],
                         [(date(2025, 3, 10), "overdue"), (date(2026, 3, 10), "overdue"), (date(2027, 3, 10), "upcoming"), (date(2028, 3, 10), "upcoming")])


class Checkpoints(unittest.TestCase):
    def test_one_per_line_bullets_blanks_and_repeats(self):
        self.assertEqual(checklist.parse_checkpoints("- Oil level OK\n\u2022 No leaks \n\n  Guard   fitted\r\noil level ok\n"),
                         ["Oil level OK", "No leaks", "Guard fitted"])
        self.assertEqual(checklist.parse_checkpoints(""), [])
        self.assertEqual(checklist.parse_checkpoints(None), [])

    def test_limits(self):
        with self.assertRaises(ValueError):
            checklist.parse_checkpoints("\n".join(f"item {i}" for i in range(checklist.MAX_ITEMS + 1)))
        self.assertEqual(len(checklist.parse_checkpoints("\n".join(f"item {i}" for i in range(checklist.MAX_ITEMS)))), checklist.MAX_ITEMS)
        with self.assertRaises(ValueError):
            checklist.parse_checkpoints("x" * (checklist.MAX_LEN + 1))

    def test_round_trip(self):
        items = ["a", "b c"]
        self.assertEqual(checklist.parse_checkpoints(checklist.format_checkpoints(items)), items)

    def test_snapshot_needs_every_box(self):
        items = ["one", "two", "three"]
        snap, missing = checklist.tick_snapshot(items, ["0", "1", "2"])
        self.assertEqual((missing, [s["ok"] for s in snap], [s["text"] for s in snap]), ([], [True] * 3, items))
        snap, missing = checklist.tick_snapshot(items, ["0", "2"])
        self.assertEqual(missing, ["two"])

    def test_forged_values_do_not_count(self):
        _, missing = checklist.tick_snapshot(["one", "two"], ["0", "7", "-1", "abc", "", None, "1.5"])
        self.assertEqual(missing, ["two"])
        self.assertEqual(checklist.tick_snapshot([], ["0"]), ([], []))                  # nothing to check -> nothing missing


if __name__ == "__main__":
    unittest.main()
