"""Pure Python: the list of 'days this job should NOT happen'."""
import sys, unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.datelist import MAX_DATES, format_date_list, parse_date_list   # noqa: E402


class Parse(unittest.TestCase):
    def test_separate_days_in_any_order(self):
        self.assertEqual(parse_date_list("2026-12-25, 2026-11-03,2026-11-10"), [date(2026, 11, 3), date(2026, 11, 10), date(2026, 12, 25)])

    def test_days_do_not_have_to_be_consecutive_or_listed_in_ranges(self):
        r = parse_date_list("2026-11-01 2026-11-03 2026-11-09")
        self.assertEqual([d.day for d in r], [1, 3, 9])

    def test_separators(self):
        self.assertEqual(len(parse_date_list("2026-01-01;2026-01-02\n2026-01-03\t2026-01-04 , ,2026-01-05")), 5)

    def test_duplicates_collapse(self):
        self.assertEqual(parse_date_list("2026-01-01, 2026-01-01"), [date(2026, 1, 1)])

    def test_empty_inputs(self):
        for t in (None, "", "   ", " , ; "):
            self.assertEqual(parse_date_list(t), [])

    def test_bad_formats_are_explained(self):
        for bad in ("03/11/2026", "2026-1-5", "tomorrow", "2026-11-03x", "20261103", "2026-11-03, oops"):
            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                parse_date_list(bad)

    def test_impossible_dates(self):
        for bad in ("2026-02-30", "2026-13-01", "2026-00-10", "2027-02-29"):
            with self.assertRaisesRegex(ValueError, "real calendar date"):
                parse_date_list(bad)

    def test_absurd_years_refused(self):
        for bad in ("0001-01-01", "9999-12-31", "1999-12-31"):
            with self.assertRaisesRegex(ValueError, "outside the years"):
                parse_date_list(bad)

    def test_limit(self):
        ok = ",".join(date.fromordinal(date(2026, 1, 1).toordinal() + i).isoformat() for i in range(MAX_DATES))
        self.assertEqual(len(parse_date_list(ok)), MAX_DATES)
        with self.assertRaisesRegex(ValueError, "at most"):
            parse_date_list(ok + ",2030-01-01")

    def test_long_junk_is_cut_in_the_message_not_echoed_whole(self):
        with self.assertRaises(ValueError) as cm:
            parse_date_list("x" * 5000)
        self.assertLess(len(str(cm.exception)), 120)

    def test_round_trip(self):
        ds = [date(2026, 11, 10), date(2026, 11, 3)]
        self.assertEqual(format_date_list(ds), "2026-11-03, 2026-11-10")
        self.assertEqual(parse_date_list(format_date_list(ds)), sorted(ds))


if __name__ == "__main__":
    unittest.main()
