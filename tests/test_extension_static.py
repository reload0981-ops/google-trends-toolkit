import base64
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"


class ExtensionReleaseSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        cls.content = (EXTENSION / "content.js").read_text(encoding="utf-8")
        cls.controller = (EXTENSION / "controller.js").read_text(encoding="utf-8")

    def test_stale_filename_queue_is_replaced_not_appended(self):
        self.assertIn("pendingFilenames = [msg.filename]", self.background)
        self.assertNotIn("pendingFilenames.push(msg.filename)", self.background)
        self.assertIn("INVALID_TOOLKIT_FILENAME", self.background)
        self.assertIn("if (!prepared || !prepared.ok)", self.content)

    def test_thai_detectors_are_not_mojibake(self):
        self.assertNotIn("เธขเธทเธ", self.content)
        self.assertNotIn("เน€เธเธดเธ”", self.content)
        self.assertIn("THAI_CAPTCHA_PHRASES", self.content)
        self.assertIn("THAI_SOFT_ERROR", self.content)
        self.assertIn("THAI_NEW_EXPLORE_AUTH", self.content)
        self.assertIn('return "AUTH_REQUIRED"', self.content)
        self.assertIn('["CAPTCHA", "AUTH_REQUIRED"]', self.controller)

    def test_notification_has_valid_embedded_48px_png(self):
        self.assertNotIn('chrome.runtime.getURL("icons/48.png")', self.controller)
        match = re.search(r'NOTIFICATION_ICON_DATA_URL = "data:image/png;base64,([^"]+)"', self.controller)
        self.assertIsNotNone(match)
        payload = base64.b64decode(match.group(1), validate=True)
        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(payload[16:20], "big"), 48)
        self.assertEqual(int.from_bytes(payload[20:24], "big"), 48)

    def test_no_data_manifest_tracks_observations_not_total_attempts(self):
        self.assertIn("job.no_data_attempts = (job.no_data_attempts || 0) + 1", self.controller)
        self.assertIn("no_data_attempts: job.no_data_attempts", self.controller)
        self.assertIn("job.no_data_attempts = 0", self.controller)
        self.assertIn("state.no_data_manifest_exported_at = null", self.controller)
        self.assertIn("await exportNoDataManifest()", self.controller)

    def test_manifest_version_matches_release_behavior(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.7.1")
        self.assertIn("downloads", manifest["permissions"])

    def test_new_trends_ui_is_the_primary_monthly_export_path(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("https://trends.google.co.th/*", manifest["host_permissions"])
        matches = manifest["content_scripts"][0]["matches"]
        self.assertIn("https://trends.google.co.th/explore*", matches)
        self.assertIn("https://trends.google.co.th/explore?", self.controller)
        self.assertIn('"date=all"', self.controller)
        self.assertIn('button[aria-label="ดาวน์โหลด CSV ความสนใจในช่วงเวลาที่ผ่านมา"]', self.content)
        self.assertIn('svg[role~="graphics-document"]', self.content)
        self.assertIn("looksLikeExpectedTimeseriesDownload", self.background)

    def test_timeseries_button_lookup_is_chart_scoped_and_fail_closed(self):
        self.assertIn("function findCSVButton(chartEl)", self.content)
        self.assertIn("scope.querySelectorAll(sel)", self.content)
        self.assertIn("const seen = new Set()", self.content)
        self.assertIn("if (candidates.length === 1)", self.content)
        self.assertIn("if (candidates.length > 1) return null", self.content)
        self.assertEqual(self.content.count("findCSVButton(chart)"), 2)
        self.assertNotIn(
            "Array.from(document.querySelectorAll('button,[role=\"button\"],a'))",
            self.content,
        )
        self.assertIn('state: "maximized"', self.controller)

    def test_background_renames_only_expected_timeseries_geo(self):
        self.assertIn(
            "const TOOLKIT_FILENAME = /^[A-Z]{2}\\d{3}__(TH(?:-\\d{2})?)\\.csv$/",
            self.background,
        )
        self.assertIn("function looksLikeExpectedTimeseriesDownload", self.background)
        self.assertIn("const next = pendingFilenames[0]", self.background)
        self.assertLess(
            self.background.index("looksLikeExpectedTimeseriesDownload(item, next)"),
            self.background.index("pendingFilenames.shift()"),
        )
        self.assertNotIn('url.includes("trends.google.com")', self.background)
        self.assertNotIn('fn.includes("multitimeline")', self.background)
        self.assertNotIn('fn.includes("geomap")', self.background)
        self.assertIn("startedAfter: jobStartIso", self.controller)
        self.assertIn('reason: "NO_DOWNLOAD_FOUND"', self.controller)

    def test_python_download_bridge_is_fail_closed(self):
        self.assertIn("BROWSER_RUNNER_MODE_KEY", self.controller)
        self.assertIn("BROWSER_RUNNER_DOWNLOAD_ACK_KEY", self.controller)
        self.assertIn("ack.filename === job.filename", self.controller)
        self.assertIn('ack.status === "valid"', self.controller)
        self.assertIn('ack.status === "invalid"', self.controller)

    def test_controller_can_import_a_fresh_queue_without_extension_reload(self):
        html = (EXTENSION / "controller.html").read_text(encoding="utf-8")
        self.assertIn('id="btn-import-jobs"', html)
        self.assertIn('id="jobs-file-input"', html)
        self.assertIn("function validateJobs(jobs)", self.controller)
        self.assertIn("await importJobsFile(file)", self.controller)
        self.assertIn("timeframe must be", self.controller)
        self.assertIn("await resetStateFromJobs(state.jobs", self.controller)
        self.assertIn("const canReplaceQueue = isIdle && !loopRunning", self.controller)
        self.assertIn("Queue state changed while choosing the file", self.controller)
        self.assertIn('loopRunning || (state && state.status !== "idle")', self.controller)


if __name__ == "__main__":
    unittest.main()
