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
        self.assertEqual(manifest["version"], "0.4.0")
        self.assertIn("downloads", manifest["permissions"])


if __name__ == "__main__":
    unittest.main()
