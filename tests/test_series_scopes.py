import csv
import unittest
from collections import defaultdict
from pathlib import Path

from analysis.core import ISAN5, SCOPES
from analysis.pipeline import SERIES_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "derived" / "sa_pipeline_v3"


class SeriesScopeTests(unittest.TestCase):
    """Provinces are charted directly, so their weakness must travel with them."""

    @classmethod
    def setUpClass(cls):
        with (DERIVED / "series.csv").open(encoding="utf-8", newline="") as handle:
            cls.series = list(csv.DictReader(handle))
        with (DERIVED / "quality_flags.csv").open(encoding="utf-8", newline="") as handle:
            cls.quality = list(csv.DictReader(handle))

    def test_every_province_is_its_own_scope(self):
        self.assertEqual(
            tuple(SCOPES), ("TH", "REG_ISAN5") + ISAN5
        )
        for geo in ISAN5:
            self.assertEqual(SCOPES[geo]["geos"], (geo,))
            # Provincial data before 2014 is a Google geo break, not a signal.
            self.assertEqual(SCOPES[geo]["start"], "2014-01")

    def test_series_carries_the_quality_verdict(self):
        # Without this column a chart on บุรีรัมย์ looks exactly like one on TH,
        # and the reader would have to open quality_flags.csv to learn otherwise.
        self.assertEqual(SERIES_COLUMNS[-1], "Quality_Status")
        self.assertIn("Quality_Status", self.series[0])

    def test_the_verdict_matches_the_sidecar_for_every_case_and_scope(self):
        sidecar = {
            (row["Scope"], row["Case_ID"]): row["Quality_Status"] for row in self.quality
        }
        seen = defaultdict(set)
        for row in self.series:
            key = (row["Scope"], row["Case_ID"])
            seen[key].add(row["Quality_Status"])

        self.assertEqual(set(seen), set(sidecar))
        for key, statuses in seen.items():
            # One verdict per case and scope, repeated on each of its months.
            self.assertEqual(len(statuses), 1, key)
            self.assertEqual(statuses.pop(), sidecar[key], key)

    def test_every_scope_actually_produced_rows(self):
        scopes = {row["Scope"] for row in self.series}

        self.assertEqual(scopes, set(SCOPES))


if __name__ == "__main__":
    unittest.main()
