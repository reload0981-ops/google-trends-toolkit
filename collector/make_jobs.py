#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google Trends Toolkit - สร้างคิวงานให้ Chrome extension (การเก็บชุดใหญ่)

อ่าน keywords.csv แล้วเขียน extension/data/jobs.json ให้ extension ไล่โหลด CSV
จากหน้า Google Trends ใน Chrome จริง (ผ่านง่ายกว่า pytrends มาก) ไฟล์ที่ได้ถูกตั้งชื่อ
<ID>__<GEO>.csv พร้อมให้ collector/ingest.py กินทันที

ตัวอย่าง:
  python collector/make_jobs.py --all                    # 50 คำ x 6 พื้นที่ = 300 jobs
  python collector/make_jobs.py --ids FP014,FU014
  python collector/make_jobs.py --group FP --geo TH

นโยบายช่วงเวลา (ข้อมูลหลักของโปรเจค = long horizon): **โหลดยาวสุดเสมอ**
default เริ่ม 2004-01-01 (จุดเริ่มข้อมูล Google Trends) ถึงวันนี้ ได้รายเดือนแท้
ระดับจังหวัดที่ก่อน 2014 เชื่อถือไม่ได้ (Google ปรับระบบ geo) จะถูกตัดทิ้ง
อัตโนมัติตอน ingest ไม่ต้องทำอะไรเพิ่ม
"""

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_CSV = ROOT / "keywords.csv"
OUT = ROOT / "extension" / "data" / "jobs.json"

GEOS = {
    "TH": "ประเทศไทย",
    "TH-30": "นครราชสีมา",
    "TH-31": "บุรีรัมย์",
    "TH-34": "อุบลราชธานี",
    "TH-40": "ขอนแก่น",
    "TH-41": "อุดรธานี",
}
CANONICAL_START = "2004-01-01"


def validate_collection_window(start, end, canonical_end=None):
    """Reject queues that would overwrite the archive with a short window."""
    canonical_end = canonical_end or str(date.today())
    if start != CANONICAL_START or end != canonical_end:
        raise ValueError(
            "นโยบายข้อมูลหลักกำหนดให้สร้าง jobs ทั้งช่วงเท่านั้น: "
            f"--start {CANONICAL_START} --end {canonical_end} "
            "(ห้ามใช้ช่วงสั้น เพราะ Google Trends จะ rescale แล้วทับ archive เดิม)"
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = ap.add_argument_group("scope")
    scope.add_argument("--all", action="store_true", help="ทุกคำใน keywords.csv")
    scope.add_argument("--ids", help="รายคำ เช่น FP014,FU014")
    scope.add_argument("--group", help="รายกลุ่มตาม prefix ของ ID เช่น FP,FU")
    ap.add_argument("--geo", help=f"จำกัดพื้นที่ (คั่นด้วย ,) จาก {list(GEOS)}")
    ap.add_argument("--start", default=CANONICAL_START, help=f"วันเริ่ม (ต้องเป็น {CANONICAL_START} ตามนโยบายข้อมูลหลัก)")
    ap.add_argument("--end", default=str(date.today()), help="วันจบ (default วันนี้)")
    ap.add_argument("--out", default=str(OUT), help="ที่เขียน jobs.json")
    args = ap.parse_args()

    try:
        validate_collection_window(args.start, args.end)
    except ValueError as e:
        ap.error(str(e))

    with open(KEYWORDS_CSV, encoding="utf-8-sig") as f:
        keywords = list(csv.DictReader(f))

    if args.ids:
        wanted = {x.strip().upper() for x in args.ids.split(",") if x.strip()}
        rows = [r for r in keywords if r["Keyword_ID"].upper() in wanted]
        missing = wanted - {r["Keyword_ID"].upper() for r in rows}
        if missing:
            sys.exit(f"ไม่พบ ID ใน keywords.csv: {sorted(missing)}")
    elif args.group:
        prefixes = tuple(x.strip().upper() for x in args.group.split(",") if x.strip())
        rows = [r for r in keywords if r["Keyword_ID"].upper().startswith(prefixes)]
        if not rows:
            sys.exit(f"ไม่มีคำในกลุ่ม {prefixes}")
    elif args.all:
        rows = keywords
    else:
        sys.exit("ต้องระบุ scope: --all หรือ --ids หรือ --group (ดู --help)")

    if args.geo:
        geos = [g.strip().upper() for g in args.geo.split(",") if g.strip()]
        unknown = [g for g in geos if g not in GEOS]
        if unknown:
            sys.exit(f"geo ไม่รู้จัก: {unknown} (ใช้ได้: {list(GEOS)})")
    else:
        geos = list(GEOS)

    timeframe = f"{args.start} {args.end}"
    jobs, seq = [], 1
    for geo in geos:
        for r in rows:
            jobs.append({
                "job_id": f"j{seq:04d}",
                "keyword_id": r["Keyword_ID"],
                "keyword": r["Keyword_TH"],
                "segment": r.get("Segment", ""),
                "factor": r.get("Factor", ""),
                "geo_code": geo,
                "geo_name": GEOS[geo],
                "timeframe": timeframe,
                "filename": f"{r['Keyword_ID']}__{geo}.csv",
                "kind": "timeseries",
            })
            seq += 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

    # Only the canonical jobs.json belongs in Controller's bundled dropdown.
    # Custom outputs (for example jobs_smoke.json) are import-only and must not
    # relabel or replace the canonical jobs_index.json next to them.
    if out.resolve() == OUT.resolve():
        scope_label = args.ids or args.group or "all"
        index = [{
            "file": "data/jobs.json",
            "label": f"{scope_label} ({len(jobs)} jobs)",
            "mode": "timeseries",
            "description": f"สร้างเมื่อ {date.today()} | {len(geos)} พื้นที่ x {len(rows)} คำ | {timeframe}",
        }]
        (out.parent / "jobs_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"เขียน {out} : {len(jobs)} jobs ({len(geos)} พื้นที่ x {len(rows)} คำ) timeframe: {timeframe}")
    print("ขั้นถัดไป: เปิด extension Controller > กด 'Import jobs.json' >")
    print(f"  เลือก {out.resolve()} > กด 'Start'")
    print("(ตั้ง download folder ของ Chrome เป็น incoming/ และปิด Ask where to save ก่อน)")


if __name__ == "__main__":
    main()
