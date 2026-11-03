"""Scheduling engine + date maths. Pure Python:  python -m unittest core.tests.test_engine -v"""
import unittest
from collections import namedtuple
from datetime import date, timedelta
from types import SimpleNamespace

from core import engine, recurrence as rec

T = date(2026, 10, 27)
d = lambda n: T + timedelta(days=n)
R = namedtuple("R", "start_date end_date reason")


def job(kind="repeat", start=0, every=1, unit="week", end=None):
    return SimpleNamespace(kind=kind, start_date=d(start), every=every if kind == "repeat" else None,
                           unit=unit if kind == "repeat" else "", end_date=d(end) if end is not None else None)


def run(j, lo=-20, hi=15, **kw):
    return engine.expand(j, d(lo), d(hi), T, **kw)


class DateMaths(unittest.TestCase):
    def test_month_end_clamps_without_drift(self):
        a = date(2026, 1, 31)
        self.assertEqual([rec.nth(a, 1, "month", k) for k in range(4)],
                         [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_leap_year(self):
        self.assertEqual(rec.nth(date(2028, 1, 31), 1, "month", 1), date(2028, 2, 29))

    def test_fast_forward_matches_brute_force(self):
        for a, every, unit in [(date(2020, 1, 31), 3, "month"), (date(2021, 3, 5), 2, "week"), (date(2019, 12, 1), 5, "day")]:
            for start in [date(2026, 10, 27), date(2027, 1, 1), a, a - timedelta(days=5)]:
                fast = next(rec.occurrences(a, every, unit, start))
                k = 0
                while rec.nth(a, every, unit, k) < start:
                    k += 1
                self.assertEqual(fast, rec.nth(a, every, unit, k))

    def test_describe(self):
        self.assertEqual(rec.describe(1, "week"), "Every week")
        self.assertEqual(rec.describe(3, "month"), "Every 3 months")


class NormalJobs(unittest.TestCase):
    def test_statuses(self):
        for start, st in ((3, "upcoming"), (0, "today"), (-2, "overdue")):
            self.assertEqual(run(job("once", start))[0].status, st)

    def test_old_one_off_never_falls_off(self):
        self.assertEqual(run(job("once", -400))[0].status, "overdue")

    def test_one_off_done_old_visible_only_on_the_day_it_was_done(self):
        j = job("once", -400)
        self.assertEqual(run(j, done_on={d(-400): T})[0].status, "done")
        self.assertEqual(run(j, done_on={d(-400): d(-1)}), [])

    def test_skip_ranges_do_not_touch_one_offs(self):
        self.assertEqual(len(run(job("once", 1), skip_ranges=[R(d(0), d(5), "")])), 1)


class RepeatingJobs(unittest.TestCase):
    def test_weekly(self):
        s = run(job(start=-14))
        self.assertEqual([o.when for o in s], [d(-14), d(-7), d(0), d(7), d(14)])
        self.assertEqual([o.status for o in s], ["overdue", "overdue", "today", "upcoming", "upcoming"])

    def test_done_one_date_only(self):
        self.assertEqual([o.status for o in run(job(start=-14), hi=8, done_on={d(-7): d(-6)})],
                         ["overdue", "done", "today", "upcoming"])

    def test_end_date(self):
        self.assertEqual(len(run(job(unit="day", start=-2, end=0), lo=-10, hi=10)), 3)

    def test_future_start(self):
        self.assertEqual([o.when for o in run(job(start=10), lo=-60, hi=12)], [d(10)])


class ExceptionsManyDays(unittest.TestCase):
    """The user asked: exceptions must be able to cover MORE THAN ONE day."""

    def test_multi_day_range_skips_every_date_inside(self):
        j = job(unit="day", start=-5)
        s = run(j, lo=-5, hi=5, skip_ranges=[R(d(-2), d(2), "holiday")])
        self.assertEqual([o.when for o in s], [d(-5), d(-4), d(-3), d(3), d(4), d(5)])

    def test_range_boundaries_are_inclusive(self):
        s = run(job(unit="day", start=0), lo=0, hi=4, skip_ranges=[R(d(1), d(3), "")])
        self.assertEqual([o.when for o in s], [d(0), d(4)])

    def test_single_day_is_just_a_range_of_one(self):
        s = run(job(unit="day", start=0), lo=0, hi=3, skip_ranges=[R(d(2), d(2), "")])
        self.assertEqual([o.when for o in s], [d(0), d(1), d(3)])

    def test_several_ranges_at_once(self):
        s = run(job(unit="day", start=0), lo=0, hi=9, skip_ranges=[R(d(1), d(2), ""), R(d(6), d(8), "")])
        self.assertEqual([o.when for o in s], [d(0), d(3), d(4), d(5), d(9)])

    def test_overlapping_ranges_are_fine(self):
        s = run(job(unit="day", start=0), lo=0, hi=6, skip_ranges=[R(d(1), d(4), ""), R(d(3), d(5), "")])
        self.assertEqual([o.when for o in s], [d(0), d(6)])

    def test_weekly_job_skips_only_the_dates_that_fall_in_range(self):
        s = run(job(start=-14), skip_ranges=[R(d(-9), d(-5), "")])      # covers -7 only
        self.assertEqual([o.when for o in s], [d(-14), d(0), d(7), d(14)])

    def test_skipped_shown_when_asked_with_reason_attached(self):
        s = run(job(unit="day", start=0), lo=0, hi=2, skip_ranges=[R(d(1), d(2), "shutdown")], include_skipped=True)
        self.assertEqual([(o.status, getattr(o.skip_range, "reason", None)) for o in s],
                         [("today", None), ("skipped", "shutdown"), ("skipped", "shutdown")])

    def test_completed_dates_stay_done_even_inside_a_range(self):
        s = run(job(unit="day", start=-2), lo=-2, hi=0, skip_ranges=[R(d(-2), d(0), "")],
                done_on={d(-1): d(-1)}, include_skipped=True)
        self.assertEqual([o.status for o in s], ["skipped", "done", "skipped"])

    def test_move_beats_skip(self):
        s = run(job(unit="day", start=0), lo=0, hi=5, skip_ranges=[R(d(0), d(5), "")], moves={d(1): d(4)})
        self.assertEqual([(o.orig, o.when, o.moved) for o in s], [(d(1), d(4), True)])

    def test_move_later_and_earlier(self):
        s = run(job(start=-7), lo=-10, hi=10, moves={d(-7): d(2), d(7): d(5)})
        self.assertEqual([(o.when, o.orig, o.status, o.moved) for o in s],
                         [(d(0), d(0), "today", False), (d(2), d(-7), "upcoming", True), (d(5), d(7), "upcoming", True)])

    def test_move_window_edges(self):
        j = job(start=40)
        self.assertEqual([o.when for o in run(j, lo=0, hi=7, moves={d(40): d(3)})], [d(3)])
        self.assertEqual(run(j, lo=20, hi=60, moves={d(40): d(3)})[0].when, d(47))

    def test_moved_then_done_uses_original_key(self):
        s = run(job(start=-7), lo=-10, hi=3, moves={d(-7): d(2)}, done_on={d(-7): d(1)})
        self.assertEqual([o.status for o in s if o.moved], ["done"])


class Validation(unittest.TestCase):
    def test_valid_occurrence(self):
        j = job(start=0, every=2)
        self.assertTrue(engine.valid_occurrence(j, d(28)))
        self.assertFalse(engine.valid_occurrence(j, d(21)))
        self.assertFalse(engine.valid_occurrence(j, d(-14)))

    def test_end_date_and_once(self):
        j = job(start=0, end=10)
        self.assertTrue(engine.valid_occurrence(j, d(7)))
        self.assertFalse(engine.valid_occurrence(j, d(14)))
        o = job("once", 3)
        self.assertTrue(engine.valid_occurrence(o, d(3)))
        self.assertFalse(engine.valid_occurrence(o, d(4)))

    def test_health(self):
        self.assertEqual(engine.health(["done", "overdue"]), "overdue")
        self.assertEqual(engine.health(["upcoming"]), "soon")
        self.assertEqual(engine.health(["done"]), "ok")
        self.assertEqual(engine.health([]), "ok")

    def test_very_old_series_is_fast(self):
        import time
        t0 = time.time()
        s = run(job(unit="day", start=-9000), lo=-60, hi=7)
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(len(s), 68)


if __name__ == "__main__":
    unittest.main()
