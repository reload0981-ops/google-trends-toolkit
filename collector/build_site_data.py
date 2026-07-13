#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""รวมข้อมูลใน data/series/*.csv เป็น data.js สำหรับหน้าแสดงผล (index.html)

รันเองก็ได้: python collector/build_site_data.py
(ปกติ collect.py เรียกให้อัตโนมัติหลังเก็บข้อมูลเสร็จ)

เหตุผลที่ใช้ data.js แทน fetch JSON: เปิด index.html ตรงจากไฟล์ในเครื่อง (file://)
ก็ยังทำงานได้ ไม่ติด CORS และขึ้น GitHub Pages ได้เหมือนกัน
"""

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__:
    from .audit import audit_dataset
else:
    from audit import audit_dataset

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_CSV = ROOT / "keywords.csv"
SERIES_DIR = ROOT / "data" / "series"
CATALOG_PATH = ROOT / "data" / "catalog.json"
OUT_PATH = ROOT / "data.js"

GEOS = {
    "TH": "ประเทศไทย",
    "ISAN": "อีสาน (คอมโพสิต)",
    "TH-30": "นครราชสีมา",
    "TH-31": "บุรีรัมย์",
    "TH-34": "อุบลราชธานี",
    "TH-40": "ขอนแก่น",
    "TH-41": "อุดรธานี",
}
PROVINCES = ["TH-30", "TH-31", "TH-34", "TH-40", "TH-41"]


def isan_aggregate(geo_map):
    """ซีรีส์รวมภาคอีสาน (derived, ไม่ได้มาจาก Google โดยตรง)

    สูตรเดียวกับ REG_ISAN5 ของโปรเจคเดิม (build_interactive_compare_v25_long_horizon):
    rebase แต่ละจังหวัดให้ max ของตัวเอง = 100 -> เฉลี่ยข้ามจังหวัดที่มีข้อมูลรายเดือน
    -> rebase ผลรวมให้ max = 100 เพื่อให้ทุกจังหวัดมีน้ำหนักเท่ากันแม้ scale ดิบต่างกัน
    """
    provs = []
    support_geos = []
    for g in PROVINCES:
        s = geo_map.get(g)
        if not s or not s["values"]:
            continue
        mx = max(s["values"])
        if mx <= 0:
            continue
        provs.append({m: v / mx * 100 for m, v in zip(s["months"], s["values"])})
        support_geos.append(g)
    if not provs:
        return None
    months = sorted(set().union(*[set(p) for p in provs]))
    mean = [sum(p[m] for p in provs if m in p) / sum(1 for p in provs if m in p) for m in months]
    mx = max(mean)
    if mx <= 0:
        return None
    return {
        "months": months,
        "values": [round(v / mx * 100, 1) for v in mean],
        "support_n": len(support_geos),
        "support_geos": support_geos,
        "support_total": len(PROVINCES),
    }


def build_payload(root=ROOT):
    """Build the JSON-safe site payload without writing to disk."""
    root = Path(root)
    keywords_csv = root / "keywords.csv"
    series_dir = root / "data" / "series"
    catalog_path = root / "data" / "catalog.json"

    with open(keywords_csv, encoding="utf-8-sig") as f:
        keywords = list(csv.DictReader(f))

    series = {}
    for path in sorted(series_dir.glob("*.csv")):
        kid, _, geo = path.stem.partition("__")
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        series.setdefault(kid, {})[geo] = {
            "months": [r["Month"] for r in rows],
            "values": [float(r["Value"]) for r in rows],
        }

    n_isan = 0
    for kid, geo_map in series.items():
        agg = isan_aggregate(geo_map)
        if agg:
            geo_map["ISAN"] = agg
            n_isan += 1

    catalog = {}
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    # ห้ามใส่ timestamp ปัจจุบันในไฟล์นี้: data.js ต้อง deterministic ต่อข้อมูล
    # ไม่งั้นทุกการ rebuild จะสร้าง diff ปลอมให้ git ทั้งที่ข้อมูลไม่เปลี่ยน
    payload = {
        "updated_at": catalog.get("updated_at"),
        "geos": GEOS,
        "keywords": [
            {
                "id": r["Keyword_ID"],
                "keyword": r["Keyword_TH"],
                "tier": r["Tier"],
                "segment": r["Segment"],
                "factor": r["Factor"],
                "family": r.get("Family_Name_TH", ""),
            }
            for r in keywords
        ],
        "series": series,
        "health": audit_dataset(root),
    }

    return payload


def render_data_js(payload):
    """Serialize a payload in the file:// compatible format used by the UI."""
    return "window.GT_DATA = " + json.dumps(payload, ensure_ascii=False) + ";"


def build(root=ROOT, check=False):
    """Write data.js, or verify deterministically that it is already current."""
    root = Path(root)
    out_path = root / "data.js"
    payload = build_payload(root)
    rendered = render_data_js(payload)

    n_series = sum(len(v) for v in payload["series"].values())
    n_isan = sum("ISAN" in v for v in payload["series"].values())
    summary = (
        f"{len(payload['keywords'])} คำ, {n_series} ซีรีส์ "
        f"(รวมอีสาน derived {n_isan} ตัว)"
    )

    if check:
        actual = out_path.read_text(encoding="utf-8") if out_path.exists() else None
        if actual != rendered:
            print(f"STALE {out_path.name}: generated output does not match source data ({summary})")
            return False
        print(f"OK {out_path.name}: deterministic output matches source data ({summary})")
        return True

    out_path.write_text(rendered, encoding="utf-8")
    print(f"เขียน {out_path.name}: {summary}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="ตรวจว่า data.js ตรงกับข้อมูลต้นทาง โดยไม่เขียนไฟล์",
    )
    args = parser.parse_args()
    result = build(check=args.check)
    if args.check and not result:
        sys.exit(1)
