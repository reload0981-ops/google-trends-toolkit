import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from collector.check_keyword import check_keyword


def month_sequence(count: int, year: int, month: int) -> list[str]:
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


class CheckKeywordTest(unittest.TestCase):
    """The screening gates a maintainer applies before adopting a new keyword."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data" / "series").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, keyword_id, geo, values, start=(2014, 1)):
        months = month_sequence(len(values), *start)
        path = self.root / "data" / "series" / f"{keyword_id}__{geo}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Month", "Value"])
            writer.writerows(zip(months, values))

    def write_all_provinces(self, keyword_id, live_count, months=100):
        provinces = ["TH-30", "TH-31", "TH-34", "TH-40", "TH-41"]
        for index, geo in enumerate(provinces):
            values = [1.0] * months if index < live_count else [0.0] * months
            self.write(keyword_id, geo, values)

    def test_full_province_support_suggests_a_standalone_keyword(self):
        self.write("FP900", "TH", [1.0] * 100)
        self.write_all_provinces("FP900", live_count=5)

        result = check_keyword("FP900", self.root)

        self.assertTrue(result["national_pass"])
        self.assertEqual(result["regional_support"], 5)
        self.assertEqual(result["suggested_tier"], "T1")

    def test_partial_province_support_forces_a_family(self):
        self.write("FP901", "TH", [1.0] * 100)
        self.write_all_provinces("FP901", live_count=3)

        result = check_keyword("FP901", self.root)

        self.assertTrue(result["national_pass"])
        self.assertEqual(result["regional_support"], 3)
        self.assertEqual(result["suggested_tier"], "T2")

    def test_no_province_signal_is_context_only(self):
        self.write("FP902", "TH", [1.0] * 100)
        self.write_all_provinces("FP902", live_count=0)

        result = check_keyword("FP902", self.root)

        self.assertEqual(result["regional_support"], 0)
        self.assertEqual(result["suggested_tier"], "T3")

    def test_national_gate_rejects_more_than_a_quarter_zero_months(self):
        self.write("FP903", "TH", [0.0] * 26 + [1.0] * 74)
        self.write_all_provinces("FP903", live_count=5)

        result = check_keyword("FP903", self.root)

        self.assertFalse(result["national_pass"])
        self.assertEqual(result["national_zero_months"], 26)
        self.assertEqual(result["national_max_allowed"], 25)
        self.assertEqual(result["suggested_tier"], "ไม่ผ่าน")

    def test_national_gate_accepts_exactly_a_quarter(self):
        self.write("FP904", "TH", [0.0] * 25 + [1.0] * 75)
        self.write_all_provinces("FP904", live_count=5)

        self.assertTrue(check_keyword("FP904", self.root)["national_pass"])

    def test_months_before_2014_are_outside_the_window(self):
        # A national series reaching back to 2004 must not be judged on years
        # where provincial data does not exist yet.
        self.write("FP905", "TH", [0.0] * 120 + [1.0] * 100, start=(2004, 1))
        self.write_all_provinces("FP905", live_count=5)

        result = check_keyword("FP905", self.root)

        self.assertEqual(result["national_window_months"], 100)
        self.assertEqual(result["national_zero_months"], 0)
        self.assertTrue(result["national_pass"])

    def test_uncollected_keyword_reports_that_it_cannot_be_judged(self):
        result = check_keyword("FP906", self.root)

        self.assertFalse(result["collected"])
        self.assertIsNone(result["suggested_tier"])


if __name__ == "__main__":
    unittest.main()
