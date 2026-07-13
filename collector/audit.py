#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic health audit for the Google Trends Toolkit dataset.

The audit is deliberately read-only and uses only Python's standard library.
It separates file/shape problems from coverage and signal quality so that an
absent Google Trends export is never confused with a valid all-zero series.

Examples:
  python -X utf8 collector/audit.py
  python -X utf8 collector/audit.py --json
  python -X utf8 collector/audit.py --strict
  python -X utf8 collector/audit.py --strict --require-latest
  python -X utf8 collector/audit.py --require-latest 2026-06
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
RAW_GEOS = ("TH", "TH-30", "TH-31", "TH-34", "TH-40", "TH-41")
SIGNAL_WINDOW_MONTHS = 64
SIGNAL_TIERS = ("VERY_GOOD", "ACCEPTABLE", "WEAK")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
CANONICAL_START = "2004-01-01"


def _month_number(month: str) -> int | None:
    match = MONTH_RE.fullmatch(month)
    if not match:
        return None
    year, number = int(match.group(1)), int(match.group(2))
    if not 1 <= number <= 12:
        return None
    return year * 12 + number - 1


def _latest_completed_month(today: date | None = None) -> str:
    today = today or date.today()
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _validate_no_data_meta(key: str, meta: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Validate proof that a canonical collection returned no observations."""

    prefix = f"data/catalog.json: {key}"
    errors: list[str] = []
    timeframe = meta.get("timeframe")
    parts = timeframe.split() if isinstance(timeframe, str) else []
    timeframe_start = parts[0] if len(parts) == 2 else None
    timeframe_end = parts[1] if len(parts) == 2 else None
    timeframe_end_date = _iso_date(timeframe_end)
    fetched_on = meta.get("fetched_on")
    fetched_on_date = _iso_date(fetched_on)

    if meta.get("status") != "no_data":
        errors.append(f"{prefix}.status must be 'no_data'")
    if not isinstance(meta.get("keyword"), str) or not meta["keyword"].strip():
        errors.append(f"{prefix}.keyword must be non-empty")
    if timeframe_start != CANONICAL_START or timeframe_end_date is None:
        errors.append(
            f"{prefix}.timeframe must be '{CANONICAL_START} YYYY-MM-DD'"
        )
    if type(meta.get("months")) is not int or meta.get("months") != 0:
        errors.append(f"{prefix}.months must be 0")
    if meta.get("first") is not None or meta.get("last") is not None:
        errors.append(f"{prefix}.first and .last must be null")
    if fetched_on_date is None:
        errors.append(f"{prefix}.fetched_on must be YYYY-MM-DD")

    fetched_at = meta.get("fetched_at")
    fetched_at_date = None
    if isinstance(fetched_at, str):
        try:
            fetched_at_date = datetime.fromisoformat(fetched_at).date()
        except ValueError:
            pass
    if fetched_at_date is None:
        errors.append(f"{prefix}.fetched_at must be an ISO datetime")
    if (
        timeframe_end_date is not None
        and fetched_on_date is not None
        and timeframe_end_date != fetched_on_date
    ):
        errors.append(f"{prefix}: timeframe end must equal fetched_on")
    if (
        fetched_at_date is not None
        and fetched_on_date is not None
        and fetched_at_date != fetched_on_date
    ):
        errors.append(f"{prefix}: fetched_at date must equal fetched_on")
    if not isinstance(meta.get("note"), str) or not meta["note"].strip():
        errors.append(f"{prefix}.note must be non-empty")

    fields = {
        "timeframe": timeframe,
        "timeframe_start": timeframe_start,
        "timeframe_end": timeframe_end,
        "fetched_on": fetched_on,
        "fetched_at": fetched_at,
        "source_note": meta.get("note"),
    }
    return errors, fields


def classify_signal(values: Iterable[float], window_months: int = SIGNAL_WINDOW_MONTHS) -> tuple[str, int, int]:
    """Return ``(tier, zero_count, observed_count)`` for the latest window.

    Tiers are methodology constants: no zeroes is VERY_GOOD, 1..16 zeroes is
    ACCEPTABLE, and more than 16 zeroes is WEAK.
    """

    recent = list(values)[-window_months:]
    zeros = sum(value == 0 for value in recent)
    if zeros == 0:
        tier = "VERY_GOOD"
    elif zeros <= 16:
        tier = "ACCEPTABLE"
    else:
        tier = "WEAK"
    return tier, zeros, len(recent)


def _read_keywords(path: Path, errors: list[str]) -> list[str]:
    if not path.exists():
        errors.append("keywords.csv: file not found")
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "Keyword_ID" not in reader.fieldnames:
                errors.append("keywords.csv: required column Keyword_ID is missing")
                return []
            raw_ids = [(row.get("Keyword_ID") or "").strip().upper() for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"keywords.csv: cannot read ({exc})")
        return []

    ids: list[str] = []
    seen: set[str] = set()
    for row_number, keyword_id in enumerate(raw_ids, start=2):
        if not keyword_id:
            errors.append(f"keywords.csv:{row_number}: empty Keyword_ID")
            continue
        if keyword_id in seen:
            errors.append(f"keywords.csv:{row_number}: duplicate Keyword_ID {keyword_id}")
            continue
        seen.add(keyword_id)
        ids.append(keyword_id)
    return ids


def _read_catalog(path: Path, errors: list[str]) -> tuple[dict[str, Any], Any]:
    if not path.exists():
        errors.append("data/catalog.json: file not found")
        return {}, None
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"data/catalog.json: cannot read ({exc})")
        return {}, None
    if not isinstance(catalog, dict):
        errors.append("data/catalog.json: root must be an object")
        return {}, None
    series = catalog.get("series", {})
    if not isinstance(series, dict):
        errors.append("data/catalog.json: series must be an object")
        series = {}
    return series, catalog.get("updated_at")


def _read_series(path: Path, key: str, errors: list[str]) -> dict[str, Any] | None:
    rel = f"data/series/{path.name}"
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            if not {"Month", "Value"}.issubset(fields):
                errors.append(f"{rel}: required columns Month and Value are missing")
                return None
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"{rel}: cannot read ({exc})")
        return None

    if not rows:
        errors.append(f"{rel}: series has no observations")
        return None

    months: list[str] = []
    values: list[float] = []
    valid = True
    for row_number, row in enumerate(rows, start=2):
        month = (row.get("Month") or "").strip()
        month_number = _month_number(month)
        if month_number is None:
            errors.append(f"{rel}:{row_number}: invalid month {month!r}")
            valid = False
        raw_value = (row.get("Value") or "").strip()
        try:
            value = float(raw_value)
        except ValueError:
            errors.append(f"{rel}:{row_number}: invalid value {raw_value!r}")
            valid = False
            continue
        if not math.isfinite(value) or not 0 <= value <= 100:
            errors.append(f"{rel}:{row_number}: value outside finite 0..100 range ({raw_value!r})")
            valid = False
        months.append(month)
        values.append(value)

    if not valid or len(months) != len(values):
        return None

    if len(set(months)) != len(months):
        errors.append(f"{rel}: duplicate months")
        valid = False
    month_numbers = [_month_number(month) for month in months]
    if month_numbers != sorted(month_numbers):
        errors.append(f"{rel}: months are not strictly ascending")
        valid = False
    gaps = [
        (months[index - 1], months[index])
        for index in range(1, len(month_numbers))
        if month_numbers[index] != month_numbers[index - 1] + 1
    ]
    if gaps:
        sample = ", ".join(f"{left}->{right}" for left, right in gaps[:3])
        errors.append(f"{rel}: non-contiguous monthly series ({sample})")
        valid = False
    if not valid:
        return None

    tier, zero_count, observed_count = classify_signal(values)
    all_zero = all(value == 0 for value in values)
    return {
        "status": "available",
        "data_start": months[0],
        "data_end": months[-1],
        "months": len(months),
        "signal_status": "ALL_ZERO" if all_zero else "OBSERVED",
        "signal_tier": tier,
        "recent_window_months": SIGNAL_WINDOW_MONTHS,
        "recent_observed_months": observed_count,
        "recent_zeros": zero_count,
    }


def audit_dataset(root: str | Path = ROOT) -> dict[str, Any]:
    """Audit a repository dataset and return a deterministic JSON-safe report.

    No current timestamp is added. Given identical repository inputs, the
    returned object and its serialized representation are identical.
    """

    root = Path(root)
    errors: list[str] = []
    keyword_ids = _read_keywords(root / "keywords.csv", errors)
    catalog, catalog_updated_at = _read_catalog(root / "data" / "catalog.json", errors)
    series_dir = root / "data" / "series"
    if not series_dir.is_dir():
        errors.append("data/series: directory not found")

    expected_keys = [f"{keyword_id}__{geo}" for keyword_id in sorted(keyword_ids) for geo in RAW_GEOS]
    expected_set = set(expected_keys)
    paths = {path.stem: path for path in sorted(series_dir.glob("*.csv"))} if series_dir.is_dir() else {}

    unexpected = sorted(set(paths) - expected_set)
    for key in unexpected:
        errors.append(f"data/series/{paths[key].name}: unexpected keyword/geo series")

    unexpected_catalog = sorted(set(catalog) - expected_set)
    for key in unexpected_catalog:
        errors.append(f"data/catalog.json: unexpected series metadata {key}")

    per_series: dict[str, dict[str, Any]] = {}
    missing_keys: list[str] = []
    invalid_keys: list[str] = []
    confirmed_no_data_keys: list[str] = []
    invalid_no_data_keys: list[str] = []
    signal_tiers: Counter[str] = Counter()
    data_starts: list[str] = []
    data_ends: list[str] = []
    data_end_distribution: Counter[str] = Counter()

    for key in expected_keys:
        path = paths.get(key)
        meta = catalog.get(key)
        if path is None:
            if isinstance(meta, dict) and meta.get("status") == "no_data":
                no_data_errors, no_data_fields = _validate_no_data_meta(key, meta)
                if no_data_errors:
                    errors.extend(no_data_errors)
                    invalid_no_data_keys.append(key)
                    status, signal_status = "invalid_no_data", "INVALID_NO_DATA"
                else:
                    confirmed_no_data_keys.append(key)
                    status, signal_status = "no_data", "NO_DATA"
                per_series[key] = {
                    "status": status,
                    "data_start": None,
                    "data_end": None,
                    "months": 0,
                    "signal_status": signal_status,
                    "signal_tier": None,
                    "recent_window_months": SIGNAL_WINDOW_MONTHS,
                    "recent_observed_months": 0,
                    "recent_zeros": None,
                    **no_data_fields,
                }
                continue

            if isinstance(meta, dict):
                errors.append(
                    f"data/catalog.json: metadata for missing file {key} must use status='no_data'"
                )
            missing_keys.append(key)
            per_series[key] = {
                "status": "missing",
                "data_start": None,
                "data_end": None,
                "months": 0,
                "signal_status": "MISSING",
                "signal_tier": None,
                "recent_window_months": SIGNAL_WINDOW_MONTHS,
                "recent_observed_months": 0,
                "recent_zeros": None,
                "timeframe": meta.get("timeframe") if isinstance(meta, dict) else None,
                "timeframe_start": None,
                "timeframe_end": None,
                "fetched_on": meta.get("fetched_on") if isinstance(meta, dict) else None,
                "fetched_at": meta.get("fetched_at") if isinstance(meta, dict) else None,
                "source_note": meta.get("note") if isinstance(meta, dict) else None,
            }
            continue

        parsed = _read_series(path, key, errors)
        if parsed is None:
            invalid_keys.append(key)
            per_series[key] = {
                "status": "invalid",
                "data_start": None,
                "data_end": None,
                "months": 0,
                "signal_status": "INVALID",
                "signal_tier": None,
                "recent_window_months": SIGNAL_WINDOW_MONTHS,
                "recent_observed_months": 0,
                "recent_zeros": None,
                "timeframe": meta.get("timeframe") if isinstance(meta, dict) else None,
                "timeframe_start": None,
                "timeframe_end": None,
                "fetched_on": meta.get("fetched_on") if isinstance(meta, dict) else None,
                "fetched_at": meta.get("fetched_at") if isinstance(meta, dict) else None,
                "source_note": meta.get("note") if isinstance(meta, dict) else None,
            }
            continue

        if not isinstance(meta, dict):
            errors.append(f"data/catalog.json: metadata missing for {key}")
            meta = {}
        else:
            if meta.get("status") not in (None, "available"):
                errors.append(
                    f"data/catalog.json: {key}.status is {meta.get('status')!r}; expected 'available'"
                )
            expected_meta = {
                "months": parsed["months"],
                "first": parsed["data_start"],
                "last": parsed["data_end"],
            }
            for field, expected_value in expected_meta.items():
                if meta.get(field) != expected_value:
                    errors.append(
                        f"data/catalog.json: {key}.{field} is {meta.get(field)!r}; expected {expected_value!r}"
                    )

        timeframe = meta.get("timeframe")
        timeframe_parts = timeframe.split() if isinstance(timeframe, str) else []
        parsed["timeframe"] = timeframe
        parsed["timeframe_start"] = timeframe_parts[0] if len(timeframe_parts) == 2 else None
        parsed["timeframe_end"] = timeframe_parts[1] if len(timeframe_parts) == 2 else None
        parsed["fetched_on"] = meta.get("fetched_on")
        parsed["fetched_at"] = meta.get("fetched_at")
        parsed["source_note"] = meta.get("note")
        per_series[key] = parsed
        signal_tiers[parsed["signal_tier"]] += 1
        data_starts.append(parsed["data_start"])
        data_ends.append(parsed["data_end"])
        data_end_distribution[parsed["data_end"]] += 1

    all_zero_count = sum(item["signal_status"] == "ALL_ZERO" for item in per_series.values())
    report: dict[str, Any] = {
        "schema_version": 2,
        "expected_raw_series": len(expected_keys),
        "available_raw_series": sum(item["status"] == "available" for item in per_series.values()),
        "confirmed_no_data_raw_series": len(confirmed_no_data_keys),
        "confirmed_no_data_raw_series_keys": confirmed_no_data_keys,
        "missing_raw_series": len(missing_keys),
        "missing_raw_series_keys": missing_keys,
        "invalid_raw_series": len(invalid_keys),
        "invalid_raw_series_keys": invalid_keys,
        "invalid_no_data_raw_series": len(invalid_no_data_keys),
        "invalid_no_data_raw_series_keys": invalid_no_data_keys,
        "all_zero_raw_series": all_zero_count,
        "data_start": min(data_starts) if data_starts else None,
        "data_end": max(data_ends) if data_ends else None,
        "data_end_distribution": dict(sorted(data_end_distribution.items())),
        "catalog_updated_at": catalog_updated_at,
        "signal_window_months": SIGNAL_WINDOW_MONTHS,
        "signal_tiers": {tier: signal_tiers[tier] for tier in SIGNAL_TIERS},
        "structural_ok": not errors,
        "structural_error_count": len(errors),
        "structural_errors": errors,
        "per_series": per_series,
    }
    return report


def evaluate_gates(
    report: dict[str, Any], *, strict: bool = False, require_latest: str | None = None
) -> dict[str, Any]:
    """Evaluate optional release gates without mutating an audit report."""

    if require_latest == "auto":
        require_latest = _latest_completed_month()
    if require_latest is not None and _month_number(require_latest) is None:
        raise ValueError("--require-latest must be YYYY-MM")

    stale_available_keys: list[str] = []
    stale_no_data_keys: list[str] = []
    missing_keys = sorted(
        key for key, item in report["per_series"].items() if item["status"] == "missing"
    )
    invalid_keys = sorted(
        key for key, item in report["per_series"].items() if item["status"] == "invalid"
    )
    invalid_no_data_keys = sorted(
        key
        for key, item in report["per_series"].items()
        if item["status"] == "invalid_no_data"
    )
    if require_latest:
        stale_available_keys = sorted(
            key
            for key, item in report["per_series"].items()
            if item["status"] == "available" and item["data_end"] < require_latest
        )
        stale_no_data_keys = sorted(
            key
            for key, item in report["per_series"].items()
            if item["status"] == "no_data"
            and (
                (item.get("timeframe_end") or "")[:7] <= require_latest
                or (item.get("fetched_on") or "")[:7] <= require_latest
            )
        )
    structural_pass = not strict or bool(report["structural_ok"])
    complete_release_pass = not require_latest or (
        report["expected_raw_series"] > 0
        and not stale_available_keys
        and not stale_no_data_keys
        and not missing_keys
        and not invalid_keys
        and not invalid_no_data_keys
    )
    stale_keys = sorted(stale_available_keys + stale_no_data_keys)
    return {
        "strict_enabled": strict,
        "structural_pass": structural_pass,
        "required_latest": require_latest,
        "freshness_pass": complete_release_pass,
        "complete_release_pass": complete_release_pass,
        "stale_raw_series": len(stale_keys),
        "stale_raw_series_keys": stale_keys,
        "stale_available_raw_series": len(stale_available_keys),
        "stale_available_raw_series_keys": stale_available_keys,
        "stale_no_data_raw_series": len(stale_no_data_keys),
        "stale_no_data_raw_series_keys": stale_no_data_keys,
        "missing_raw_series": len(missing_keys),
        "missing_raw_series_keys": missing_keys,
        "invalid_raw_series": len(invalid_keys),
        "invalid_raw_series_keys": invalid_keys,
        "invalid_no_data_raw_series": len(invalid_no_data_keys),
        "invalid_no_data_raw_series_keys": invalid_no_data_keys,
        "pass": structural_pass and complete_release_pass,
    }


def _human_report(report: dict[str, Any], gate: dict[str, Any]) -> str:
    def key_summary(keys: list[str], limit: int = 24) -> str:
        shown = keys[:limit]
        suffix = f" … +{len(keys) - limit} more" if len(keys) > limit else ""
        return ", ".join(shown) + suffix

    tiers = report["signal_tiers"]
    lines = [
        "Google Trends Toolkit — data health",
        (
            f"Coverage: {report['available_raw_series']}/{report['expected_raw_series']} available | "
            f"{report['confirmed_no_data_raw_series']} confirmed no-data | "
            f"{report['missing_raw_series']} missing | "
            f"{report['invalid_raw_series']} invalid | "
            f"{report['invalid_no_data_raw_series']} invalid no-data"
        ),
        f"Range: {report['data_start'] or '-'} to {report['data_end'] or '-'} | catalog: {report['catalog_updated_at'] or '-'}",
        (
            f"Last {report['signal_window_months']} months: "
            f"VERY_GOOD {tiers['VERY_GOOD']} | ACCEPTABLE {tiers['ACCEPTABLE']} | "
            f"WEAK {tiers['WEAK']} | all-zero {report['all_zero_raw_series']}"
        ),
        (
            f"Structure: {'PASS' if report['structural_ok'] else 'FAIL'} "
            f"({report['structural_error_count']} errors)"
        ),
    ]
    if report["missing_raw_series_keys"]:
        lines.append("Missing: " + key_summary(report["missing_raw_series_keys"]))
    if report["confirmed_no_data_raw_series_keys"]:
        lines.append(
            "Confirmed no-data: " + key_summary(report["confirmed_no_data_raw_series_keys"])
        )
    if report["invalid_no_data_raw_series_keys"]:
        lines.append(
            "Invalid no-data: " + key_summary(report["invalid_no_data_raw_series_keys"])
        )
    for error in report["structural_errors"]:
        lines.append("ERROR: " + error)
    if gate["required_latest"]:
        lines.append(
            f"Complete release >= {gate['required_latest']}: "
            f"{'PASS' if gate['freshness_pass'] else 'FAIL'} "
            f"({gate['stale_available_raw_series']} stale available | "
            f"{gate['stale_no_data_raw_series']} stale no-data | "
            f"{gate['missing_raw_series']} missing | "
            f"{gate['invalid_raw_series']} invalid | "
            f"{gate['invalid_no_data_raw_series']} invalid no-data)"
        )
        if gate["stale_available_raw_series_keys"]:
            lines.append(
                "Stale available: " + key_summary(gate["stale_available_raw_series_keys"])
            )
        if gate["stale_no_data_raw_series_keys"]:
            lines.append(
                "Stale no-data: " + key_summary(gate["stale_no_data_raw_series_keys"])
            )
    lines.append("Gate: " + ("PASS" if gate["pass"] else "FAIL"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="fail on structural errors")
    parser.add_argument(
        "--require-latest",
        nargs="?",
        const="auto",
        metavar="YYYY-MM",
        help=(
            "fail unless every expected cell is current data or freshly confirmed no-data; "
            "omit value for latest completed month"
        ),
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    report = audit_dataset(args.root)
    try:
        gate = evaluate_gates(report, strict=args.strict, require_latest=args.require_latest)
    except ValueError as exc:
        parser.error(str(exc))

    if args.json:
        output = dict(report)
        output["gate"] = gate
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_human_report(report, gate))
    return 0 if gate["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
