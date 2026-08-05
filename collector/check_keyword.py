#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ตรวจว่าคำค้นผ่านด่านคัดกรองหรือไม่ (อ่านอย่างเดียว ไม่แก้ข้อมูล)

ด่านที่ 1 National  : เดือนที่ค่าเป็นศูนย์ต้องไม่เกิน 25% ของช่วง 2014-01 ถึงปัจจุบัน ที่ระดับ TH
ด่านที่ 2 Regional  : ต้องมีสัญญาณครบทั้ง 5 จังหวัด จึงจะเป็นคำเดี่ยว T1 ได้
ด่านที่ 3 Tier      : สรุปว่าคำนั้นควรอยู่ชั้นไหน

ต้องเก็บข้อมูลของคำนั้นเข้าคลังก่อน ถึงจะตรวจได้

Examples:
  python -X utf8 collector/check_keyword.py FP014
  python -X utf8 collector/check_keyword.py FP014 FU015 --json
  python -X utf8 collector/check_keyword.py --all
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.audit import (  # noqa: E402
    RAW_GEOS,
    SIGNAL_WINDOW_START,
    SIGNAL_ZERO_SHARE_MAX,
    classify_signal,
)

ROOT = Path(__file__).resolve().parent.parent
PROVINCES = tuple(geo for geo in RAW_GEOS if geo != "TH")


def _read_series(root: Path, keyword_id: str, geo: str) -> tuple[list[str], list[float]]:
    path = root / "data" / "series" / f"{keyword_id}__{geo}.csv"
    if not path.exists():
        return [], []
    months: list[str] = []
    values: list[float] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            if len(row) >= 2:
                months.append(row[0])
                values.append(float(row[1]))
    return months, values


def _read_incoming(root: Path, keyword_id: str) -> dict[str, tuple[list[str], list[float]]]:
    """Parse a candidate's freshly downloaded CSVs without touching the archive.

    Screening has to happen before ingest. Once a keyword is in data/series and
    data/catalog.json, backing out a candidate that failed means cleaning three
    places by hand, so the whole point of this mode is that a rejected candidate
    costs one deleted row and a few deleted downloads.
    """

    from collector.ingest import load_keyword_map, parse_file  # noqa: E402

    kw_to_id, ids = load_keyword_map()
    found: dict[str, tuple[list[str], list[float]]] = {}
    incoming = root / "incoming"
    if not incoming.is_dir():
        return found
    for path in sorted(incoming.glob("*.csv")):
        try:
            parsed_id, geo, points = parse_file(path, kw_to_id, ids)
        except Exception:
            continue
        if parsed_id.upper() != keyword_id.upper():
            continue
        found[geo] = ([month for month, _ in points], [value for _, value in points])
    return found


def check_keyword(keyword_id: str, root: Path = ROOT, *, from_incoming: bool = False) -> dict:
    """Return the three screening-gate verdicts for one keyword."""

    if from_incoming:
        collected = _read_incoming(root, keyword_id)
        # A half-finished download looks exactly like a keyword with no regional
        # signal, so refuse to judge until every area has arrived. Rejecting a
        # good keyword because its province files were still queued would be the
        # worst possible failure of this check.
        if collected and len(collected) < len(RAW_GEOS):
            missing = [geo for geo in RAW_GEOS if geo not in collected]
            return {
                "keyword_id": keyword_id,
                "collected": False,
                "national_pass": None,
                "regional_support": None,
                "suggested_tier": None,
                "reason": (
                    f"เก็บมาแล้ว {len(collected)}/{len(RAW_GEOS)} พื้นที่ ยังขาด {', '.join(missing)} "
                    "รอให้คิวเก็บครบทุกพื้นที่ก่อนแล้วค่อยตรวจ"
                ),
            }
        months, values = collected.get("TH", ([], []))
    else:
        collected = None
        months, values = _read_series(root, keyword_id, "TH")
    if not months:
        return {
            "keyword_id": keyword_id,
            "collected": False,
            "national_pass": None,
            "regional_support": None,
            "suggested_tier": None,
            "reason": (
                "ยังไม่พบไฟล์ระดับประเทศใน incoming/ ต้องเก็บก่อนถึงจะตรวจได้"
                if from_incoming
                else "ยังไม่มีข้อมูลระดับประเทศ ต้องเก็บก่อนถึงจะตรวจได้"
            ),
        }

    tier, zeros, window = classify_signal(values, months)
    national_pass = window > 0 and zeros <= SIGNAL_ZERO_SHARE_MAX * window

    support = []
    for geo in PROVINCES:
        if collected is not None:
            _, province_values = collected.get(geo, ([], []))
        else:
            _, province_values = _read_series(root, keyword_id, geo)
        if province_values and max(province_values) > 0:
            support.append(geo)

    if not national_pass:
        suggested, reason = "ไม่ผ่าน", "ตกด่าน National สัญญาณบางเกินไปแม้ที่ระดับประเทศ"
    elif len(support) == len(PROVINCES):
        suggested, reason = "T1", "ผ่านทั้งสองด่าน ใช้เป็นคำเดี่ยวได้"
    elif support:
        suggested, reason = (
            "T2",
            f"ผ่าน National แต่มีสัญญาณ {len(support)}/{len(PROVINCES)} จังหวัด "
            "ต้องเข้าครอบครัวที่มีสมาชิกอย่างน้อย 2 คำ",
        )
    else:
        suggested, reason = "T3", "ผ่าน National แต่ไม่มีสัญญาณระดับจังหวัดเลย ใช้เป็น context เท่านั้น"

    return {
        "keyword_id": keyword_id,
        "collected": True,
        "signal_window_start": SIGNAL_WINDOW_START,
        "national_window_months": window,
        "national_zero_months": zeros,
        "national_zero_share": round(zeros / window, 4) if window else None,
        "national_max_allowed": int(SIGNAL_ZERO_SHARE_MAX * window),
        "national_tier": tier,
        "national_pass": national_pass,
        "regional_support": len(support),
        "regional_support_total": len(PROVINCES),
        "regional_support_geos": support,
        "suggested_tier": suggested,
        "reason": reason,
    }


def _format(result: dict) -> str:
    kid = result["keyword_id"]
    if not result["collected"]:
        return f"{kid}: {result['reason']}"
    national = "ผ่าน" if result["national_pass"] else "ไม่ผ่าน"
    return (
        f"{kid}\n"
        f"  ด่าน 1 National : {national} "
        f"(เดือนศูนย์ {result['national_zero_months']}/{result['national_window_months']} เดือน "
        f"เพดาน {result['national_max_allowed']} เดือน ตั้งแต่ {result['signal_window_start']})\n"
        f"  ด่าน 2 Regional : มีสัญญาณ {result['regional_support']}/{result['regional_support_total']} จังหวัด\n"
        f"  ด่าน 3 Tier     : {result['suggested_tier']} — {result['reason']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("keyword_ids", nargs="*", help="Keyword_ID ที่ต้องการตรวจ")
    parser.add_argument("--all", action="store_true", help="ตรวจทุกคำใน keywords.csv")
    parser.add_argument(
        "--incoming",
        action="store_true",
        help="ตรวจจากไฟล์ที่เพิ่งโหลดมาใน incoming/ ก่อนเอาเข้าคลัง (ใช้กับคำใหม่)",
    )
    parser.add_argument("--json", action="store_true", help="พิมพ์ผลแบบ JSON")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    keyword_ids = [kid.strip().upper() for kid in args.keyword_ids]
    if args.all:
        path = args.root / "keywords.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            keyword_ids = [row["Keyword_ID"].strip().upper() for row in csv.DictReader(handle)]
    if not keyword_ids:
        parser.error("ระบุ Keyword_ID อย่างน้อยหนึ่งตัว หรือใช้ --all")

    results = [
        check_keyword(kid, args.root, from_incoming=args.incoming) for kid in keyword_ids
    ]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(_format(result) for result in results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
