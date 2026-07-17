import csv
import json
import tempfile
import unittest
from pathlib import Path

from collector.build_site_data import build, isan_aggregate


class IsanAggregateTests(unittest.TestCase):
    def test_formula_is_unchanged_and_support_is_explicit(self):
        geo_map = {
            "TH-30": {"months": ["2026-01", "2026-02"], "values": [10, 20]},
            "TH-31": {"months": ["2026-01", "2026-02"], "values": [20, 20]},
            "TH-34": {"months": ["2026-01", "2026-02"], "values": [0, 0]},
        }

        result = isan_aggregate(geo_map)

        self.assertEqual(result["values"], [75.0, 100.0])
        self.assertEqual(result["support_n"], 2)
        self.assertEqual(result["support_geos"], ["TH-30", "TH-31"])
        self.assertEqual(result["support_total"], 5)


class BuildSiteDataTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "data" / "series").mkdir(parents=True)
        (self.root / "keywords.csv").write_text(
            (
                "Keyword_ID,Keyword_TH,Tier,Segment,Factor,Family_Name_TH\n"
                "FP001,ทดสอบ,T1,Formal,Pull,ครอบครัวทดสอบ\n"
            ),
            encoding="utf-8",
        )
        self.catalog = {"updated_at": "2026-07-01T00:00:00", "series": {}}
        self.write_series("TH-30", [10, 20])
        self.write_series("TH-31", [20, 20])
        (self.root / "data" / "catalog.json").write_text(
            json.dumps(self.catalog, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def write_series(self, geo, values):
        key = f"FP001__{geo}"
        months = []
        year, month = 2014, 1
        while (year, month) <= (2026, 2):
            months.append(f"{year:04d}-{month:02d}")
            month += 1
            if month == 13:
                year += 1
                month = 1
        values = [0] * (len(months) - len(values)) + values
        path = self.root / "data" / "series" / f"{key}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Month", "Value"])
            writer.writerows(zip(months, values))
        self.catalog["series"][key] = {
            "status": "available",
            "keyword": "ทดสอบ",
            "timeframe": "2004-01-01 2026-07-01",
            "months": len(months),
            "first": "2014-01",
            "last": "2026-02",
            "fetched_on": "2026-07-01",
            "fetched_at": "2026-07-01T12:00:00",
            "note": f"source {geo}",
        }

    def test_build_embeds_health_and_is_deterministic(self):
        payload = build(root=self.root)
        first = (self.root / "data.js").read_bytes()
        second_payload = build(root=self.root)
        second = (self.root / "data.js").read_bytes()

        self.assertEqual(first, second)
        self.assertEqual(payload, second_payload)
        self.assertEqual(payload["geos"]["ISAN"], "อีสาน (คอมโพสิต)")
        self.assertEqual(payload["series"]["FP001"]["ISAN"]["support_n"], 2)
        self.assertEqual(payload["health"]["expected_raw_series"], 6)
        self.assertEqual(payload["health"]["available_raw_series"], 2)
        self.assertEqual(payload["health"]["data_end"], "2026-02")
        self.assertEqual(payload["health"]["catalog_updated_at"], "2026-07-01T00:00:00")
        self.assertTrue(build(root=self.root, check=True))

        (self.root / "data.js").write_text("stale", encoding="utf-8")
        self.assertFalse(build(root=self.root, check=True))


if __name__ == "__main__":
    unittest.main()
