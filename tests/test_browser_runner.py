import tempfile
import unittest
from pathlib import Path

from collector.browser_runner import (
    RunnerError, is_timeseries_download_filename, summarize_state,
    validate_captured_download, validate_jobs,
)


def job(job_id="j0001", keyword_id="FP014", geo="TH", end="2026-07-15"):
    return {
        "job_id": job_id,
        "keyword_id": keyword_id,
        "keyword": "ช่างไฟ",
        "geo_code": geo,
        "timeframe": f"2004-01-01 {end}",
        "filename": f"{keyword_id}__{geo}.csv",
    }


class BrowserRunnerSafetyTests(unittest.TestCase):
    def test_valid_queue_plan_is_countable(self):
        jobs = [job(), job("j0002", "FP019", "TH-30")]
        plan = validate_jobs(jobs, canonical_end="2026-07-15")
        self.assertEqual(plan["total"], 2)
        self.assertEqual(plan["keywords"], 2)
        self.assertEqual(plan["geos"], 2)

    def test_short_or_stale_window_is_rejected_before_browser_launch(self):
        with self.assertRaises(RunnerError):
            validate_jobs([job(end="2026-04-30")], canonical_end="2026-07-15")

    def test_filename_must_match_keyword_and_geo(self):
        broken = job()
        broken["filename"] = "FP014__TH-30.csv"
        with self.assertRaises(RunnerError):
            validate_jobs([broken], canonical_end="2026-07-15")

    def test_duplicate_output_is_rejected(self):
        with self.assertRaises(RunnerError):
            validate_jobs(
                [job("j0001"), job("j0002")], canonical_end="2026-07-15"
            )

    def test_state_summary_marks_captcha_and_completion(self):
        paused = {
            "status": "paused",
            "captcha_tab_id": 123,
            "human_action_reason": "AUTH_REQUIRED",
            "cursor": 0,
            "jobs": [{**job(), "status": "RUNNING"}],
        }
        paused_summary = summarize_state(paused)
        self.assertTrue(paused_summary["human_action_required"])
        self.assertEqual(paused_summary["human_action_reason"], "AUTH_REQUIRED")

        complete = {
            "status": "idle",
            "cursor": 2,
            "jobs": [
                {**job(), "status": "DONE"},
                {**job("j0002", "FP019", "TH-30"), "status": "NO_DATA"},
            ],
        }
        summary = summarize_state(complete)
        self.assertTrue(summary["complete"])
        self.assertTrue(summary["successful"])
        self.assertEqual(summary["counts"]["no_data"], 1)

    def test_yearly_export_is_rejected_by_ingest_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FP014__TH.csv"
            path.write_text(
                "Category: All categories\n\nYear,สมัครงาน: (Thailand)\n2004,97\n2026,37\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "หัวตาราง"):
                validate_captured_download(path)

    def test_new_and_classic_timeseries_download_names_are_recognized(self):
        self.assertTrue(is_timeseries_download_filename("multiTimeline.csv"))
        self.assertTrue(is_timeseries_download_filename(
            "time_series_TH_20040101-0700_20260715-1525.csv"
        ))
        self.assertTrue(is_timeseries_download_filename(
            "time_series_TH-40_20040101-0700_20260715-1525.csv"
        ))
        self.assertFalse(is_timeseries_download_filename("geoMap.csv"))
        self.assertFalse(is_timeseries_download_filename("time_series.csv.exe"))


if __name__ == "__main__":
    unittest.main()
