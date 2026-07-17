#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google Trends Toolkit - ingest ไฟล์ CSV ที่โหลดมาเอง (จากหน้าเว็บ GT หรือ Extension)

เส้นทางหลักของการอัพเดทข้อมูล: โหลด CSV จาก Google Trends มาวางใน incoming/
แล้วรันคำสั่งนี้ มันจะ validate, จับคู่คำ, แปลงเป็นรายเดือน, เขียนเข้า data/series/
และ rebuild data.js ให้หน้าแสดงผลอัตโนมัติ (ใช้แค่ Python มาตรฐาน ไม่ต้องติดตั้งอะไร)

การใช้:
  python collector/ingest.py            # กิน .csv ทุกไฟล์ใน incoming/
  python collector/ingest.py --dry-run  # ตรวจอย่างเดียว ไม่เขียนอะไร

รูปแบบไฟล์ที่รู้จัก:
  1. Export จากหน้าเว็บ Google Trends: มีบรรทัดหัว "หมวดหมู่: ..." แล้วตามด้วย
     "เดือน,<คำ>: (<พื้นที่>)" หรือ "Month,<keyword>: (<place>)" (รายสัปดาห์ก็ได้ จะถูกเฉลี่ยเป็นรายเดือน)
  2. ไฟล์ที่ตั้งชื่อเอง: <ID>__<GEO>.csv (เช่น FP014__TH-40.csv) เนื้อในเป็น Time/Month,Value
  3. ไฟล์จากวงจรเดิมของโปรเจค: manual_<ID>.csv = ระดับประเทศ (TH)
  4. no_data_manifest__*.json จาก Chrome extension = หลักฐานว่า retry แล้วไม่มีข้อมูล

กติกา:
  - ค่า "<1" ถูกแปลงเป็น 0
  - เดือนปัจจุบันที่ยังไม่จบถูกตัดทิ้ง
  - ระดับจังหวัด: เดือนก่อน 2014-01 ถูกตัดทิ้งอัตโนมัติ (Google ปรับระบบ geo
    ช่วง 2011-2013 ข้อมูลจังหวัดก่อนหน้านั้นเป็นรู/break ใช้ไม่ได้ ระดับประเทศไม่ตัด)
  - เขียนแทนซีรีส์เดิมทั้งไฟล์ (ห้ามต่อท่อนข้อมูลคนละช่วง scale จะเพี้ยน)
  - จับคู่คำ/พื้นที่ไม่ได้ = ย้ายไฟล์ไป incoming/review/ ไม่เดาเด็ดขาด
"""

import argparse
import csv
import json
import math
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCOMING = ROOT / "incoming"
SERIES_DIR = ROOT / "data" / "series"
CATALOG_PATH = ROOT / "data" / "catalog.json"
KEYWORDS_CSV = ROOT / "keywords.csv"

TIME_WORDS = {"time", "month", "week", "day", "เดือน", "สัปดาห์", "วัน"}
PROVINCE_MIN_MONTH = "2014-01"  # จังหวัด/ภาคใช้ได้หลัง geo break (ดู README)
CANONICAL_START_MONTH = "2004-01"
CANONICAL_START_DATE = "2004-01-01"
NO_DATA_MANIFEST_SCHEMA = "google-trends-toolkit/no-data-manifest-v1"
PLACE_TO_GEO = {
    "ประเทศไทย": "TH", "thailand": "TH", "th": "TH",
    "นครราชสีมา": "TH-30", "nakhon ratchasima": "TH-30", "th-30": "TH-30",
    "บุรีรัมย์": "TH-31", "buri ram": "TH-31", "buriram": "TH-31", "th-31": "TH-31",
    "อุบลราชธานี": "TH-34", "ubon ratchathani": "TH-34", "th-34": "TH-34",
    "ขอนแก่น": "TH-40", "khon kaen": "TH-40", "th-40": "TH-40",
    "อุดรธานี": "TH-41", "udon thani": "TH-41", "th-41": "TH-41",
}
RAW_GEOS = frozenset(PLACE_TO_GEO.values())


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def load_keyword_map():
    with open(KEYWORDS_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    kw_to_id = {}
    for r in rows:
        kw_to_id.setdefault(norm(r["Keyword_TH"]), r["Keyword_ID"])
    ids = {r["Keyword_ID"].upper(): r["Keyword_TH"] for r in rows}
    return kw_to_id, ids


def parse_value(v):
    v = (v or "").strip().replace("%", "")
    if v == "":
        return None
    if v.startswith("<"):
        return 0.0
    try:
        return float(v)
    except ValueError:
        return None


def parse_month(s):
    s = (s or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})(-(\d{2}))?", s)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def latest_completed_month(today=None):
    today = today or date.today()
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def validate_canonical_coverage(geo, points, today=None):
    """Reject a short/stale export before it can replace a canonical series."""
    if not points:
        raise ValueError("ไม่มีข้อมูลรายเดือนสำหรับตรวจ canonical coverage")
    expected_first = CANONICAL_START_MONTH if geo == "TH" else PROVINCE_MIN_MONTH
    expected_last = latest_completed_month(today)
    actual_first, actual_last = points[0][0], points[-1][0]
    if actual_first != expected_first or actual_last != expected_last:
        raise ValueError(
            "ช่วงข้อมูลไม่ใช่ canonical long horizon: "
            f"คาด {expected_first} ถึง {expected_last}, "
            f"แต่ไฟล์มี {actual_first} ถึง {actual_last}; "
            "ให้ export 2004-01-01 ถึงวันนี้ใหม่ทั้งช่วง"
        )
    month_numbers = []
    for month, value in points:
        match = re.fullmatch(r"(\d{4})-(\d{2})", month)
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise ValueError(f"เดือนในไฟล์ไม่ถูกต้อง: {month!r}")
        month_numbers.append(int(match.group(1)) * 12 + int(match.group(2)) - 1)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f"ค่า {month} ต้องเป็นตัวเลข finite ในช่วง 0..100 (พบ {value!r})")
    if len(set(month_numbers)) != len(month_numbers):
        raise ValueError("ไฟล์มีเดือนซ้ำ")
    if month_numbers != sorted(month_numbers):
        raise ValueError("เดือนในไฟล์ไม่ได้เรียงจากเก่าไปใหม่")
    gaps = [
        (points[index - 1][0], points[index][0])
        for index in range(1, len(month_numbers))
        if month_numbers[index] != month_numbers[index - 1] + 1
    ]
    if gaps:
        sample = ", ".join(f"{left}->{right}" for left, right in gaps[:3])
        raise ValueError(f"ช่วงข้อมูลมีเดือนขาด ({sample})")


def _parse_iso_datetime(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"no-data manifest: {field} ต้องเป็น ISO datetime")
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"no-data manifest: {field} ไม่ใช่ ISO datetime") from exc


def parse_no_data_manifest(path, ids, catalog, series_dir=SERIES_DIR, today=None):
    """Validate one extension proof atomically and return catalog entries."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"อ่าน no-data manifest ไม่ได้: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != NO_DATA_MANIFEST_SCHEMA:
        raise ValueError(f"no-data manifest: schema ต้องเป็น {NO_DATA_MANIFEST_SCHEMA}")
    _parse_iso_datetime(payload.get("generated_at"), "generated_at")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("no-data manifest: entries ต้องเป็นรายการที่ไม่ว่าง")

    today = today or date.today()
    result, seen = [], set()
    for index, entry in enumerate(entries, 1):
        label = f"entries[{index}]"
        if not isinstance(entry, dict) or entry.get("status") != "NO_DATA":
            raise ValueError(f"no-data manifest: {label}.status ต้องเป็น NO_DATA")
        kid = str(entry.get("keyword_id") or "").strip().upper()
        geo = str(entry.get("geo_code") or "").strip().upper()
        if kid not in ids:
            raise ValueError(f"no-data manifest: {label}.keyword_id {kid!r} ไม่อยู่ใน keywords.csv")
        if norm(str(entry.get("keyword") or "")) != norm(ids[kid]):
            raise ValueError(f"no-data manifest: {label}.keyword ไม่ตรงกับ keywords.csv")
        if geo not in RAW_GEOS:
            raise ValueError(f"no-data manifest: {label}.geo_code {geo!r} ไม่รองรับ")
        key = f"{kid}__{geo}"
        if key in seen:
            raise ValueError(f"no-data manifest: key ซ้ำ {key}")
        seen.add(key)

        attempts = entry.get("attempts")
        no_data_attempts = entry.get("no_data_attempts")
        if (
            not isinstance(no_data_attempts, int)
            or isinstance(no_data_attempts, bool)
            or no_data_attempts < 2
        ):
            raise ValueError(f"no-data manifest: {label}.no_data_attempts ต้องอย่างน้อย 2")
        if (
            not isinstance(attempts, int)
            or isinstance(attempts, bool)
            or attempts < no_data_attempts
        ):
            raise ValueError(f"no-data manifest: {label}.attempts ต้องไม่น้อยกว่า no_data_attempts")
        reason = str(entry.get("reason") or "").strip()
        if not re.fullmatch(r"NO_VOLUME|NO_DATA_EXPORT_\d+B", reason):
            raise ValueError(f"no-data manifest: {label}.reason ไม่ใช่ no-data reason ที่รองรับ")
        timeframe = str(entry.get("timeframe") or "").strip()
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) (\d{4}-\d{2}-\d{2})", timeframe)
        if not match or match.group(1) != CANONICAL_START_DATE:
            raise ValueError(
                f"no-data manifest: {label}.timeframe ต้องเริ่ม {CANONICAL_START_DATE}"
            )
        try:
            end_date = date.fromisoformat(match.group(2))
        except ValueError as exc:
            raise ValueError(f"no-data manifest: {label}.timeframe end ไม่ใช่วันที่จริง") from exc
        if end_date > today:
            raise ValueError(f"no-data manifest: {label}.timeframe end อยู่ในอนาคต")
        observed_at = _parse_iso_datetime(entry.get("observed_at"), f"{label}.observed_at")
        if observed_at.date() != end_date:
            raise ValueError(
                f"no-data manifest: {label}.observed_at ต้องเป็นวันเดียวกับ timeframe end"
            )
        if (series_dir / f"{key}.csv").exists():
            raise ValueError(f"no-data manifest: {key} มี CSV เดิมอยู่ ห้ามเปลี่ยนเป็น no_data")
        previous = catalog.get("series", {}).get(key)
        if previous is not None and not (
            isinstance(previous, dict) and previous.get("status") == "no_data"
        ):
            raise ValueError(f"no-data manifest: {key} มี metadata เดิมที่ไม่ใช่ no_data")

        result.append((key, {
            "status": "no_data",
            "keyword": ids[kid],
            "timeframe": timeframe,
            "months": 0,
            "first": None,
            "last": None,
            "fetched_on": str(end_date),
            "fetched_at": observed_at.isoformat(timespec="seconds"),
            "note": (
                f"extension confirmed no data after {no_data_attempts} consecutive observations: {reason}; "
                f"manifest {path.name}"
            ),
        }))
    return result


def parse_file(path, kw_to_id, ids, today=None):
    """คืน (keyword_id, geo, [(YYYY-MM, value)]) หรือโยน ValueError พร้อมเหตุผล"""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines()]

    # หา header: บรรทัดแรกที่ field แรกเป็นคำบอกเวลา
    header_i, header = None, None
    for i, ln in enumerate(lines[:10]):
        cells = next(csv.reader([ln]), [])
        if cells and norm(cells[0]) in TIME_WORDS:
            header_i, header = i, cells
            break
    if header_i is None:
        raise ValueError("ไม่พบบรรทัดหัวตาราง (Time/Month/เดือน)")
    if len(header) < 2:
        raise ValueError("หัวตารางมีคอลัมน์เดียว")

    # ระบุ keyword + geo จากชื่อไฟล์ก่อน แล้ว cross-check กับ header ที่ระบุตัวตน
    kid = geo = None
    name = path.stem
    m = re.match(r"^([A-Za-z]{2}\d{3})__(TH(?:-\d{2})?)$", name)
    if m:
        kid, geo = m.group(1).upper(), m.group(2).upper()
    else:
        m = re.match(r"^manual_([A-Za-z]{2}\d{3})$", name)
        if m:
            kid, geo = m.group(1).upper(), "TH"
        else:
            m = re.match(
                r"^time_series_(TH(?:-\d{2})?)_\d{8}-\d{4}_\d{8}-\d{4}(?: \(\d+\))?$",
                name,
                re.IGNORECASE,
            )
            if m:
                geo = m.group(1).upper()

    # Header "Month,Value" เป็น canonical/manual format ที่ไม่ระบุตัวตน
    # จึงใช้ได้เฉพาะเมื่อชื่อไฟล์ระบุ ID/GEO ครบแล้ว
    hcell = header[1]
    if norm(hcell) != "value":
        hm = re.match(r"^(.*?):\s*\((.*?)\)\s*$", hcell)
        kw_txt = hm.group(1) if hm else hcell
        header_kid = kw_to_id.get(norm(kw_txt))
        if header_kid is None:
            raise ValueError(
                f"คำ '{kw_txt.strip()}' ในหัวตารางไม่อยู่ใน keywords.csv "
                "(เพิ่มคำก่อน หรือเช็คตัวสะกด)"
            )
        if kid is not None and kid != header_kid:
            raise ValueError(
                f"คำในหัวตาราง '{kw_txt.strip()}' ({header_kid}) "
                f"ไม่ตรงกับ ID จากชื่อไฟล์ ({kid})"
            )
        kid = header_kid

        if hm:
            place = hm.group(2)
            header_geo = PLACE_TO_GEO.get(norm(place))
            if header_geo is None:
                raise ValueError(
                    f"ไม่รู้จักพื้นที่ '{place}' (รองรับ: ประเทศไทย + 5 จังหวัดอีสาน)"
                )
            if geo is not None and geo != header_geo:
                raise ValueError(
                    f"พื้นที่ในหัวตาราง '{place}' ({header_geo}) "
                    f"ไม่ตรงกับ GEO จากชื่อไฟล์ ({geo})"
                )
            geo = header_geo
    if kid is None or geo is None:
        raise ValueError("ระบุคำ/พื้นที่ไม่ได้จากทั้งชื่อไฟล์และหัวตาราง")
    if kid not in ids:
        raise ValueError(f"ID {kid} ไม่อยู่ใน keywords.csv")
    if geo not in RAW_GEOS:
        raise ValueError(f"GEO {geo} ไม่รองรับ (ใช้ได้: {sorted(RAW_GEOS)})")

    # อ่านข้อมูล รวมเป็นรายเดือน (เฉลี่ยถ้าเป็นรายสัปดาห์/รายวัน)
    bucket = {}
    for row in csv.reader(lines[header_i + 1:]):
        if len(row) < 2:
            continue
        month, val = parse_month(row[0]), parse_value(row[1])
        if month is None or val is None:
            continue
        bucket.setdefault(month, []).append(val)
    if not bucket:
        raise ValueError("ไม่มีแถวข้อมูลที่อ่านได้")

    this_month = (today or date.today()).strftime("%Y-%m")
    months = sorted(m for m in bucket if m < this_month)
    if geo != "TH":
        months = [m for m in months if m >= PROVINCE_MIN_MONTH]
    if not months:
        raise ValueError("ไม่มีเดือนที่ใช้ได้ (เดือนยังไม่จบ หรือจังหวัดก่อน 2014 ทั้งหมด)")
    points = [(m, round(sum(bucket[m]) / len(bucket[m]), 1)) for m in months]
    return kid, geo, points


def _move_to(path, dest_dir):
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / path.name
    i = 1
    while dest.exists():
        dest = dest_dir / f"{path.stem}_dup{i}{path.suffix}"
        i += 1
    shutil.move(str(path), str(dest))


def _write_series_file(path, points):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Month", "Value"])
        writer.writerows(points)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(INCOMING), help="โฟลเดอร์ไฟล์ขาเข้า (default: incoming/)")
    ap.add_argument("--dry-run", action="store_true", help="ตรวจอย่างเดียว ไม่เขียน/ไม่ย้ายไฟล์")
    ap.add_argument(
        "--since",
        help="เลิกใช้งานแล้ว: ห้ามตัด long-horizon archive ก่อน ingest",
    )
    args = ap.parse_args(argv)
    if args.since:
        ap.error(
            "--since ถูกปิดใช้งาน เพราะจะตัด canonical long-horizon archive แล้วทับซีรีส์เดิม "
            "ให้ export ใหม่ทั้งช่วง 2004-01-01 ถึงวันนี้ แล้ว ingest โดยไม่ใส่ --since"
        )

    indir = Path(args.dir)
    files = sorted(p for p in indir.glob("*.csv"))
    manifest_files = sorted(p for p in indir.glob("no_data_manifest__*.json"))
    if not files and not manifest_files:
        print(f"ไม่มีไฟล์ .csv หรือ no_data_manifest__*.json ใน {indir}")
        return 0

    run_date = date.today()
    kw_to_id, ids = load_keyword_map()
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8")) if CATALOG_PATH.exists() else {"series": {}}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        sys.exit(f"อ่าน data/catalog.json ไม่ได้ จึงหยุดก่อนเขียนข้อมูล: {exc}")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("series"), dict):
        sys.exit("data/catalog.json ต้องเป็น object ที่มี series object; หยุดก่อนเขียนข้อมูล")
    csv_plans, manifest_plans, bad = [], [], []

    for path in files:
        try:
            kid, geo, points = parse_file(path, kw_to_id, ids, today=run_date)
            validate_canonical_coverage(geo, points, today=run_date)
        except ValueError as e:
            bad.append((path, str(e)))
            print(f"REVIEW  {path.name}: {e}")
            continue
        print(f"OK      {path.name} -> {kid} @ {geo}: {len(points)} เดือน ({points[0][0]} ถึง {points[-1][0]})")
        metadata = {
            "status": "available",
            "keyword": ids[kid],
            "timeframe": f"{CANONICAL_START_DATE} {run_date}",
            "months": len(points),
            "first": points[0][0],
            "last": points[-1][0],
            "fetched_on": str(run_date),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "note": f"ingest จาก {path.name}",
        }
        csv_plans.append((path, f"{kid}__{geo}", points, metadata))

    for path in manifest_files:
        try:
            entries = parse_no_data_manifest(
                path, ids, catalog, series_dir=SERIES_DIR, today=run_date
            )
        except ValueError as e:
            bad.append((path, str(e)))
            print(f"REVIEW  {path.name}: {e}")
            continue
        print(f"OK      {path.name} -> ยืนยัน no_data {len(entries)} เซลล์")
        manifest_plans.append((path, entries))

    sources_by_key = {}
    for path, key, _points, _metadata in csv_plans:
        sources_by_key.setdefault(key, []).append(path)
    for path, entries in manifest_plans:
        for key, _metadata in entries:
            sources_by_key.setdefault(key, []).append(path)
    for key, sources in sorted(sources_by_key.items()):
        unique_sources = list(dict.fromkeys(sources))
        if len(sources) > 1:
            names = ", ".join(path.name for path in unique_sources)
            reason = f"ปลายทางซ้ำ {key} จาก {names}"
            print(f"REVIEW  {reason}")
            for path in unique_sources:
                if not any(existing == path for existing, _why in bad):
                    bad.append((path, reason))

    total = len(files) + len(manifest_files)
    if bad:
        if not args.dry_run:
            for path, _why in bad:
                if path.exists():
                    _move_to(path, indir / "review")
        print(
            f"\nหยุดก่อนเขียนคลัง: อ่านได้ {total - len({path for path, _why in bad})}/{total} ไฟล์ | "
            f"ต้อง review {len({path for path, _why in bad})} ไฟล์"
        )
        return 1

    if args.dry_run:
        print(f"\n(dry-run) อ่านได้ {total}/{total} ไฟล์")
        return 0

    if csv_plans or manifest_plans:
        SERIES_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".ingest-", dir=SERIES_DIR.parent) as tempdir:
            staged_dir = Path(tempdir)
            for _path, key, points, _metadata in csv_plans:
                _write_series_file(staged_dir / f"{key}.csv", points)
            for _path, key, _points, metadata in csv_plans:
                os.replace(staged_dir / f"{key}.csv", SERIES_DIR / f"{key}.csv")
                catalog["series"][key] = metadata
        for _path, entries in manifest_plans:
            for key, metadata in entries:
                catalog["series"][key] = metadata
        catalog["updated_at"] = datetime.now().isoformat(timespec="seconds")
        CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=CATALOG_PATH.parent, delete=False
        ) as handle:
            json.dump(catalog, handle, ensure_ascii=False, indent=1)
            staged_catalog = Path(handle.name)
        os.replace(staged_catalog, CATALOG_PATH)

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build_site_data
        build_site_data.build()

        # Keep the source files retryable until every canonical generated file
        # has been rebuilt successfully. A failed build returns nonzero and the
        # next run can safely re-ingest the same full-window exports.
        for path, *_detail in csv_plans:
            _move_to(path, indir / "processed")
        for path, _entries in manifest_plans:
            _move_to(path, indir / "processed")

    print(
        f"\nสรุป: CSV เข้าคลัง {len(csv_plans)} ไฟล์ | "
        f"no_data manifest {len(manifest_plans)} ไฟล์ | ต้อง review 0 ไฟล์"
    )
    if csv_plans or manifest_plans:
        print("ก่อนเผยแพร่ต้องรัน audit --strict --require-latest และ stage เฉพาะไฟล์ข้อมูลที่ระบบสร้าง")
    return 0


if __name__ == "__main__":
    sys.exit(main())
