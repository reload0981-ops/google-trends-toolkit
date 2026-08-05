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

    def test_a_queue_is_never_built_over_a_round_still_collecting(self):
        # The Controller holds one queue, so a fresh import mid-round throws
        # away everything collected so far.
        self.assertIn("Assert-NoRoundInFlight", self.toolkit)
        self.assertIn("still in flight", self.toolkit)
        self.assertIn("-AllowUnfinishedRound", self.toolkit)
        # It must fire before add-keyword writes the row, or a refused run
        # leaves a keyword with no data and breaks the release gate.
        guard = self.toolkit.index("Assert-NoRoundInFlight -Allow:$AllowUnfinishedRound", self.toolkit.index('"add-keyword" {'))
        add_call = self.toolkit.index("add_keyword.py", guard)
        self.assertLess(guard, add_call)

    def test_desktop_copy_is_made_from_the_canonical_queue(self):
        self.assertIn('GetFolderPath("Desktop")', self.toolkit)
        self.assertIn("--desktop-kind", self.toolkit)
        self.assertIn(
            "-DesktopCopy cannot be combined with -out",
            self.toolkit,
        )


if __name__ == "__main__":
    unittest.main()
