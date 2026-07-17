"""Pure data-contract helpers for the portable analytical pipeline."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


ISAN5 = ("TH-30", "TH-31", "TH-34", "TH-40", "TH-41")
SCOPES = {
    "TH": {"geos": ("TH",), "start": "2011-01"},
    "REG_ISAN5": {"geos": ISAN5, "start": "2014-01"},
}


class PipelineError(RuntimeError):
    """A deterministic input, environment, or analytical build failure."""


@dataclass(frozen=True)
class Case:
    case_id: str
    tier: str
    case_type: str
    members: tuple[str, ...]
    segment: str
    factor: str
    name_th: str


def load_cases(path: str | Path) -> list[Case]:
    """Load T1 keywords and group T2 member rows into family cases."""

    path = Path(path)
    required = {
        "Keyword_ID", "Keyword_TH", "Tier", "Segment", "Factor",
        "Case_ID", "Case_Type", "Family_ID", "Family_Name_TH",
    }
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise PipelineError(f"{path.name}: missing columns {', '.join(sorted(missing))}")
        rows = list(reader)

    grouped: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    seen_keywords: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        normalized = {key: (value or "").strip() for key, value in row.items()}
        case_id = normalized["Case_ID"].upper()
        keyword_id = normalized["Keyword_ID"].upper()
        tier = normalized["Tier"].upper()
        case_type = normalized["Case_Type"]
        if not case_id or not keyword_id:
            raise PipelineError(f"{path.name}:{row_number}: empty Case_ID or Keyword_ID")
        if keyword_id in seen_keywords:
            raise PipelineError(f"{path.name}:{row_number}: duplicate Keyword_ID {keyword_id}")
        seen_keywords.add(keyword_id)
        if tier not in {"T1", "T2"}:
            raise PipelineError(f"{path.name}:{row_number}: unsupported Tier {tier!r}")
        if case_type not in {"keyword", "family_member"}:
            raise PipelineError(f"{path.name}:{row_number}: unsupported Case_Type {case_type!r}")
        normalized.update(Case_ID=case_id, Keyword_ID=keyword_id, Tier=tier)
        if case_id not in grouped:
            grouped[case_id] = []
            order.append(case_id)
        grouped[case_id].append(normalized)

    cases: list[Case] = []
    for case_id in order:
        members = grouped[case_id]
        first = members[0]
        for field in ("Tier", "Case_Type", "Segment", "Factor", "Family_Name_TH"):
            if len({row[field] for row in members}) != 1:
                raise PipelineError(f"{path.name}: {case_id} has inconsistent {field}")
        tier = first["Tier"]
        case_type = first["Case_Type"]
        if tier == "T1" and (case_type != "keyword" or len(members) != 1):
            raise PipelineError(f"{path.name}: T1 case {case_id} must contain one keyword")
        if tier == "T1" and members[0]["Keyword_ID"] != case_id:
            raise PipelineError(f"{path.name}: T1 Case_ID must equal Keyword_ID for {case_id}")
        if tier == "T2":
            if case_type != "family_member" or len(members) < 2:
                raise PipelineError(f"{path.name}: T2 case {case_id} must contain at least two family members")
            if any(row["Family_ID"].upper() != case_id for row in members):
                raise PipelineError(f"{path.name}: T2 Family_ID must equal Case_ID for {case_id}")
            if not first["Family_Name_TH"]:
                raise PipelineError(f"{path.name}: T2 case {case_id} has an empty Family_Name_TH")
        cases.append(Case(
            case_id=case_id,
            tier=tier,
            case_type=case_type,
            members=tuple(row["Keyword_ID"] for row in members),
            segment=first["Segment"],
            factor=first["Factor"],
            name_th=first["Keyword_TH"] if tier == "T1" else first["Family_Name_TH"],
        ))
    return cases


def _as_case(case: Case | Mapping[str, Any]) -> Case:
    if isinstance(case, Case):
        return case
    tier = str(case["tier"])
    return Case(
        case_id=str(case["case_id"]), tier=tier,
        case_type=str(case.get("case_type", "keyword" if tier == "T1" else "family_member")),
        members=tuple(case["members"]), segment=str(case.get("segment", "")),
        factor=str(case.get("factor", "")), name_th=str(case.get("name_th", case["case_id"])),
    )


def rebase_max100(series: pd.Series) -> tuple[pd.Series, dict[str, Any]]:
    """Scale a non-negative series to max=100; keep all-zero at zero."""

    if not isinstance(series, pd.Series) or series.empty:
        raise ValueError("rebase_max100 requires a non-empty pandas Series")
    values = series.astype(float)
    array = values.to_numpy()
    if not np.isfinite(array).all():
        raise ValueError("rebase_max100 received non-finite values")
    if float(array.min()) < -1e-12:
        raise ValueError("rebase_max100 requires non-negative values")
    pre_max = float(array.max())
    if pre_max <= 0:
        return values * 0.0, {"status": "NO_SIGNAL", "pre_max": 0.0}
    return values * (100.0 / pre_max), {"status": "OK", "pre_max": pre_max}


def centered_ma3(series: pd.Series) -> pd.Series:
    """Floor at zero before the canonical centered 3-month mean."""

    return series.astype(float).clip(lower=0).rolling(3, center=True, min_periods=1).mean()


def _validate_same_months(series_by_label: Mapping[str, pd.Series]) -> pd.DatetimeIndex:
    if not series_by_label:
        raise ValueError("no source series supplied")
    first_label, first = next(iter(series_by_label.items()))
    expected = pd.DatetimeIndex(first.index)
    if expected.empty:
        raise ValueError(f"{first_label}: empty source series")
    for label, series in series_by_label.items():
        if not pd.DatetimeIndex(series.index).equals(expected):
            raise ValueError(f"monthly support mismatch: {label} differs from {first_label}")
    return expected


def build_pre_sa_for_case(
    case: Case | Mapping[str, Any],
    raw_map: Mapping[tuple[str, str], pd.Series],
    scope: Sequence[str],
) -> dict[str, Any]:
    """Apply mentor-v3 A/B/C rebases before seasonal adjustment."""

    case = _as_case(case)
    geos = tuple(scope)
    if not geos:
        raise ValueError("scope must contain at least one geography")
    selected: dict[str, pd.Series] = {}
    for member in case.members:
        for geo in geos:
            key = (member, geo)
            if key not in raw_map:
                raise ValueError(f"missing required source {member}__{geo}")
            selected[f"{member}__{geo}"] = raw_map[key]
    _validate_same_months(selected)

    audits: list[dict[str, Any]] = []
    geo_series: list[pd.Series] = []
    signal_n = 0
    for geo in geos:
        member_series: list[pd.Series] = []
        for member in case.members:
            rebased, audit = rebase_max100(raw_map[(member, geo)])
            signal_n += int(audit["status"] == "OK")
            audits.append({
                "stage": "A_MEMBER_GEO", "member_id": member, "geo": geo,
                "status": audit["status"], "pre_max": audit["pre_max"],
                "contributors_n": 1, "required_n": 1,
            })
            member_series.append(rebased)

        within_geo = pd.concat(member_series, axis=1).mean(axis=1)
        if case.tier == "T2":
            within_geo, audit = rebase_max100(within_geo)
            audits.append({
                "stage": "B_FAMILY_GEO", "member_id": "", "geo": geo,
                "status": audit["status"], "pre_max": audit["pre_max"],
                "contributors_n": sum(float(raw_map[(m, geo)].max()) > 0 for m in case.members),
                "required_n": len(case.members),
            })
        geo_series.append(within_geo)

    scope_mean = pd.concat(geo_series, axis=1).mean(axis=1)
    result, audit = rebase_max100(scope_mean)
    audits.append({
        "stage": "C_SCOPE", "member_id": "", "geo": "+".join(geos),
        "status": audit["status"], "pre_max": audit["pre_max"],
        "contributors_n": sum(float(series.max()) > 0 for series in geo_series),
        "required_n": len(geos),
    })
    return {
        "series": result, "audits": audits, "status": audit["status"],
        "signal_n": signal_n, "required_n": len(case.members) * len(geos),
    }


def read_raw_series(path: Path, start: str) -> pd.Series:
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        raise PipelineError(f"cannot read {path}: {exc}") from exc
    if not {"Month", "Value"}.issubset(frame.columns):
        raise PipelineError(f"{path.name}: required columns Month and Value are missing")
    try:
        index = pd.to_datetime(frame["Month"].astype(str) + "-01", format="%Y-%m-%d")
        values = pd.to_numeric(frame["Value"], errors="raise").astype(float)
    except Exception as exc:
        raise PipelineError(f"{path.name}: invalid Month or Value: {exc}") from exc
    series = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(index), name=path.stem)
    if series.index.has_duplicates or not series.index.is_monotonic_increasing:
        raise PipelineError(f"{path.name}: months must be unique and increasing")
    if series.empty or not np.isfinite(series.to_numpy()).all():
        raise PipelineError(f"{path.name}: values must be finite")
    if float(series.min()) < 0 or float(series.max()) > 100:
        raise PipelineError(f"{path.name}: values must be in the 0..100 range")
    if not series.index.equals(pd.date_range(series.index[0], series.index[-1], freq="MS")):
        raise PipelineError(f"{path.name}: monthly series contains a gap")
    trimmed = series.loc[pd.Timestamp(f"{start}-01"):]
    if trimmed.empty or trimmed.index[0] != pd.Timestamp(f"{start}-01"):
        raise PipelineError(f"{path.name}: does not cover required start {start}")
    return trimmed


def load_scope_raw(
    root: Path, cases: Sequence[Case], geos: Sequence[str], start: str,
) -> dict[tuple[str, str], pd.Series]:
    raw_map: dict[tuple[str, str], pd.Series] = {}
    for member in sorted({member for case in cases for member in case.members}):
        for geo in geos:
            path = root / "data" / "series" / f"{member}__{geo}.csv"
            if not path.is_file():
                raise PipelineError(f"missing canonical series {path.relative_to(root)}")
            raw_map[(member, geo)] = read_raw_series(path, start)
    _validate_same_months({f"{m}__{g}": s for (m, g), s in raw_map.items()})
    return raw_map
