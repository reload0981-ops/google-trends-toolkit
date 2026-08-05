import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ToolkitEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.toolkit = (ROOT / "scripts" / "toolkit.ps1").read_text(encoding="utf-8")
        cls.launcher = (ROOT / "run-monthly-loop.cmd").read_text(encoding="utf-8")

    def test_monthly_run_has_one_human_checkpoint_between_prepare_and_finish(self):
        action = self.toolkit.index('"monthly-run" {')
        prepare = self.toolkit.index("Invoke-MonthlyPrepare", action)
        checkpoint = self.toolkit.index("Read-Host", prepare)
        finish = self.toolkit.index("Invoke-MonthlyFinish", checkpoint)
        self.assertLess(prepare, checkpoint)
        self.assertLess(checkpoint, finish)
        self.assertIn('$confirmation -cne "FINISH"', self.toolkit)

    def test_monthly_finish_names_the_tableau_ready_output(self):
        self.assertIn(
            "Tableau source: derived\\sa_pipeline_v3\\series.csv",
            self.toolkit,
        )

    def test_clickable_launcher_runs_the_guarded_monthly_loop(self):
        self.assertIn("scripts\\toolkit.ps1", self.launcher)
        self.assertIn("monthly-run", self.launcher)
        self.assertNotIn("git push", self.launcher)

    def test_operator_launcher_refreshes_the_bundled_queue_before_copying(self):
        # Writing only to the Desktop left the Controller dropdown pointing at a
        # stale bundled queue that silently replaces a running round when picked.
        operator = (ROOT / "เริ่มเก็บข้อมูลเดือนนี้.cmd").read_text(encoding="utf-8")
        self.assertIn("-DesktopCopy", operator)
        self.assertNotIn("--out", operator)
        self.assertNotIn("-out ", operator)

    def test_adding_a_keyword_has_a_clickable_entry_point(self):
        launcher = (ROOT / "เพิ่มคำใหม่.cmd").read_text(encoding="utf-8")
        self.assertIn("add-keyword", launcher)
        self.assertIn(r"scripts\toolkit.ps1", launcher)
        # Thai prompts live in Python because Windows PowerShell 5.1 reads a
        # script without a BOM as ANSI.
        self.assertIn('"--interactive", "--id-file"', self.toolkit)
        self.assertIn('Read-Host "Type FINISH', self.toolkit)

    def test_a_deliberate_stop_is_not_dressed_up_as_a_crash(self):
        # A guard that prints a PowerShell stack trace and tells the maintainer
        # to report it teaches people to ignore guards.
        self.assertIn("if ($LASTEXITCODE -eq 9) { exit 9 }", self.toolkit)
        self.assertIn("-AllowUnfinishedRound", self.toolkit)
        for name in ("เริ่มเก็บข้อมูลเดือนนี้.cmd", "เพิ่มคำใหม่.cmd"):
            launcher = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('if "%toolkit_exit%"=="9" goto :done', launcher)

    def test_desktop_copy_is_made_from_the_canonical_queue(self):
        self.assertIn('GetFolderPath("Desktop")', self.toolkit)
        self.assertIn("--desktop-kind", self.toolkit)
        self.assertIn(
            "-DesktopCopy cannot be combined with -out",
            self.toolkit,
        )


if __name__ == "__main__":
    unittest.main()
