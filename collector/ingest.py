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

กติกา:
  - ค่า "<1" ถูกแปลงเป็น 0
  - เดือนปัจจุบันที่ยังไม่จบถูกตัดทิ้ง
  - เขียนแทนซีรีส์เดิมทั้งไฟล์ (ห้ามต่อท่อนข้อมูลคนละช่วง scale จะเพี้ยน)
  - จับคู่คำ/พื้นที่ไม่ได้ = ย้ายไฟล์ไป incoming/review/ ไม่เดาเด็ดขาด
"""

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCOMING = ROOT / "incoming"
SERIES_DIR = ROOT / "data" / "series"
CATALOG_PATH = ROOT / "data" / "catalog.json"
KEYWORDS_CSV = ROOT / "keywords.csv"

TIME_WORDS = {"time", "month", "week", "day", "เดือน", "สัปดาห์", "วัน"}
PLACE_TO_GEO = {
    "ประเทศไทย": "TH", "thailand": "TH", "th": "TH",
    "นครราชสีมา": "TH-30", "nakhon ratchasima": "TH-30", "th-30": "TH-30",
    "บุรีรัมย์": "TH-31", "buri ram": "TH-31", "buriram": "TH-31", "th-31": "TH-31",
    "อุบลราชธานี": "TH-34", "ubon ratchathani": "TH-34", "th-34": "TH-34",
    "ขอนแก่น": "TH-40", "khon kaen": "TH-40", "th-40": "TH-40",
    "อุดรธานี": "TH-41", "udon thani": "TH-41", "th-41": "TH-41",
}


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


def parse_file(path, kw_to_id, ids):
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

    # ระบุ keyword + geo
    kid = geo = None
    name = path.stem
    m = re.match(r"^([A-Za-z]{2}\d{3})__(TH(?:-\d{2})?)$", name)
    if m:
        kid, geo = m.group(1).upper(), m.group(2).upper()
    else:
        m = re.match(r"^manual_([A-Za-z]{2}\d{3})$", name)
        if m:
            kid, geo = m.group(1).upper(), "TH"

    # จาก header cell: "<คำ>: (<พื้นที่>)"
    hcell = header[1]
    hm = re.match(r"^(.*?):\s*\((.*?)\)\s*$", hcell)
    if hm:
        kw_txt, place = hm.group(1), hm.group(2)
        if geo is None:
            geo = PLACE_TO_GEO.get(norm(place))
            if geo is None:
                raise ValueError(f"ไม่รู้จักพื้นที่ '{place}' (รองรับ: ประเทศไทย + 5 จังหวัดอีสาน)")
        if kid is None:
            kid = kw_to_id.get(norm(kw_txt))
            if kid is None:
                raise ValueError(f"คำ '{kw_txt.strip()}' ไม่อยู่ใน keywords.csv (เพิ่มคำก่อน หรือเช็คตัวสะกด)")
    if kid is None or geo is None:
        raise ValueError("ระบุคำ/พื้นที่ไม่ได้จากทั้งชื่อไฟล์และหัวตาราง")
    if kid not in ids:
        raise ValueError(f"ID {kid} ไม่อยู่ใน keywords.csv")

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

    this_month = date.today().strftime("%Y-%m")
    months = sorted(m for m in bucket if m < this_month)
    if not months:
        raise ValueError("มีแต่ข้อมูลเดือนปัจจุบันที่ยังไม่จบ")
    points = [(m, round(sum(bucket[m]) / len(bucket[m]), 1)) for m in months]
    return kid, geo, points


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(INCOMING), help="โฟลเดอร์ไฟล์ขาเข้า (default: incoming/)")
    ap.add_argument("--dry-run", action="store_true", help="ตรวจอย่างเดียว ไม่เขียน/ไม่ย้ายไฟล์")
    ap.add_argument("--since", help="ตัดข้อมูลก่อนเดือนนี้ทิ้ง เช่น 2022-01 (ใช้คู่กับ jobs ของ extension ที่ดึงตั้งแต่ 2021 เพื่อให้ได้รายเดือน)")
    args = ap.parse_args()
    if args.since and not re.match(r"^\d{4}-\d{2}$", args.since):
        sys.exit("--since ต้องเป็นรูปแบบ YYYY-MM เช่น 2022-01")

    indir = Path(args.dir)
    files = sorted(p for p in indir.glob("*.csv"))
    if not files:
        print(f"ไม่มีไฟล์ .csv ใน {indir}")
        return

    kw_to_id, ids = load_keyword_map()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8")) if CATALOG_PATH.exists() else {"series": {}}
    ok, bad = [], []

    for path in files:
        try:
            kid, geo, points = parse_file(path, kw_to_id, ids)
            if args.since:
                points = [p for p in points if p[0] >= args.since]
                if not points:
                    raise ValueError(f"ไม่มีข้อมูลตั้งแต่ {args.since}")
        except ValueError as e:
            bad.append((path, str(e)))
            print(f"REVIEW  {path.name}: {e}")
            continue
        print(f"OK      {path.name} -> {kid} @ {geo}: {len(points)} เดือน ({points[0][0]} ถึง {points[-1][0]})")
        if args.dry_run:
            continue
        SERIES_DIR.mkdir(parents=True, exist_ok=True)
        with open(SERIES_DIR / f"{kid}__{geo}.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Month", "Value"])
            w.writerows(points)
        catalog["series"][f"{kid}__{geo}"] = {
            "keyword": ids[kid],
            "timeframe": f"{points[0][0]} {points[-1][0]}",
            "months": len(points),
            "first": points[0][0],
            "last": points[-1][0],
            "fetched_on": str(date.today()),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "note": f"ingest จาก {path.name}",
        }
        ok.append((path, kid, geo))

    if args.dry_run:
        print(f"\n(dry-run) อ่านได้ {len(files) - len(bad)}/{len(files)} ไฟล์")
        return

    # ย้ายไฟล์: สำเร็จ -> processed/, มีปัญหา -> review/
    for path, kid, geo in ok:
        dest_dir = indir / "processed"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / path.name
        i = 1
        while dest.exists():
            dest = dest_dir / f"{path.stem}_dup{i}{path.suffix}"
            i += 1
        shutil.move(str(path), str(dest))
    for path, _why in bad:
        dest_dir = indir / "review"
        dest_dir.mkdir(exist_ok=True)
        if path.exists():
            shutil.move(str(path), str(dest_dir / path.name))

    if ok:
        catalog["updated_at"] = datetime.now().isoformat(timespec="seconds")
        CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build_site_data
        build_site_data.build()

    print(f"\nสรุป: เข้าคลัง {len(ok)} ไฟล์ | ต้อง review {len(bad)} ไฟล์ (ดูใน incoming/review/)")
    if ok:
        print("ถ้าจะเผยแพร่: git add -A && git commit -m \"update data\" && git push")


if __name__ == "__main__":
    main()
