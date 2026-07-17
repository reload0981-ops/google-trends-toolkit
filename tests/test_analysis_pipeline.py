import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ANALYSIS_IMPORT_ERROR = None
try:
    import pandas as pd
    from analysis.build import audit_outputs, build, main
    from analysis.core import PipelineError
    from analysis.pipeline import (
        build_pre_sa_for_case,
        centered_ma3,
        load_cases,
        rebase_max100,
    )
    from analysis.x13 import (
        X13SeriesError,
        _parse_saved_series,
        parse_diagnostics,
        seasonally_adjust,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"numpy", "pandas", "scipy", "statsmodels"}:
        raise
    ANALYSIS_IMPORT_ERROR = str(exc)


ROOT = Path(__file__).resolve().parents[1]
ISAN5 = ("TH-30", "TH-31", "TH-34", "TH-40", "TH-41")


def series(values, start="2024-01-01"):
    return pd.Series(
        values,
        index=pd.date_range(start, periods=len(values), freq="MS"),
        dtype=float,
    )


def result_series(result):
    if isinstance(result, pd.Series):
        return result
    if isinstance(result, dict):
        return result["series"]
    return result.series


def case_field(case, name):
    if isinstance(case, dict):
        return case.get(name, case.get(name.title()))
    return getattr(case, name)


@unittest.skipIf(ANALYSIS_IMPORT_ERROR, "optional analytical dependencies are not installed")
class RebaseTests(unittest.TestCase):
    def test_all_zero_series_stays_zero_and_is_no_signal(self):
        rebased, audit = rebase_max100(series([0, 0, 0]))

        pd.testing.assert_series_equal(
            rebased,
            series([0, 0, 0]),
            check_dtype=False,
            check_names=False,
        )
        self.assertIsInstance(audit, dict)
        self.assertEqual(audit["status"], "NO_SIGNAL")

    def test_rebase_preserves_real_zero_without_epsilon(self):
        rebased, audit = rebase_max100(series([0, 2, 4]))

        pd.testing.assert_series_equal(
            rebased,
            series([0, 50, 100]),
            check_dtype=False,
            check_names=False,
        )
        self.assertIsInstance(audit, dict)
        self.assertEqual(rebased.iloc[0], 0)


@unittest.skipIf(ANALYSIS_IMPORT_ERROR, "optional analytical dependencies are not installed")
class MovingAverageTests(unittest.TestCase):
    def test_centered_ma3_floors_negative_values_before_smoothing(self):
        actual = centered_ma3(series([-9, 0, 9]))

        pd.testing.assert_series_equal(
            actual,
            series([0, 3, 4.5]),
            check_dtype=False,
            check_names=False,
        )


@unittest.skipIf(ANALYSIS_IMPORT_ERROR, "optional analytical dependencies are not installed")
class PreSeasonalAggregationTests(unittest.TestCase):
    def test_t1_rebases_each_province_before_mean_and_region_rebase(self):
        case = {"case_id": "K1", "tier": "T1", "members": ["K1"]}
        raw_map = {
            ("K1", "TH-30"): series([1, 2]),
            ("K1", "TH-31"): series([100, 100]),
            ("K1", "TH-34"): series([10, 20]),
            ("K1", "TH-40"): series([0, 10]),
            ("K1", "TH-41"): series([5, 5]),
        }

        result = build_pre_sa_for_case(case, raw_map, ISAN5)

        pd.testing.assert_series_equal(
            result_series(result),
            series([60, 100]),
            check_dtype=False,
            check_names=False,
        )

    def test_t2_rebases_members_then_family_per_province_then_region(self):
        case = {"case_id": "F1", "tier": "T2", "members": ["A", "B"]}
        pairs = {
            "TH-30": ([1, 0, 0], [0, 2, 1]),
            "TH-31": ([0, 2, 0], [1, 0, 1]),
            "TH-34": ([0, 1, 2], [2, 0, 0]),
            "TH-40": ([0, 2, 1], [1, 0, 0]),
            "TH-41": ([1, 0, 1], [2, 0, 1]),
        }
        raw_map = {
            (member, geo): series(values)
            for geo, member_values in pairs.items()
            for member, values in zip(case["members"], member_values)
        }

        result = build_pre_sa_for_case(case, raw_map, ISAN5)

        # Skipping the family-level province rebase would yield
        # [100, 58.333..., 75], so this fixture protects the exact T2 order.
        pd.testing.assert_series_equal(
            result_series(result),
            series([100, 70, 75]),
            check_dtype=False,
            check_names=False,
            rtol=1e-10,
            atol=1e-10,
        )

    def test_missing_required_province_raises(self):
        case = {"case_id": "K1", "tier": "T1", "members": ["K1"]}
        raw_map = {
            ("K1", geo): series([1, 2])
            for geo in ISAN5[:-1]
        }

        with self.assertRaises(ValueError):
            build_pre_sa_for_case(case, raw_map, ISAN5)


@unittest.skipIf(ANALYSIS_IMPORT_ERROR, "optional analytical dependencies are not installed")
class KeywordMetadataTests(unittest.TestCase):
    def test_current_keywords_define_22_t1_and_8_t2_cases(self):
        cases = load_cases(ROOT / "keywords.csv")
        tier_counts = {
            tier: sum(case_field(case, "tier") == tier for case in cases)
            for tier in ("T1", "T2")
        }

        self.assertEqual(tier_counts, {"T1": 22, "T2": 8})
        self.assertEqual(len(cases), 30)

    def test_duplicate_keyword_id_fails_closed(self):
        source = (ROOT / "keywords.csv").read_text(encoding="utf-8-sig")
        duplicate_row = source.splitlines()[1]
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "keywords.csv"
            path.write_text(source.rstrip() + "\n" + duplicate_row + "\n", encoding="utf-8")

            with self.assertRaisesRegex(PipelineError, "duplicate Keyword_ID"):
                load_cases(path)


@unittest.skipIf(ANALYSIS_IMPORT_ERROR, "optional analytical dependencies are not installed")
class SeasonalAdjustmentTests(unittest.TestCase):
    def test_build_rejects_nonpositive_timeout_before_running_x13(self):
        with self.assertRaisesRegex(PipelineError, "at least 1 second"):
            build(root=ROOT, timeout=0)

    def test_x13_failure_uses_logged_stl_fallback(self):
        values = series([10 + (month % 12) for month in range(36)])

        with patch("analysis.x13._x13_adjust", side_effect=X13SeriesError("model failed")):
            result = seasonally_adjust(values, Path("unused-x13"), fallback="stl")

        self.assertEqual(result.method, "STL_FALLBACK")
        self.assertEqual(result.status, "FALLBACK")
        self.assertEqual(result.reason, "X13SeriesError: model failed")
        self.assertEqual(len(result.series), len(values))

    def test_infrastructure_error_is_not_silently_changed_to_stl(self):
        values = series([10 + (month % 12) for month in range(36)])

        with patch("analysis.x13._x13_adjust", side_effect=RuntimeError("parser regression")):
            with self.assertRaisesRegex(RuntimeError, "parser regression"):
                seasonally_adjust(values, Path("unused-x13"), fallback="stl")

    def test_all_zero_skips_x13_and_stays_zero(self):
        values = series([0] * 36)

        with patch("analysis.x13._x13_adjust") as run_x13:
            result = seasonally_adjust(values, Path("unused-x13"))

        run_x13.assert_not_called()
        self.assertEqual(result.method, "NO_SIGNAL")
        self.assertTrue((result.series == 0).all())

    def test_parse_real_x13_quality_fields(self):
        output = """
        M1 = 0.125
        M 7 = 0.432
        M11 = 1.250
        Q (without M2) = 0.52
        *** Q (without M2) = 0.52 CONDITIONALLY ACCEPTED.
        F-test for stable seasonality from Table B 1. : 2.771 1.44%
        Kruskal-Wallis Chi Squared test
            for stable seasonality from Table D 8. : 137.865 0.00%
        F-test for moving seasonality from Table D 8. : 1.500 12.50%
        """

        result = parse_diagnostics(output)

        self.assertEqual(result["M1"], 0.125)
        self.assertEqual(result["M7"], 0.432)
        self.assertEqual(result["M11"], 1.25)
        self.assertEqual(result["Q_Without_M2"], 0.52)
        self.assertEqual(result["Accept_Status"], "CONDITIONALLY ACCEPTED")
        self.assertEqual(result["F_Stable_B1_PValue"], 0.0144)
        self.assertEqual(result["F_Moving_D8_PValue"], 0.125)
        self.assertEqual(result["Kruskal_Wallis_Chi"], 137.865)

    def test_saved_x13_series_rejects_shifted_months(self):
        index = pd.date_range("2024-01-01", periods=2, freq="MS")
        shifted = "date\tvalue\n------\t-----\n202402\t1.0\n202403\t2.0\n"

        with self.assertRaisesRegex(PipelineError, "monthly support"):
            _parse_saved_series(shifted, index)


@unittest.skipIf(ANALYSIS_IMPORT_ERROR, "optional analytical dependencies are not installed")
class DerivedOutputAuditTests(unittest.TestCase):
    def test_tracked_derived_outputs_pass_full_audit(self):
        result = audit_outputs(ROOT)

        self.assertEqual(result["status"], "PASS", result.get("errors"))


@unittest.skipIf(ANALYSIS_IMPORT_ERROR, "optional analytical dependencies are not installed")
class AnalysisCliTests(unittest.TestCase):
    def test_json_mode_suppresses_progress_output(self):
        stdout = io.StringIO()
        with patch("analysis.build.build", return_value={"status": "BUILT"}) as run_build:
            with redirect_stdout(stdout):
                exit_code = main(["--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "BUILT"})
        self.assertTrue(run_build.call_args.args[-1])


if __name__ == "__main__":
    unittest.main()
