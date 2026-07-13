#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google Trends Toolkit - ตัวเก็บ/อัพเดทข้อมูล

เก็บข้อมูล Google Trends ของคำค้นใน keywords.csv แล้วบันทึกเป็นรายเดือนลง data/series/
จากนั้น rebuild data.js ให้หน้าแสดงผลอัตโนมัติ

ตัวอย่างการใช้:
  python collector/collect.py --plan --all                 # ดูงานที่จะทำ ไม่ยิง API
  python collector/collect.py --all                        # อัพเดททุกคำ ทุกพื้นที่
  python collector/collect.py --ids FP014,FU014            # เฉพาะบางคำ
  python collector/collect.py --group FP,FU                # เฉพาะบางกลุ่ม (prefix ของ ID)
  python collector/collect.py --geo TH                     # เฉพาะระดับประเทศ
  python collector/collect.py --all --sleep 20             # ช้ากว่า default ได้ แต่ห้ามเร็วกว่า

พฤติกรรมสำคัญ:
- ข้อมูลถูก resample เป็นรายเดือนเสมอ และแทนที่ไฟล์ซีรีส์เดิมทั้งไฟล์
  (ค่า Google Trends เป็น index 0-100 เทียบภายในช่วงเวลาที่ดึง จึงต้องดึงทั้งช่วงใหม่ทุกครั้ง
   ห้ามต่อท่อนข้อมูลคนละช่วงเข้าไฟล์เดียว)
- เดือนล่าสุดที่ยังไม่จบ (isPartial) จะถูกตัดทิ้ง
- โดน rate limit (429) จะรอแบบทวีคูณและลองใหม่ ถ้าโดนติดกันเกิน MAX_CONSECUTIVE_429 จะหยุดทั้งรอบ
  รันซ้ำคำสั่งเดิมได้เลย ซีรีส์ที่เก็บสำเร็จวันนี้แล้วจะถูกข้าม (ใส่ --force ถ้าจะเก็บซ้ำ)
"""

import argparse
import csv
import json
import random
import sys
import time
from datetime import date, datetime
from pathlib import Path

if __package__:
    from .ingest import validate_canonical_coverage
else:
    from ingest import validate_canonical_coverage

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_CSV = ROOT / "keywords.csv"
DATA_DIR = ROOT / "data"
SERIES_DIR = DATA_DIR / "series"
CATALOG_PATH = DATA_DIR / "catalog.json"

GEOS = {
    "TH": "ประเทศไทย",
    "TH-30": "นครราชสีมา",
    "TH-31": "บุรีรัมย์",
    "TH-34": "อุบลราชธานี",
    "TH-40": "ขอนแก่น",
    "TH-41": "อุดรธานี",
}

PROVINCE_MIN_MONTH = "2014-01"  # จังหวัด/ภาคใช้ได้หลัง geo break ของ Google (ดู README)
BASE_SLEEP = 15          # วินาที พักระหว่าง request ปกติ
JITTER = 5               # สุ่มบวกเพิ่ม 0..JITTER วินาที
BACKOFF_START = 60       # วินาที เมื่อโดน 429 ครั้งแรก
BACKOFF_MAX = 600
MAX_CONSECUTIVE_429 = 4  # โดนติดกันเท่านี้ = หยุดทั้งรอบ กัน IP โดนแบน
CANONICAL_START = "2004-01-01"


def load_keywords():
    with open(KEYWORDS_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_catalog():
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {"series": {}, "updated_at": None}


def save_catalog(catalog):
    catalog["updated_at"] = datetime.now().isoformat(timespec="seconds")
    CATALOG_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def resolve_jobs(args, keywords):
    """คืนรายการงาน (keyword_row, geo) ตาม scope ที่เลือก"""
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

    return [(r, g) for r in rows for g in geos]


def series_key(keyword_id, geo):
    return f"{keyword_id}__{geo}"


def validate_collection_policy(start, end, sleep_seconds, canonical_end=None):
    """Reject settings that could create a short-window or over-fast release."""
    canonical_end = canonical_end or str(date.today())
    if start != CANONICAL_START or end != canonical_end:
        raise ValueError(
            "นโยบายข้อมูลหลักกำหนดให้ดึงทั้งช่วงเท่านั้น: "
            f"--start {CANONICAL_START} --end {canonical_end} "
            "(ห้ามใช้ช่วงสั้น เพราะ Google Trends จะ rescale แล้วทำให้ซีรีส์เทียบกับ archive เดิมไม่ได้)"
        )
    if sleep_seconds < BASE_SLEEP:
        raise ValueError(
            f"--sleep ต่ำกว่า {BASE_SLEEP} วินาทีไม่ได้ "
            "เพื่อป้องกัน rate limit/IP block; ใช้ค่า default หรือสูงกว่า"
        )


def fetch_series(pytrends, keyword, geo, timeframe):
    """ดึง 1 ซีรีส์ คืน list ของ (YYYY-MM, value) รายเดือน"""
    pytrends.build_payload([keyword], timeframe=timeframe, geo=geo)
    df = pytrends.interest_over_time()
    if df is None or df.empty:
        return []
    if "isPartial" in df.columns:
        df = df[~df["isPartial"].astype(bool)]
        df = df.drop(columns=["isPartial"])
    # resample เป็นรายเดือนเสมอ (ช่วงสั้น Google ส่งรายสัปดาห์/รายวันมา)
    monthly = df[keyword].resample("MS").mean().round(1)
    points = [(idx.strftime("%Y-%m"), float(v)) for idx, v in monthly.items() if v == v]
    if geo != "TH":
        points = [p for p in points if p[0] >= PROVINCE_MIN_MONTH]
    return points


def fetch_with_empty_confirmation(pytrends, keyword, geo, timeframe, sleep_seconds):
    """Require two paced empty responses before classifying a cell as no-data."""
    points = fetch_series(pytrends, keyword, geo, timeframe)
    if points:
        return points, 1
    time.sleep(sleep_seconds + random.uniform(0, JITTER))
    return fetch_series(pytrends, keyword, geo, timeframe), 2


def write_series(keyword_id, geo, points):
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    path = SERIES_DIR / f"{series_key(keyword_id, geo)}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Month", "Value"])
        w.writerows(points)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = ap.add_argument_group("scope")
    scope.add_argument("--all", action="store_true", help="ทุกคำใน keywords.csv")
    scope.add_argument("--ids", help="รายคำ เช่น FP014,FU014")
    scope.add_argument("--group", help="รายกลุ่มตาม prefix ของ ID เช่น FP หรือ FP,FU")
    ap.add_argument("--geo", help=f"จำกัดพื้นที่ (คั่นด้วย ,) จาก {list(GEOS)} ไม่ใส่ = ทุกพื้นที่")
    ap.add_argument("--start", default=CANONICAL_START, help=f"วันเริ่ม (ต้องเป็น {CANONICAL_START} ตามนโยบายข้อมูลหลัก)")
    ap.add_argument("--end", default=str(date.today()), help="วันจบ (default วันนี้)")
    ap.add_argument("--plan", action="store_true", help="แสดงงานที่จะทำแล้วจบ ไม่ยิง API")
    ap.add_argument("--force", action="store_true", help="เก็บซ้ำแม้ซีรีส์นั้นสำเร็จไปแล้ววันนี้")
    ap.add_argument("--sleep", type=int, default=BASE_SLEEP, help=f"วินาทีพักระหว่าง request (default {BASE_SLEEP})")
    args = ap.parse_args()

    try:
        validate_collection_policy(args.start, args.end, args.sleep)
    except ValueError as e:
        ap.error(str(e))

    keywords = load_keywords()
    jobs = resolve_jobs(args, keywords)
    catalog = load_catalog()
    timeframe = f"{args.start} {args.end}"
    run_date = date.today()
    today = str(run_date)

    todo = []
    for row, geo in jobs:
        meta = catalog["series"].get(series_key(row["Keyword_ID"], geo))
        if (
            not args.force
            and meta
            and meta.get("fetched_on") == today
            and meta.get("timeframe") == timeframe
        ):
            continue  # เก็บสำเร็จวันนี้แล้ว ข้าม (resume)
        todo.append((row, geo))

    print(f"งานทั้งหมด {len(jobs)} ซีรีส์ | ต้องเก็บรอบนี้ {len(todo)} | timeframe: {timeframe}")
    if args.plan:
        for row, geo in todo[:200]:
            print(f"  {row['Keyword_ID']:>8}  {geo:>6}  {row['Keyword_TH']}")
        if len(todo) > 200:
            print(f"  ... และอีก {len(todo) - 200} ซีรีส์")
        est = len(todo) * (args.sleep + JITTER / 2) / 60
        print(f"เวลาโดยประมาณ ~{est:.0f} นาที (ยังไม่รวมเวลารอ rate limit)")
        return

    if not todo:
        print("ไม่มีอะไรต้องเก็บ (ทุกซีรีส์สำเร็จแล้ววันนี้) ใส่ --force ถ้าต้องการเก็บซ้ำ")
        return

    try:
        from pytrends.request import TrendReq
        from pytrends.exceptions import TooManyRequestsError, ResponseError
    except ImportError:
        sys.exit("ยังไม่ได้ติดตั้ง dependencies: รัน  pip install -r requirements.txt")

    # ปิด retry ภายในของ pytrends (ชน bug urllib3) แล้วคุม retry เองข้างล่าง
    pytrends = TrendReq(hl="th-TH", tz=420, timeout=(10, 30), retries=0)

    ok, no_data, failed, consecutive_429 = 0, 0, [], 0
    backoff = BACKOFF_START
    abort_run = False
    for i, (row, geo) in enumerate(todo, 1):
        kid, kw = row["Keyword_ID"], row["Keyword_TH"]
        label = f"[{i}/{len(todo)}] {kid} ({kw}) @ {geo}"
        if date.today() != run_date:
            print(f"{label} -> วันที่เปลี่ยนระหว่างรัน หยุดเพื่อไม่ให้ metadata/timeframe คนละวัน")
            failed.append((kid, geo, "collection_date_changed"))
            break
        while True:
            try:
                points, observations = fetch_with_empty_confirmation(
                    pytrends, kw, geo, timeframe, args.sleep
                )
                fetched_at = datetime.now()
                if fetched_at.date() != run_date:
                    print(f"{label} -> วันที่เปลี่ยนระหว่าง request หยุดและไม่บันทึกผลของงานนี้")
                    failed.append((kid, geo, "collection_date_changed"))
                    abort_run = True
                    break
                consecutive_429 = 0
                backoff = BACKOFF_START
                if points:
                    try:
                        validate_canonical_coverage(geo, points, today=run_date)
                    except ValueError as exc:
                        print(f"{label} -> ช่วงข้อมูลที่ตอบกลับไม่ปลอดภัย เก็บไฟล์เดิมไว้: {exc}")
                        failed.append((kid, geo, f"unsafe_response: {exc}"))
                        break
                    write_series(kid, geo, points)
                    catalog["series"][series_key(kid, geo)] = {
                        "status": "available",
                        "keyword": kw,
                        "timeframe": timeframe,
                        "months": len(points),
                        "first": points[0][0],
                        "last": points[-1][0],
                        "fetched_on": today,
                        "fetched_at": fetched_at.isoformat(timespec="seconds"),
                    }
                    save_catalog(catalog)
                    print(f"{label} -> {len(points)} เดือน ({points[0][0]} ถึง {points[-1][0]})")
                    ok += 1
                else:
                    existing_path = SERIES_DIR / f"{series_key(kid, geo)}.csv"
                    if existing_path.exists():
                        print(f"{label} -> ไม่มีข้อมูลผิดปกติ แต่มีซีรีส์เดิมอยู่ (เก็บไฟล์เดิมและหยุด release)")
                        failed.append((kid, geo, "unexpected_no_data_for_existing_series"))
                    else:
                        catalog["series"][series_key(kid, geo)] = {
                            "status": "no_data",
                            "keyword": kw,
                            "timeframe": timeframe,
                            "months": 0,
                            "first": None,
                            "last": None,
                            "fetched_on": today,
                            "fetched_at": fetched_at.isoformat(timespec="seconds"),
                            "note": (
                                "Google Trends returned no observations in "
                                f"{observations} paced canonical-window requests"
                            ),
                        }
                        save_catalog(catalog)
                        print(f"{label} -> ยืนยันไม่มีข้อมูลทั้ง canonical window (บันทึก status=no_data)")
                        no_data += 1
                break
            except TooManyRequestsError:
                consecutive_429 += 1
                if consecutive_429 >= MAX_CONSECUTIVE_429:
                    print(f"\nโดน rate limit ติดกัน {consecutive_429} ครั้ง หยุดรอบนี้เพื่อไม่ให้ IP โดนแบน")
                    print("พักอย่างน้อย 1 ชั่วโมงแล้วรันคำสั่งเดิมซ้ำ จะเก็บต่อจากที่ค้างเอง")
                    _finish(catalog, ok, no_data, failed)
                    sys.exit(2)
                print(f"{label} -> โดน 429 รอ {backoff} วินาที (ครั้งที่ {consecutive_429})")
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
            except ResponseError as e:
                print(f"{label} -> Google ตอบผิดปกติ: {e}")
                failed.append((kid, geo, str(e)))
                break
            except Exception as e:  # กันรอบทั้งหมดล่มเพราะคำเดียว
                print(f"{label} -> ผิดพลาด: {e}")
                failed.append((kid, geo, str(e)))
                break
        if abort_run:
            break
        time.sleep(args.sleep + random.uniform(0, JITTER))

    exit_code = _finish(catalog, ok, no_data, failed)
    if exit_code:
        sys.exit(exit_code)


def _finish(catalog, ok, no_data, failed):
    print(f"\nสรุป: สำเร็จ {ok} ซีรีส์ | ยืนยันไม่มีข้อมูล {no_data} | ล้มเหลว {len(failed)}")
    if failed:
        for kid, geo, why in failed:
            print(f"  FAIL {kid} @ {geo}: {why}")
    if not ok and not no_data:
        # ไม่มีข้อมูลใหม่ = ไม่แตะไฟล์ใดๆ กัน commit ที่มีแต่ timestamp เปลี่ยน
        print("ไม่มีข้อมูลใหม่ ไม่แตะ catalog/data.js")
        return 1 if failed else 0
    save_catalog(catalog)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_site_data
    build_site_data.build()
    print("อัพเดท data.js แล้ว ต้องผ่าน audit --strict --require-latest ก่อนเผยแพร่")
    if failed:
        print("รอบนี้มีงานล้มเหลว จึงคืน exit code 1 เพื่อห้าม workflow commit ชุดข้อมูลบางส่วน")
        return 1
    return 0


if __name__ == "__main__":
    main()
