import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from collector import round_guard


class RoundGuardTests(unittest.TestCase):
    """A queue must never be built over a round that is still collecting."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "incoming").mkdir()

    def write_download(self, name="FP014__TH.csv"):
        (self.root / "incoming" / name).write_text("Month,Value\n", encoding="utf-8")

    def run_guard(self, **kwargs):
        """Return (exit_code, printed_text); exit_code is None when it allowed."""

        buffer = io.StringIO()
        code = None
        with contextlib.redirect_stdout(buffer):
            try:
                round_guard.assert_no_round_in_flight(self.root, **kwargs)
            except SystemExit as stop:
                code = stop.code
        return code, buffer.getvalue()

    def test_no_downloads_waiting_means_no_round_in_flight(self):
        code, _ = self.run_guard()

        self.assertIsNone(code)

    def test_waiting_downloads_stop_the_run_with_a_distinct_code(self):
        # The Controller holds one queue, so building another one now and
        # importing it would throw away everything collected so far.
        self.write_download()

        code, _ = self.run_guard()

        # A code of its own so callers can tell a deliberate stop from a crash
        # and skip the "send this to the maintainer" message.
        self.assertEqual(code, round_guard.ROUND_IN_FLIGHT_EXIT)
        self.assertNotIn(round_guard.ROUND_IN_FLIGHT_EXIT, (0, 1))

    def test_the_override_lets_a_deliberate_restart_through(self):
        self.write_download()

        code, _ = self.run_guard(allow=True)

        self.assertIsNone(code)

    def test_already_ingested_files_do_not_look_like_a_live_round(self):
        # ingest.py moves what it consumed into incoming/processed, so a folder
        # holding only that is a finished round, not a live one.
        processed = self.root / "incoming" / "processed"
        processed.mkdir()
        (processed / "FP014__TH.csv").write_text("Month,Value\n", encoding="utf-8")

        code, _ = self.run_guard()

        self.assertIsNone(code)

    def test_a_missing_incoming_folder_is_not_a_live_round(self):
        shutil.rmtree(self.root / "incoming")

        code, _ = self.run_guard()

        self.assertIsNone(code)

    def test_the_message_says_what_to_do_and_that_nothing_broke(self):
        self.write_download()

        _, printed = self.run_guard()

        self.assertIn("FINISH", printed)
        self.assertIn("ไม่ใช่ข้อผิดพลาด", printed)
        self.assertNotIn("Traceback", printed)


if __name__ == "__main__":
    unittest.main()
