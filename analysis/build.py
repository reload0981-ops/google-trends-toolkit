"""CLI and artifact writer for the portable analytical pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import scipy
import statsmodels

from .core import Case, PipelineError, SCOPES, load_cases, load_scope_raw
from .pipeline import OUTPUT_FILES, ROOT, SCHEMA_VERSION, SERIES_COLUMNS, _build_rows, _write_csv
from .x13 import (
    DIAGNOSTIC_FIELDS,
    EXPECTED_SHA256,
    EXPECTED_VERSION,
    discover_x13,
    sha256,
    verify_x13,
)


IMPLEMENTATION_FILES = (
    "analysis/core.py",
    "analysis/pipeline.py",
    "analysis/x13.py",
    "analysis/build.py",
    "requirements-analysis.txt",
)
METHOD_COLUMNS = (
    "Case_ID", "Scope", "Tier", "Case_Type", "Members", "Status", "Method",
    "Reason", "Signal_Contributors_N", "Required_Contributors_N",
    "Post_SA_Status", "Post_SA_Pre_Max",
)
AUDIT_COLUMNS = (
    "Case_ID", "Scope", "Tier", "Stage", "Member_ID", "Geo", "Status",
    "Pre_Max", "Contributors_N", "Required_N",
)
DIAGNOSTIC_COLUMNS = ("Case_ID", "Scope", "Method", *DIAGNOSTIC_FIELDS, "Accept_Status")
METHOD_CONTRACT = {
    "pre_sa": "T1 A(member-geo)->mean-geos->C; T2 A(member-geo)->mean-members->B->mean-geos->C",
    "seasonal_adjustment": "X-13ARIMA-SEATS additive, log=false, outlier=false; zeros become 0.001 only at the SA call",
    "post_sa": "floor at zero -> rebase max100 -> centered MA3 (3,min_periods=1)",
    "all_zero": "preserve as zero and mark NO_SIGNAL",
    "missing_support": "fail; never pad missing months or geographies",
    "fallback": "stl",
}


def source_digest(root: Path, cases: Sequence[Case]) -> tuple[str, list[Path]]:
    files = [root / "keywords.csv"]
    members = sorted({member for case in cases for member in case.members})
    for member in members:
        for geo in ("TH", "TH-30", "TH-31", "TH-34", "TH-40", "TH-41"):
            files.append(root / "data" / "series" / f"{member}__{geo}.csv")
    missing = [path for path in files if not path.is_file()]
    if missing:
        sample = ", ".join(str(path.relative_to(root)) for path in missing[:5])
        raise PipelineError(f"missing canonical source file(s): {sample}")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), files


def implementation_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in IMPLEMENTATION_FILES:
        path = root / relative
        if not path.is_file():
            raise PipelineError(f"missing analytical implementation file: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def prepare_output(
    root: Path,
    destination: Path,
    executable: Path,
    x13_info: Mapping[str, str],
    timeout: int,
    fallback: str,
    quiet: bool,
) -> dict[str, Any]:
    cases = load_cases(root / "keywords.csv")
    if not cases:
        raise PipelineError("keywords.csv does not define any analytical cases")
    digest, source_files = source_digest(root, cases)
    destination.mkdir(parents=True, exist_ok=True)
    series_rows, method_rows, audit_rows, diagnostic_rows, metadata = _build_rows(
        root, cases, executable, timeout, fallback, quiet,
    )

    row_counts = {
        "series.csv": _write_csv(destination / "series.csv", SERIES_COLUMNS, series_rows),
        "method_log.csv": _write_csv(
            destination / "method_log.csv",
            METHOD_COLUMNS,
            method_rows,
        ),
        "rebase_audit.csv": _write_csv(
            destination / "rebase_audit.csv",
            AUDIT_COLUMNS,
            audit_rows,
        ),
        "x13_diagnostics.csv": _write_csv(
            destination / "x13_diagnostics.csv",
            DIAGNOSTIC_COLUMNS,
            diagnostic_rows,
        ),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "method": {**METHOD_CONTRACT, "fallback": fallback},
        "windows": metadata["scopes"],
        "counts": metadata["counts"],
        "source": {
            "digest_sha256": digest,
            "keywords_sha256": sha256(root / "keywords.csv"),
            "files": len(source_files),
        },
        "implementation": {
            "digest_sha256": implementation_digest(root),
            "files": list(IMPLEMENTATION_FILES),
        },
        "runtime": {
            "x13_version": x13_info["version"],
            "x13_sha256": x13_info["sha256"],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "statsmodels": statsmodels.__version__,
        },
        "files": {
            name: {"sha256": sha256(destination / name), "rows": row_counts[name]}
            for name in row_counts
        },
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    return manifest


def build(
    root: Path = ROOT,
    output_dir: Path | None = None,
    x13_path: str | None = None,
    timeout: int = 60,
    fallback: str = "stl",
    check: bool = False,
    allow_unverified_x13: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    root = Path(root).resolve()
    output_dir = Path(output_dir or root / "derived" / "sa_pipeline_v3").resolve()
    if timeout < 1:
        raise PipelineError("--timeout must be at least 1 second")
    if fallback not in {"stl", "error"}:
        raise PipelineError("fallback must be 'stl' or 'error'")
    executable = discover_x13(root, x13_path)
    x13_info = verify_x13(executable, allow_unverified_x13)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gt_analysis_", dir=output_dir.parent) as temp_name:
        staging = Path(temp_name) / "sa_pipeline_v3"
        manifest = prepare_output(root, staging, executable, x13_info, timeout, fallback, quiet)
        if check:
            differences: list[str] = []
            for name in OUTPUT_FILES:
                expected, actual = output_dir / name, staging / name
                if not expected.is_file():
                    differences.append(f"missing {name}")
                elif expected.read_bytes() != actual.read_bytes():
                    differences.append(f"stale {name}")
            if output_dir.exists():
                unexpected = sorted(
                    path.name for path in output_dir.iterdir()
                    if path.is_file() and path.name not in OUTPUT_FILES
                )
                differences.extend(f"unexpected {name}" for name in unexpected)
            if differences:
                raise PipelineError("derived output check failed: " + "; ".join(differences))
            return {"status": "PASS", "mode": "check", **manifest["counts"]}

        output_dir.mkdir(parents=True, exist_ok=True)
        unexpected = sorted(
            path.name for path in output_dir.iterdir()
            if path.is_file() and path.name not in OUTPUT_FILES
        )
        if unexpected:
            raise PipelineError("unexpected derived output file(s): " + ", ".join(unexpected))
        for name in OUTPUT_FILES:
            os.replace(staging / name, output_dir / name)
        return {"status": "BUILT", "mode": "build", **manifest["counts"]}


def audit_outputs(root: Path = ROOT, output_dir: Path | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    output_dir = Path(output_dir or root / "derived" / "sa_pipeline_v3").resolve()
    errors: list[str] = []
    for name in OUTPUT_FILES:
        if not (output_dir / name).is_file():
            errors.append(f"missing {name}")
    if output_dir.exists():
        errors.extend(
            f"unexpected {path.name}" for path in output_dir.iterdir()
            if path.is_file() and path.name not in OUTPUT_FILES
        )
    if errors:
        return {"status": "FAIL", "errors": errors}

    try:
        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "errors": [f"invalid manifest.json: {exc}"]}
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version {manifest.get('schema_version')!r}")
    if manifest.get("method") != METHOD_CONTRACT:
        errors.append("manifest method contract differs from canonical policy")
    runtime = manifest.get("runtime", {})
    if runtime.get("x13_sha256") != EXPECTED_SHA256:
        errors.append("manifest does not use the canonical X-13 binary hash")
    if runtime.get("x13_version") != EXPECTED_VERSION:
        errors.append("manifest does not use the canonical X-13 version")
    expected_runtime = {
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "statsmodels": statsmodels.__version__,
    }
    for package, version in expected_runtime.items():
        if runtime.get(package) != version:
            errors.append(f"manifest {package} version differs from the pinned runtime")
    cases = load_cases(root / "keywords.csv")
    digest, source_files = source_digest(root, cases)
    if manifest.get("source", {}).get("digest_sha256") != digest:
        errors.append("source digest is stale")
    if manifest.get("source", {}).get("keywords_sha256") != sha256(root / "keywords.csv"):
        errors.append("manifest keywords hash is stale")
    if manifest.get("source", {}).get("files") != len(source_files):
        errors.append("manifest source file count is incorrect")
    code_digest = implementation_digest(root)
    if manifest.get("implementation", {}).get("digest_sha256") != code_digest:
        errors.append("analytical implementation digest is stale")
    if manifest.get("implementation", {}).get("files") != list(IMPLEMENTATION_FILES):
        errors.append("manifest implementation file list is incorrect")
    if set(manifest.get("files", {})) != set(OUTPUT_FILES[:-1]):
        errors.append("manifest output file list is incorrect")
    for name in OUTPUT_FILES[:-1]:
        if manifest.get("files", {}).get(name, {}).get("sha256") != sha256(output_dir / name):
            errors.append(f"hash mismatch {name}")

    try:
        series = pd.read_csv(output_dir / "series.csv", encoding="utf-8")
        methods = pd.read_csv(output_dir / "method_log.csv", encoding="utf-8")
        rebases = pd.read_csv(output_dir / "rebase_audit.csv", encoding="utf-8")
        diagnostics = pd.read_csv(output_dir / "x13_diagnostics.csv", encoding="utf-8")
    except Exception as exc:
        errors.append(f"cannot parse derived CSV: {exc}")
    else:
        frames = {
            "series.csv": (series, SERIES_COLUMNS),
            "method_log.csv": (methods, METHOD_COLUMNS),
            "rebase_audit.csv": (rebases, AUDIT_COLUMNS),
            "x13_diagnostics.csv": (diagnostics, DIAGNOSTIC_COLUMNS),
        }
        for name, (frame, columns) in frames.items():
            if tuple(frame.columns) != columns:
                errors.append(f"{name} columns do not match schema")
            if manifest.get("files", {}).get(name, {}).get("rows") != len(frame):
                errors.append(f"{name} row count does not match manifest")

        expected_pairs = {(scope, case.case_id) for scope in SCOPES for case in cases}
        actual_pairs = set(zip(methods.get("Scope", ()), methods.get("Case_ID", ())))
        if actual_pairs != expected_pairs or len(methods) != len(expected_pairs):
            errors.append("method_log.csv does not contain every case x scope exactly once")
        if methods.duplicated(["Scope", "Case_ID"]).any():
            errors.append("method_log.csv contains duplicate case/scope rows")
        if not set(methods.get("Method", ())).issubset({"X13", "STL_FALLBACK", "NO_SIGNAL"}):
            errors.append("method_log.csv contains an unsupported method")
        if series.duplicated(["Month", "Scope", "Case_ID"]).any():
            errors.append("series.csv contains duplicate Month/Scope/Case_ID rows")
        if set(zip(series.get("Scope", ()), series.get("Case_ID", ()))) != expected_pairs:
            errors.append("series.csv case/scope coverage is incomplete")
        if set(zip(diagnostics.get("Scope", ()), diagnostics.get("Case_ID", ()))) != expected_pairs:
            errors.append("x13_diagnostics.csv case/scope coverage is incomplete")
        if len(diagnostics) != len(expected_pairs) or diagnostics.duplicated(["Scope", "Case_ID"]).any():
            errors.append("x13_diagnostics.csv must contain one row per case/scope")
        if set(zip(rebases.get("Scope", ()), rebases.get("Case_ID", ()))) != expected_pairs:
            errors.append("rebase_audit.csv case/scope coverage is incomplete")

        numeric_columns = (
            "Input_Rebased", "SA", "SA_Floored", "SA_Rebased", "MA3_Centered",
        )
        if not np.isfinite(series.loc[:, numeric_columns].to_numpy(dtype=float)).all():
            errors.append("series.csv contains non-finite analytical values")
        tolerance = 1e-8
        if (series["Input_Rebased"] < -tolerance).any() or (series["Input_Rebased"] > 100 + tolerance).any():
            errors.append("Input_Rebased is outside 0..100")
        if not np.allclose(
            series["SA_Floored"], series["SA"].clip(lower=0), rtol=0, atol=tolerance,
        ):
            errors.append("SA_Floored does not equal max(SA, 0)")
        for column in ("SA_Floored", "SA_Rebased", "MA3_Centered"):
            if (series[column] < -tolerance).any():
                errors.append(f"{column} contains negative values")
        for column in ("SA_Rebased", "MA3_Centered"):
            if (series[column] > 100 + tolerance).any():
                errors.append(f"{column} exceeds 100")
        expected_ma3 = series.groupby(["Scope", "Case_ID"], sort=False)["SA_Rebased"].transform(
            lambda values: values.rolling(3, center=True, min_periods=1).mean()
        )
        if not np.allclose(series["MA3_Centered"], expected_ma3, rtol=0, atol=tolerance):
            errors.append("MA3_Centered does not match centered MA3 of SA_Rebased")

        for scope, config in SCOPES.items():
            scoped = series[series["Scope"] == scope]
            raw_map = load_scope_raw(root, cases, tuple(config["geos"]), str(config["start"]))
            expected_months = next(iter(raw_map.values())).index.strftime("%Y-%m").tolist()
            expected_window = {
                "geographies": list(config["geos"]),
                "required_geographies_n": len(config["geos"]),
                "start": expected_months[0],
                "end": expected_months[-1],
                "months": len(expected_months),
            }
            if manifest.get("windows", {}).get(scope) != expected_window:
                errors.append(f"manifest window for {scope} differs from canonical raw")
            for case in cases:
                actual_months = scoped.loc[scoped["Case_ID"] == case.case_id, "Month"].tolist()
                if actual_months != expected_months:
                    errors.append(f"{scope}/{case.case_id} monthly support differs from canonical raw")
                    break

        post_status_by_pair = methods.set_index(["Scope", "Case_ID"])["Post_SA_Status"]
        maxima = series.groupby(["Scope", "Case_ID"])["SA_Rebased"].max()
        for pair, maximum in maxima.items():
            target = 0.0 if post_status_by_pair.get(pair) == "NO_SIGNAL" else 100.0
            if not math.isclose(float(maximum), target, rel_tol=0, abs_tol=tolerance):
                errors.append(f"{pair[0]}/{pair[1]} post-SA rebase max is not {target:g}")
                break

        expected_counts = {
            "cases": len(cases),
            "t1_cases": sum(case.tier == "T1" for case in cases),
            "t2_cases": sum(case.tier == "T2" for case in cases),
            "case_scope_series": len(methods),
            "x13": int((methods["Method"] == "X13").sum()),
            "stl_fallback": int((methods["Method"] == "STL_FALLBACK").sum()),
            "no_signal": int((methods["Method"] == "NO_SIGNAL").sum()),
        }
        if manifest.get("counts") != expected_counts:
            errors.append("manifest method/case counts differ from derived CSV")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "counts": manifest.get("counts", {}),
        "source_digest_sha256": digest,
        "implementation_digest_sha256": code_digest,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="rebuild and byte-compare outputs")
    mode.add_argument("--audit", action="store_true", help="validate hashes/schema without X-13")
    result.add_argument("--root", type=Path, default=ROOT)
    result.add_argument("--output-dir", type=Path)
    result.add_argument("--x13-path")
    result.add_argument("--timeout", type=int, default=60)
    result.add_argument("--fallback", choices=("stl", "error"), default="stl")
    result.add_argument("--allow-unverified-x13", action="store_true")
    result.add_argument("--quiet", action="store_true")
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.audit:
            result = audit_outputs(args.root, args.output_dir)
            exit_code = int(result["status"] != "PASS")
        else:
            result = build(
                args.root, args.output_dir, args.x13_path, args.timeout, args.fallback,
                args.check, args.allow_unverified_x13, args.quiet or args.json,
            )
            exit_code = 0
    except (PipelineError, ValueError, KeyError) as exc:
        result, exit_code = {"status": "FAIL", "error": str(exc)}, 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(" ".join(f"{key}={value}" for key, value in result.items()))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
