#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""รวมข้อมูลใน data/series/*.csv เป็น data.js สำหรับหน้าแสดงผล (index.html)

รันเองก็ได้: python collector/build_site_data.py
(ปกติ collect.py เรียกให้อัตโนมัติหลังเก็บข้อมูลเสร็จ)

เหตุผลที่ใช้ data.js แทน fetch JSON: เปิด index.html ตรงจากไฟล์ในเครื่อง (file://)
ก็ยังทำงานได้ ไม่ติด CORS และขึ้น GitHub Pages ได้เหมือนกัน
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_CSV = ROOT / "keywords.csv"
SERIES_DIR = ROOT / "data" / "series"
CATALOG_PATH = ROOT / "data" / "catalog.json"
OUT_PATH = ROOT / "data.js"

GEOS = {
    "TH": "ประเทศไทย",
    "ISAN": "อีสาน (รวม 5 จังหวัด)",
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
    for g in PROVINCES:
        s = geo_map.get(g)
        if not s or not s["values"]:
            continue
        mx = max(s["values"])
        if mx <= 0:
            continue
        provs.append({m: v / mx * 100 for m, v in zip(s["months"], s["values"])})
    if not provs:
        return None
    months = sorted(set().union(*[set(p) for p in provs]))
    mean = [sum(p[m] for p in provs if m in p) / sum(1 for p in provs if m in p) for m in months]
    mx = max(mean)
    if mx <= 0:
        return None
    return {"months": months, "values": [round(v / mx * 100, 1) for v in mean]}


def build():
    with open(KEYWORDS_CSV, encoding="utf-8-sig") as f:
        keywords = list(csv.DictReader(f))

    series = {}
    for path in sorted(SERIES_DIR.glob("*.csv")):
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
    if CATALOG_PATH.exists():
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

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
    }

    OUT_PATH.write_text(
        "window.GT_DATA = " + json.dumps(payload, ensure_ascii=False) + ";",
        encoding="utf-8",
    )
    n_series = sum(len(v) for v in series.values())
    print(f"เขียน {OUT_PATH.name}: {len(payload['keywords'])} คำ, {n_series} ซีรีส์ (รวมอีสาน derived {n_isan} ตัว)")


if __name__ == "__main__":
    build()
