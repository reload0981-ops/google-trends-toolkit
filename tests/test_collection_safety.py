import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from collector import collect, ingest
from collector.collect import BASE_SLEEP, CANONICAL_START, validate_collection_policy
from collector.ingest import (
    NO_DATA_MANIFEST_SCHEMA,
    load_keyword_map,
    parse_file,
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

    def test_custom_jobs_output_does_not_create_a_misleading_index(self):
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "jobs_smoke.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "collector/make_jobs.py",
                    "--ids",
                    "FP014",
                    "--geo",
                    "TH",
                    "--out",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(output.read_text(encoding="utf-8"))), 1)
            self.assertFalse((output.parent / "jobs_index.json").exists())

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

    def test_finish_returns_nonzero_for_partial_diagnostic_run(self):
        with patch.object(collect, "save_catalog") as save_catalog:
            exit_code = collect._finish(
                {"series": {}},
                ok=1,
                no_data=1,
                failed=[("FP001", "TH", "network_error")],
            )

        self.assertEqual(exit_code, 1)
        save_catalog.assert_called_once()

    def test_finish_saves_diagnostic_catalog_without_building_site_data(self):
        with patch.object(collect, "save_catalog") as save_catalog:
            exit_code = collect._finish(
                {"series": {}}, ok=0, no_data=1, failed=[]
            )

        self.assertEqual(exit_code, 0)
        save_catalog.assert_called_once()

    def test_pytrends_output_is_isolated_from_canonical_data(self):
        self.assertEqual(collect.DIAGNOSTIC_DIR, ROOT / "incoming" / "pytrends-diagnostic")
        self.assertEqual(collect.SERIES_DIR, collect.DIAGNOSTIC_DIR / "series")
        self.assertEqual(collect.CATALOG_PATH, collect.DIAGNOSTIC_DIR / "catalog.json")
        self.assertNotEqual(collect.SERIES_DIR, ROOT / "data" / "series")

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

    def test_ingest_recognizes_new_trends_time_series_export(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "time_series_TH_20040101-0700_20260715-1525.csv"
            rows = month_range("2004-01", "2026-07")
            text = '\n'.join(
                ['"Time","สมัครงาน"'] +
                [f'"{month}-01",{value:g}' for month, value in rows]
            ) + '\n'
            path.write_text(text, encoding="utf-8")

            keyword_map, keyword_ids = load_keyword_map()
            keyword_id, geo, points = parse_file(
                path, keyword_map, keyword_ids, today=date(2026, 7, 15)
            )

            self.assertEqual(keyword_id, "FP014")
            self.assertEqual(geo, "TH")
            self.assertEqual(points[0][0], "2004-01")
            self.assertEqual(points[-1][0], "2026-06")
            self.assertEqual(len(points), 270)

    def test_ingest_cross_checks_filename_keyword_and_place(self):
        keyword_map, keyword_ids = load_keyword_map()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            wrong_keyword = root / "FP014__TH.csv"
            wrong_keyword.write_text(
                'Month,"หางาน"\n2004-01,1\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "ไม่ตรงกับ ID"):
                parse_file(wrong_keyword, keyword_map, keyword_ids, today=date(2026, 7, 15))

            wrong_geo = root / "FP014__TH.csv"
            wrong_geo.write_text(
                'Month,"สมัครงาน: (ขอนแก่น)"\n2004-01,1\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "ไม่ตรงกับ GEO"):
                parse_file(wrong_geo, keyword_map, keyword_ids, today=date(2026, 7, 15))

            unknown = root / "FP014__TH.csv"
            unknown.write_text('Month,"คำที่ไม่มี"\n2004-01,1\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ไม่อยู่ใน keywords.csv"):
                parse_file(unknown, keyword_map, keyword_ids, today=date(2026, 7, 15))

            unknown_place = root / "FP014__TH.csv"
            unknown_place.write_text(
                'Month,"สมัครงาน: (เลย)"\n2004-01,1\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "ไม่รู้จักพื้นที่"):
                parse_file(unknown_place, keyword_map, keyword_ids, today=date(2026, 7, 15))

            unsupported_geo = root / "FP014__TH-99.csv"
            unsupported_geo.write_text("Month,Value\n2014-01,1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "GEO TH-99 ไม่รองรับ"):
                parse_file(unsupported_geo, keyword_map, keyword_ids, today=date(2026, 7, 15))

    def test_ingest_preserves_generic_month_value_format(self):
        keyword_map, keyword_ids = load_keyword_map()
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "FP014__TH.csv"
            path.write_text("Month,Value\n2004-01,1\n", encoding="utf-8")
            keyword_id, geo, points = parse_file(
                path, keyword_map, keyword_ids, today=date(2026, 7, 15)
            )
            self.assertEqual((keyword_id, geo), ("FP014", "TH"))
            self.assertEqual(points, [("2004-01", 1.0)])

    def test_ingest_bad_batch_is_nonzero_and_does_not_mutate_canonical_data(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            incoming = root / "incoming"
            series_dir = root / "data" / "series"
            incoming.mkdir()
            series_dir.mkdir(parents=True)
            keywords = root / "keywords.csv"
            keywords.write_text(
                "Keyword_ID,Keyword_TH\nFP001,ทดสอบ\nFP002,อีกคำ\n",
                encoding="utf-8",
            )
            catalog_path = root / "data" / "catalog.json"
            catalog_path.write_text('{"series": {}, "updated_at": null}', encoding="utf-8")
            sentinel = series_dir / "sentinel.csv"
            sentinel.write_bytes(b"unchanged")
            good = incoming / "FP001__TH.csv"
            good_rows = ["Month,Value"] + [f"{month},{value:g}" for month, value in month_range("2004-01", "2026-06")]
            good.write_text("\n".join(good_rows) + "\n", encoding="utf-8")
            bad = incoming / "FP002__TH.csv"
            bad.write_text("Month,ทดสอบ\n2004-01,1\n", encoding="utf-8")
            catalog_before = catalog_path.read_bytes()

            with (
                patch.object(ingest, "KEYWORDS_CSV", keywords),
                patch.object(ingest, "CATALOG_PATH", catalog_path),
                patch.object(ingest, "SERIES_DIR", series_dir),
            ):
                exit_code = ingest.main(["--dir", str(incoming), "--dry-run"])

            self.assertEqual(exit_code, 1)
            self.assertEqual(catalog_path.read_bytes(), catalog_before)
            self.assertEqual(sentinel.read_bytes(), b"unchanged")
            self.assertFalse((series_dir / "FP001__TH.csv").exists())
            self.assertTrue(good.exists())
            self.assertTrue(bad.exists())

            with (
                patch.object(ingest, "KEYWORDS_CSV", keywords),
                patch.object(ingest, "CATALOG_PATH", catalog_path),
                patch.object(ingest, "SERIES_DIR", series_dir),
            ):
                exit_code = ingest.main(["--dir", str(incoming)])

            self.assertEqual(exit_code, 1)
            self.assertEqual(catalog_path.read_bytes(), catalog_before)
            self.assertEqual(sentinel.read_bytes(), b"unchanged")
            self.assertFalse((series_dir / "FP001__TH.csv").exists())
            self.assertTrue(good.exists())
            self.assertFalse(bad.exists())

    def test_ingest_rejects_duplicate_destination_before_writing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            incoming = root / "incoming"
            series_dir = root / "data" / "series"
            incoming.mkdir()
            series_dir.mkdir(parents=True)
            keywords = root / "keywords.csv"
            keywords.write_text("Keyword_ID,Keyword_TH\nFP001,ทดสอบ\n", encoding="utf-8")
            catalog_path = root / "data" / "catalog.json"
            catalog_path.write_text('{"series": {}}', encoding="utf-8")
            rows = ["Month,Value"] + [f"{month},{value:g}" for month, value in month_range("2004-01", "2026-06")]
            payload = "\n".join(rows) + "\n"
            (incoming / "FP001__TH.csv").write_text(payload, encoding="utf-8")
            (incoming / "manual_FP001.csv").write_text(payload, encoding="utf-8")

            with (
                patch.object(ingest, "KEYWORDS_CSV", keywords),
                patch.object(ingest, "CATALOG_PATH", catalog_path),
                patch.object(ingest, "SERIES_DIR", series_dir),
            ):
                exit_code = ingest.main(["--dir", str(incoming), "--dry-run"])

            self.assertEqual(exit_code, 1)
            self.assertFalse((series_dir / "FP001__TH.csv").exists())

    def test_ingest_valid_batch_commits_then_builds(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            incoming = root / "incoming"
            series_dir = root / "data" / "series"
            incoming.mkdir()
            series_dir.mkdir(parents=True)
            keywords = root / "keywords.csv"
            keywords.write_text("Keyword_ID,Keyword_TH\nFP001,ทดสอบ\n", encoding="utf-8")
            catalog_path = root / "data" / "catalog.json"
            catalog_path.write_text('{"series": {}}', encoding="utf-8")
            source = incoming / "FP001__TH.csv"
            rows = ["Month,Value"] + [f"{month},{value:g}" for month, value in month_range("2004-01", "2026-06")]
            source.write_text("\n".join(rows) + "\n", encoding="utf-8")
            build = Mock(side_effect=lambda: self.assertTrue(source.exists()))

            with (
                patch.object(ingest, "KEYWORDS_CSV", keywords),
                patch.object(ingest, "CATALOG_PATH", catalog_path),
                patch.object(ingest, "SERIES_DIR", series_dir),
                patch.dict(sys.modules, {"build_site_data": SimpleNamespace(build=build)}),
            ):
                exit_code = ingest.main(["--dir", str(incoming)])

            self.assertEqual(exit_code, 0)
            self.assertTrue((series_dir / "FP001__TH.csv").exists())
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(catalog["series"]["FP001__TH"]["status"], "available")
            self.assertFalse(source.exists())
            self.assertTrue((incoming / "processed" / source.name).exists())
            build.assert_called_once_with()

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
