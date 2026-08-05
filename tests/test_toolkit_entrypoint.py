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


if __name__ == "__main__":
    unittest.main()
