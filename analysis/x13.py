"""Pinned X-13 execution with timeout, diagnostics, and explicit fallback."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.x13 import x13_arima_analysis

from .core import PipelineError


EXPECTED_SHA256 = "2e43194361ee096797f0431765193c316196ea6776f11535e76281a413d49669"
EXPECTED_VERSION = "1.1 Build 62"
DIAGNOSTIC_FIELDS = (
    *(f"M{i}" for i in range(1, 12)),
    "Q_Without_M2", "Q_With_M2",
    "F_Stable_B1", "F_Stable_B1_PValue",
    "F_Stable_D8", "F_Stable_D8_PValue",
    "F_Moving_D8", "F_Moving_D8_PValue",
    "Kruskal_Wallis_Chi", "Kruskal_Wallis_PValue",
)


class X13SeriesError(PipelineError):
    """An expected per-series model/timeout failure eligible for STL fallback."""


@dataclass(frozen=True)
class AdjustmentResult:
    series: pd.Series
    method: str
    status: str
    reason: str
    diagnostics: dict[str, Any]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_x13(root: Path, explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("X13PATH"):
        candidates.append(Path(os.environ["X13PATH"]).expanduser())
    candidates.extend((
        root / ".tools" / "x13" / "1.1-b62" / "x13as.exe",
        root / ".tools" / "x13" / "1.1-b62" / "x13as",
    ))
    on_path = shutil.which("x13as") or shutil.which("x13as.exe")
    if on_path:
        candidates.append(Path(on_path))

    checked: list[str] = []
    for candidate in candidates:
        if candidate.is_dir():
            for name in ("x13as.exe", "x13as", "x13as_ascii.exe"):
                executable = candidate / name
                checked.append(str(executable))
                if executable.is_file():
                    return executable.resolve()
        else:
            checked.append(str(candidate))
            if candidate.is_file():
                return candidate.resolve()
    detail = f" Checked: {', '.join(checked)}" if checked else ""
    raise PipelineError(
        "X-13 binary not found. Run scripts/bootstrap-analysis-windows.ps1 "
        "or pass --x13-path." + detail
    )


def verify_x13(path: Path, allow_unverified: bool = False) -> dict[str, str]:
    digest = sha256(path)
    try:
        result = subprocess.run([str(path)], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PipelineError(f"cannot execute X-13 at {path}: {exc}") from exc
    match = re.search(
        r"Version Number\s+([^\r\n]+)", f"{result.stdout}\n{result.stderr}", re.IGNORECASE,
    )
    version = match.group(1).strip() if match else "unknown"
    if not allow_unverified and digest.lower() != EXPECTED_SHA256:
        raise PipelineError(f"X-13 SHA-256 mismatch: expected {EXPECTED_SHA256}, found {digest}")
    if not allow_unverified and version.lower() != EXPECTED_VERSION.lower():
        raise PipelineError(f"X-13 version mismatch: expected {EXPECTED_VERSION}, found {version}")
    return {"version": version, "sha256": digest.lower()}


def parse_diagnostics(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {field: None for field in DIAGNOSTIC_FIELDS}
    values["Accept_Status"] = ""
    for i in range(1, 12):
        match = re.search(rf"\bM\s*{i}\s*=\s*([-+]?\d+(?:\.\d+)?)", text)
        if match:
            values[f"M{i}"] = float(match.group(1))
    q_matches = re.findall(
        r"Q\s*\(without M2\)\s*=\s*([-+]?\d+(?:\.\d+)?)\s*"
        r"((?:CONDITIONALLY\s+)?(?:REJECTED|ACCEPTED))?",
        text, re.IGNORECASE,
    )
    if q_matches:
        q_value, q_status = next(
            (match for match in reversed(q_matches) if match[1]),
            q_matches[-1],
        )
        values["Q_Without_M2"] = float(q_value)
        values["Accept_Status"] = q_status.upper().rstrip(".")
    q_with = re.search(r"^\s*Q\s*=\s*([-+]?\d+(?:\.\d+)?)", text, re.MULTILINE)
    if q_with:
        values["Q_With_M2"] = float(q_with.group(1))

    tests = {
        "F_Stable_B1": r"F-test for stable seasonality from Table\s+B\s+1\.?\s*:\s*([\d.]+)\s+([\d.]+)%",
        "F_Stable_D8": r"F-test for stable seasonality from Table\s+D\s+8\.?\s*:\s*([\d.]+)\s+([\d.]+)%",
        "F_Moving_D8": r"F-test for moving seasonality from Table\s+D\s+8\.?\s*:\s*([\d.]+)\s+([\d.]+)%",
    }
    for field, pattern in tests.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            values[field] = float(match.group(1))
            values[f"{field}_PValue"] = float(match.group(2)) / 100
    match = re.search(
        r"Kruskal-Wallis\s+Chi Squared test\s+for stable seasonality from Table\s+D\s+8\.?"
        r"\s*:\s*([\d.]+)\s+([\d.]+)%",
        text,
        re.IGNORECASE,
    )
    if match:
        values["Kruskal_Wallis_Chi"] = float(match.group(1))
        values["Kruskal_Wallis_PValue"] = float(match.group(2)) / 100
    return values


def _parse_saved_series(text: str, index: pd.DatetimeIndex) -> pd.Series:
    months: list[str] = []
    values: list[float] = []
    for line in text.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 2:
            try:
                if not re.fullmatch(r"\d{6}", parts[0]):
                    continue
                months.append(parts[0])
                values.append(float(parts[1]))
            except ValueError:
                pass
    if len(values) != len(index):
        raise PipelineError(f"X-13 d11 length mismatch: expected {len(index)}, found {len(values)}")
    expected_months = pd.DatetimeIndex(index).strftime("%Y%m").tolist()
    if months != expected_months:
        raise PipelineError("X-13 d11 monthly support does not match the input series")
    return pd.Series(values, index=index, dtype=float)


def _x13_adjust(series: pd.Series, executable: Path, timeout: int) -> tuple[pd.Series, dict[str, Any]]:
    x13_input = series.astype(float).mask(series == 0, 0.001).asfreq("MS")
    spec = x13_arima_analysis(
        x13_input, log=False, outlier=False, print_stdout=False,
        x12path=str(executable), speconly=True,
    )
    spec = re.sub(
        r"x11\s*\{\s*save\s*=\s*\(d11\s+d12\s+d13\)\s*\}",
        "x11{ save=(d11 d12 d13) print=all }", spec, flags=re.IGNORECASE,
    )
    if "print=all" not in spec:
        raise PipelineError("could not enable X-13 diagnostics in generated spec")

    with tempfile.TemporaryDirectory(prefix="gt_x13_") as temp_name:
        temp = Path(temp_name)
        input_base, output_base = temp / "input", temp / "output"
        input_base.with_suffix(".spc").write_text(spec, encoding="utf-8", newline="\n")
        try:
            result = subprocess.run(
                [str(executable), str(input_base), str(output_base)],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise X13SeriesError(f"X-13 timed out after {timeout}s") from exc
        except OSError as exc:
            raise PipelineError(f"cannot execute X-13: {exc}") from exc
        d11, out, err = (
            output_base.with_suffix(".d11"), output_base.with_suffix(".out"),
            output_base.with_suffix(".err"),
        )
        err_text = err.read_text(encoding="utf-8", errors="replace") if err.exists() else ""
        if result.returncode != 0 or not d11.is_file() or not out.is_file():
            err_text = err_text.replace(str(input_base.with_suffix(".spc")), "input.spc")
            error_lines = [
                line.strip() for line in err_text.splitlines()
                if line.strip().upper().startswith("ERROR:")
            ]
            reason = " ".join(error_lines or err_text.split())[:300]
            if not reason:
                reason = " ".join(result.stdout.split())[:300]
            raise X13SeriesError(
                f"X-13 failed (exit {result.returncode}): {reason or 'no output'}"
            )
        adjusted = _parse_saved_series(
            d11.read_text(encoding="utf-8", errors="replace"), pd.DatetimeIndex(series.index),
        )
        diagnostics = parse_diagnostics(out.read_text(encoding="utf-8", errors="replace"))
        return adjusted, diagnostics


def seasonally_adjust(
    series: pd.Series, executable: Path, timeout: int = 60, fallback: str = "stl",
) -> AdjustmentResult:
    empty_diagnostics = {field: None for field in DIAGNOSTIC_FIELDS} | {"Accept_Status": ""}
    if float(series.max()) <= 0:
        return AdjustmentResult(
            series.astype(float) * 0, "NO_SIGNAL", "NO_SIGNAL",
            "all pre-SA values are zero", empty_diagnostics,
        )
    try:
        adjusted, diagnostics = _x13_adjust(series, executable, timeout)
        return AdjustmentResult(adjusted, "X13", "OK", "", diagnostics)
    except X13SeriesError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        if fallback != "stl":
            raise PipelineError(reason) from exc
        x13_input = series.astype(float).mask(series == 0, 0.001).asfreq("MS")
        try:
            fitted = STL(x13_input, period=12, robust=True).fit()
        except Exception as fallback_exc:
            raise PipelineError(
                f"X-13 failed ({reason}); STL fallback also failed: {fallback_exc}"
            ) from fallback_exc
        return AdjustmentResult(
            x13_input - fitted.seasonal, "STL_FALLBACK", "FALLBACK", reason[:300],
            empty_diagnostics,
        )
