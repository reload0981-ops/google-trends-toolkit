"""Build helpers for portable SA/T2/rebase/centered-MA3 outputs."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .core import (
    Case,
    SCOPES,
    build_pre_sa_for_case,
    centered_ma3,
    load_cases,
    load_scope_raw,
    rebase_max100,
)
from .x13 import DIAGNOSTIC_FIELDS, seasonally_adjust


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "sa-pipeline-v3-portable-2"
OUTPUT_FILES = (
    "series.csv", "method_log.csv", "rebase_audit.csv",
    "x13_diagnostics.csv", "quality_flags.csv", "manifest.json",
)
SERIES_COLUMNS = (
    "Month", "Scope", "Case_ID", "Tier", "Case_Type", "Segment", "Factor",
    "Case_Name_TH", "Input_Rebased", "SA", "SA_Floored", "SA_Rebased",
    "MA3_Centered",
)
QUALITY_COLUMNS = (
    "Case_ID", "Scope", "Execution_Status", "Diagnostic_Status",
    "Quality_Status", "Geo_Support_N", "Geo_Support_Total",
    "Coverage_Status", "MA3_Endpoint_Provisional",
)


def quality_flags(
    method: str,
    accept_status: str,
    geo_support_n: int,
    geo_support_total: int,
) -> dict[str, Any]:
    """Summarize execution, diagnostics, and geographic support without changing series."""

    if method == "X13":
        execution_status = "SUCCESS"
        diagnostic_status = (accept_status or "").strip().upper() or "NOT_AVAILABLE"
        quality_status = (
            "PASS"
            if diagnostic_status == "ACCEPTED"
            else "REVIEW"
        )
    elif method == "STL_FALLBACK":
        execution_status = "FALLBACK"
        diagnostic_status = "NOT_AVAILABLE"
        quality_status = "REVIEW"
    elif method == "NO_SIGNAL":
        execution_status = quality_status = "NO_SIGNAL"
        diagnostic_status = "NOT_APPLICABLE"
    else:
        raise ValueError(f"unsupported analytical method {method!r}")
    return {
        "Execution_Status": execution_status,
        "Diagnostic_Status": diagnostic_status,
        "Quality_Status": quality_status,
        "Geo_Support_N": int(geo_support_n),
        "Geo_Support_Total": int(geo_support_total),
        "Coverage_Status": "FULL" if geo_support_n == geo_support_total else "PARTIAL",
        "MA3_Endpoint_Provisional": "TRUE",
    }


def _number(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return f"{float(value):.10f}"


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
            count += 1
    return count


def _build_rows(
    root: Path,
    cases: Sequence[Case],
    executable: Path,
    timeout: int,
    fallback: str,
    quiet: bool,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]], list[dict[str, Any]], dict[str, Any],
]:
    series_rows: list[dict[str, Any]] = []
    method_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    scope_metadata: dict[str, Any] = {}

    for scope_name, config in SCOPES.items():
        geos, start = tuple(config["geos"]), str(config["start"])
        raw_map = load_scope_raw(root, cases, geos, start)
        index = next(iter(raw_map.values())).index
        scope_metadata[scope_name] = {
            "geographies": list(geos), "required_geographies_n": len(geos),
            "start": index[0].strftime("%Y-%m"), "end": index[-1].strftime("%Y-%m"),
            "months": len(index),
        }

        for case in cases:
            pre = build_pre_sa_for_case(case, raw_map, geos)
            adjustment = seasonally_adjust(pre["series"], executable, timeout, fallback)
            sa = adjustment.series.astype(float)
            floored = sa.clip(lower=0)
            rebased, post_audit = rebase_max100(floored)
            ma3 = centered_ma3(rebased)
            for month in index:
                series_rows.append({
                    "Month": month.strftime("%Y-%m"), "Scope": scope_name,
                    "Case_ID": case.case_id, "Tier": case.tier,
                    "Case_Type": case.case_type, "Segment": case.segment,
                    "Factor": case.factor, "Case_Name_TH": case.name_th,
                    "Input_Rebased": _number(pre["series"].loc[month]),
                    "SA": _number(sa.loc[month]), "SA_Floored": _number(floored.loc[month]),
                    "SA_Rebased": _number(rebased.loc[month]),
                    "MA3_Centered": _number(ma3.loc[month]),
                })
            method_rows.append({
                "Case_ID": case.case_id, "Scope": scope_name, "Tier": case.tier,
                "Case_Type": case.case_type, "Members": ";".join(case.members),
                "Status": adjustment.status, "Method": adjustment.method,
                "Reason": adjustment.reason, "Signal_Contributors_N": pre["signal_n"],
                "Required_Contributors_N": pre["required_n"],
                "Post_SA_Status": post_audit["status"],
                "Post_SA_Pre_Max": _number(post_audit["pre_max"]),
            })
            for audit in pre["audits"]:
                audit_rows.append({
                    "Case_ID": case.case_id, "Scope": scope_name, "Tier": case.tier,
                    "Stage": audit["stage"], "Member_ID": audit["member_id"],
                    "Geo": audit["geo"], "Status": audit["status"],
                    "Pre_Max": _number(audit["pre_max"]),
                    "Contributors_N": audit["contributors_n"], "Required_N": audit["required_n"],
                })
            audit_rows.append({
                "Case_ID": case.case_id, "Scope": scope_name, "Tier": case.tier,
                "Stage": "D_POST_SA", "Member_ID": "", "Geo": scope_name,
                "Status": post_audit["status"], "Pre_Max": _number(post_audit["pre_max"]),
                "Contributors_N": int(post_audit["status"] == "OK"), "Required_N": 1,
            })
            diagnostic_rows.append({
                "Case_ID": case.case_id, "Scope": scope_name, "Method": adjustment.method,
                **{field: _number(adjustment.diagnostics.get(field)) for field in DIAGNOSTIC_FIELDS},
                "Accept_Status": adjustment.diagnostics.get("Accept_Status", ""),
            })
            scope_audit = next(audit for audit in pre["audits"] if audit["stage"] == "C_SCOPE")
            quality_rows.append({
                "Case_ID": case.case_id,
                "Scope": scope_name,
                **quality_flags(
                    adjustment.method,
                    str(adjustment.diagnostics.get("Accept_Status", "")),
                    int(scope_audit["contributors_n"]),
                    int(scope_audit["required_n"]),
                ),
            })
            if not quiet:
                print(f"{scope_name:10s} {case.case_id:8s} {adjustment.method}")

    counts = {
        "cases": len(cases), "t1_cases": sum(c.tier == "T1" for c in cases),
        "t2_cases": sum(c.tier == "T2" for c in cases),
        "case_scope_series": len(method_rows),
        "x13": sum(row["Method"] == "X13" for row in method_rows),
        "stl_fallback": sum(row["Method"] == "STL_FALLBACK" for row in method_rows),
        "no_signal": sum(row["Method"] == "NO_SIGNAL" for row in method_rows),
    }
    return series_rows, method_rows, audit_rows, diagnostic_rows, quality_rows, {
        "scopes": scope_metadata, "counts": counts,
    }
