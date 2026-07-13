import csv
import json
import tempfile
import unittest
from pathlib import Path

from collector.audit import audit_dataset, classify_signal, evaluate_gates


def month_sequence(count, start_year=2020, start_month=1):
    result = []
    year, month = start_year, start_month
    for _ in range(count):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


class AuditDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "data" / "series").mkdir(parents=True)
        (self.root / "keywords.csv").write_text(
            "Keyword_ID,Keyword_TH,Tier,Segment,Factor\nFP001,ทดสอบ,T1,Formal,Pull\n",
            encoding="utf-8",
        )
        self.catalog = {"updated_at": "2026-07-01T00:00:00", "series": {}}

    def tearDown(self):
        self.tempdir.cleanup()

    def write_series(self, geo, values, months=None):
        months = months or month_sequence(len(values))
        key = f"FP001__{geo}"
        path = self.root / "data" / "series" / f"{key}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Month", "Value"])
            writer.writerows(zip(months, values))
        self.catalog["series"][key] = {
            "months": len(months),
            "first": months[0],
            "last": months[-1],
            "fetched_on": "2026-07-01",
            "note": "unit test source",
        }

    def write_no_data(self, geo, fetched_on="2026-07-13", **overrides):
        key = f"FP001__{geo}"
        meta = {
            "status": "no_data",
            "keyword": "ทดสอบ",
            "timeframe": f"2004-01-01 {fetched_on}",
            "months": 0,
            "first": None,
            "last": None,
            "fetched_on": fetched_on,
            "fetched_at": f"{fetched_on}T12:00:00",
            "note": "Google Trends returned no observations for the canonical window",
        }
        meta.update(overrides)
        self.catalog["series"][key] = meta

    def save_catalog(self):
        (self.root / "data" / "catalog.json").write_text(
            json.dumps(self.catalog, ensure_ascii=False), encoding="utf-8"
        )

    def test_signal_thresholds_are_exact(self):
        self.assertEqual(classify_signal([1] * 64), ("VERY_GOOD", 0, 64))
        self.assertEqual(classify_signal([0] * 16 + [1] * 48), ("ACCEPTABLE", 16, 64))
        self.assertEqual(classify_signal([0] * 17 + [1] * 47), ("WEAK", 17, 64))

    def test_missing_is_distinct_from_valid_all_zero_signal(self):
        self.write_series("TH", [0] * 64)
        self.write_series("TH-30", [1] * 64)
        self.write_series("TH-31", [0] * 16 + [1] * 48)
        self.write_series("TH-34", [0] * 17 + [1] * 47)
        self.save_catalog()

        report = audit_dataset(self.root)

        self.assertTrue(report["structural_ok"])
        self.assertEqual(report["expected_raw_series"], 6)
        self.assertEqual(report["available_raw_series"], 4)
        self.assertEqual(report["missing_raw_series"], 2)
        self.assertEqual(
            report["missing_raw_series_keys"], ["FP001__TH-40", "FP001__TH-41"]
        )
        self.assertEqual(report["all_zero_raw_series"], 1)
        self.assertEqual(report["per_series"]["FP001__TH"]["status"], "available")
        self.assertEqual(report["per_series"]["FP001__TH"]["signal_status"], "ALL_ZERO")
        self.assertEqual(report["per_series"]["FP001__TH-40"]["signal_status"], "MISSING")
        self.assertEqual(
            report["signal_tiers"], {"VERY_GOOD": 1, "ACCEPTABLE": 1, "WEAK": 2}
        )
        self.assertEqual(
            report["per_series"]["FP001__TH-30"]["source_note"], "unit test source"
        )

    def test_gap_is_a_structural_error_and_strict_gate_fails(self):
        self.write_series("TH", [1, 2], months=["2026-01", "2026-03"])
        self.save_catalog()

        report = audit_dataset(self.root)
        gate = evaluate_gates(report, strict=True)

        self.assertFalse(report["structural_ok"])
        self.assertTrue(any("non-contiguous" in error for error in report["structural_errors"]))
        self.assertFalse(gate["structural_pass"])
        self.assertFalse(gate["pass"])

    def test_valid_confirmed_no_data_completes_release_gate(self):
        self.write_series("TH", [1], months=["2026-06"])
        for geo in ("TH-30", "TH-31", "TH-34", "TH-40", "TH-41"):
            self.write_no_data(geo)
        self.save_catalog()
        report = audit_dataset(self.root)

        gate = evaluate_gates(report, strict=True, require_latest="2026-06")
        self.assertTrue(report["structural_ok"])
        self.assertEqual(report["confirmed_no_data_raw_series"], 5)
        self.assertEqual(report["missing_raw_series"], 0)
        self.assertEqual(report["per_series"]["FP001__TH-30"]["status"], "no_data")
        self.assertEqual(report["per_series"]["FP001__TH-30"]["signal_status"], "NO_DATA")
        self.assertTrue(gate["complete_release_pass"])
        self.assertTrue(gate["pass"])

    def test_stale_no_data_fails_release_gate_distinctly(self):
        self.write_series("TH", [1], months=["2026-06"])
        for geo in ("TH-30", "TH-31", "TH-34", "TH-40"):
            self.write_no_data(geo)
        self.write_no_data("TH-41", fetched_on="2026-06-30")
        self.save_catalog()

        report = audit_dataset(self.root)
        gate = evaluate_gates(report, require_latest="2026-06")

        self.assertTrue(report["structural_ok"])
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["stale_available_raw_series"], 0)
        self.assertEqual(gate["stale_no_data_raw_series_keys"], ["FP001__TH-41"])

    def test_malformed_no_data_is_structurally_invalid(self):
        self.write_no_data("TH-30", months=1)
        self.save_catalog()

        report = audit_dataset(self.root)
        gate = evaluate_gates(report, strict=True, require_latest="2026-06")

        self.assertFalse(report["structural_ok"])
        self.assertEqual(report["confirmed_no_data_raw_series"], 0)
        self.assertEqual(report["invalid_no_data_raw_series_keys"], ["FP001__TH-30"])
        self.assertEqual(report["per_series"]["FP001__TH-30"]["status"], "invalid_no_data")
        self.assertEqual(gate["invalid_no_data_raw_series_keys"], ["FP001__TH-30"])
        self.assertFalse(gate["pass"])

    def test_truly_missing_cells_fail_complete_release_gate(self):
        self.write_series("TH", [1], months=["2026-06"])
        self.save_catalog()
        report = audit_dataset(self.root)

        gate = evaluate_gates(report, require_latest="2026-06")

        self.assertFalse(gate["pass"])
        self.assertEqual(gate["stale_available_raw_series"], 0)
        self.assertEqual(gate["missing_raw_series"], 5)
        self.assertEqual(gate["invalid_no_data_raw_series"], 0)


if __name__ == "__main__":
    unittest.main()
