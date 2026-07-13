import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from collector import collect
from collector.collect import BASE_SLEEP, CANONICAL_START, validate_collection_policy
from collector.ingest import (
    NO_DATA_MANIFEST_SCHEMA,
    parse_no_data_manifest,
    validate_canonical_coverage,
)
from collector.make_jobs import validate_collection_window


ROOT = Path(__file__).resolve().parents[1]


def month_range(start, end):
    year, month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    result = []
    while (year, month) <= (end_year, end_month):
        result.append((f"{year:04d}-{month:02d}", 1.0))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


class CanonicalWindowPolicyTests(unittest.TestCase):
    def test_collect_accepts_only_canonical_window_and_safe_sleep(self):
        validate_collection_policy(
            CANONICAL_START,
            "2026-07-13",
            BASE_SLEEP,
            canonical_end="2026-07-13",
        )

        with self.assertRaisesRegex(ValueError, "ทั้งช่วง"):
            validate_collection_policy(
                "2022-01-01",
                "2026-07-13",
                BASE_SLEEP,
                canonical_end="2026-07-13",
            )
        with self.assertRaisesRegex(ValueError, "--sleep"):
            validate_collection_policy(
                CANONICAL_START,
                "2026-07-13",
                BASE_SLEEP - 1,
                canonical_end="2026-07-13",
            )

    def test_make_jobs_rejects_short_window(self):
        validate_collection_window(
            CANONICAL_START,
            "2026-07-13",
            canonical_end="2026-07-13",
        )
        with self.assertRaisesRegex(ValueError, "ทั้งช่วง"):
            validate_collection_window(
                "2021-01-01",
                "2026-07-13",
                canonical_end="2026-07-13",
            )

    def test_ingest_since_is_a_hard_error_before_file_processing(self):
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "collector/ingest.py",
                "--dry-run",
                "--since",
                "2022-01",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--since ถูกปิดใช้งาน", result.stderr)

    def test_finish_returns_nonzero_for_partial_release(self):
        fake_build = Mock()
        fake_module = SimpleNamespace(build=fake_build)
        with (
            patch.object(collect, "save_catalog") as save_catalog,
            patch.dict(sys.modules, {"build_site_data": fake_module}),
        ):
            exit_code = collect._finish(
                {"series": {}},
                ok=1,
                no_data=1,
                failed=[("FP001", "TH", "network_error")],
            )

        self.assertEqual(exit_code, 1)
        save_catalog.assert_called_once()
        fake_build.assert_called_once()

    def test_finish_builds_when_only_confirmed_no_data_changed(self):
        fake_build = Mock()
        fake_module = SimpleNamespace(build=fake_build)
        with (
            patch.object(collect, "save_catalog") as save_catalog,
            patch.dict(sys.modules, {"build_site_data": fake_module}),
        ):
            exit_code = collect._finish(
                {"series": {}}, ok=0, no_data=1, failed=[]
            )

        self.assertEqual(exit_code, 0)
        save_catalog.assert_called_once()
        fake_build.assert_called_once()

    def test_empty_pytrends_result_requires_second_observation(self):
        with (
            patch.object(collect, "fetch_series", side_effect=[[], []]) as fetch,
            patch.object(collect.time, "sleep") as sleep,
            patch.object(collect.random, "uniform", return_value=0),
        ):
            points, observations = collect.fetch_with_empty_confirmation(
                object(), "คำ", "TH", "2004-01-01 2026-07-13", BASE_SLEEP
            )

        self.assertEqual(points, [])
        self.assertEqual(observations, 2)
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(BASE_SLEEP)

    def test_nonempty_pytrends_result_does_not_retry(self):
        with (
            patch.object(collect, "fetch_series", return_value=[("2026-06", 1.0)]) as fetch,
            patch.object(collect.time, "sleep") as sleep,
        ):
            points, observations = collect.fetch_with_empty_confirmation(
                object(), "คำ", "TH", "2004-01-01 2026-07-13", BASE_SLEEP
            )

        self.assertEqual(points, [("2026-06", 1.0)])
        self.assertEqual(observations, 1)
        fetch.assert_called_once()
        sleep.assert_not_called()

    def test_ingest_requires_complete_contiguous_canonical_window(self):
        today = date(2026, 7, 13)
        validate_canonical_coverage("TH", month_range("2004-01", "2026-06"), today)
        validate_canonical_coverage("TH-40", month_range("2014-01", "2026-06"), today)

        with self.assertRaisesRegex(ValueError, "canonical long horizon"):
            validate_canonical_coverage("TH", month_range("2021-01", "2026-06"), today)
        with self.assertRaisesRegex(ValueError, "canonical long horizon"):
            validate_canonical_coverage("TH", month_range("2004-01", "2026-05"), today)
        with self.assertRaisesRegex(ValueError, "เดือนขาด"):
            points = month_range("2004-01", "2026-06")
            del points[5]
            validate_canonical_coverage("TH", points, today)
        with self.assertRaisesRegex(ValueError, "0..100"):
            points = month_range("2004-01", "2026-06")
            points[-1] = (points[-1][0], float("nan"))
            validate_canonical_coverage("TH", points, today)

    def test_no_data_manifest_is_strict_and_preserves_existing_csv(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            series_dir = root / "series"
            series_dir.mkdir()
            path = root / "no_data_manifest__2026-07-13.json"
            payload = {
                "schema": NO_DATA_MANIFEST_SCHEMA,
                "generated_at": "2026-07-13T12:05:00+07:00",
                "jobs_source": "data/jobs.json",
                "entries": [{
                    "job_id": "j0001",
                    "keyword_id": "FP001",
                    "keyword": "ทดสอบ",
                    "geo_code": "TH-40",
                    "timeframe": "2004-01-01 2026-07-13",
                    "status": "NO_DATA",
                    "attempts": 2,
                    "no_data_attempts": 2,
                    "reason": "NO_VOLUME",
                    "observed_at": "2026-07-13T12:00:00+07:00",
                }],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            entries = parse_no_data_manifest(
                path,
                {"FP001": "ทดสอบ"},
                {"series": {}},
                series_dir=series_dir,
                today=date(2026, 7, 13),
            )
            self.assertEqual(entries[0][0], "FP001__TH-40")
            self.assertEqual(entries[0][1]["status"], "no_data")
            self.assertEqual(entries[0][1]["months"], 0)
            self.assertEqual(entries[0][1]["fetched_on"], "2026-07-13")

            (series_dir / "FP001__TH-40.csv").write_text("Month,Value\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "มี CSV เดิมอยู่"):
                parse_no_data_manifest(
                    path,
                    {"FP001": "ทดสอบ"},
                    {"series": {}},
                    series_dir=series_dir,
                    today=date(2026, 7, 13),
                )

            (series_dir / "FP001__TH-40.csv").unlink()
            payload["entries"][0]["no_data_attempts"] = 1
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "อย่างน้อย 2"):
                parse_no_data_manifest(
                    path,
                    {"FP001": "ทดสอบ"},
                    {"series": {}},
                    series_dir=series_dir,
                    today=date(2026, 7, 13),
                )

            payload["entries"][0]["no_data_attempts"] = 2
            payload["entries"][0]["timeframe"] = "2004-01-01 2026-07-14"
            payload["entries"][0]["observed_at"] = "2026-07-14T00:05:00+07:00"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "อยู่ในอนาคต"):
                parse_no_data_manifest(
                    path,
                    {"FP001": "ทดสอบ"},
                    {"series": {}},
                    series_dir=series_dir,
                    today=date(2026, 7, 13),
                )


if __name__ == "__main__":
    unittest.main()
