#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""เพิ่ม ปิดงาน หรือถอนคำค้น โดยไม่ต้องเปิด keywords.csv เอง

คนตัดสินแค่ 2 อย่าง: คำนี้อยู่กลุ่มไหนทิศไหน (prefix) และถ้าออกมาเป็น T2 จะเข้าครอบครัวไหน
ที่เหลือสคริปต์เติมให้จากตาราง prefix และจากผลด่านคัดกรอง

รหัสจบงาน: 0 คือสำเร็จ, 9 คือหยุดเองโดยตั้งใจและอธิบายเป็นไทยไว้แล้ว (คำไม่ผ่านด่าน
คำซ้ำ ต้องเข้าครอบครัว ผู้ใช้ยกเลิก) ตัวเลขอื่นคือของพังจริงที่ต้องให้คนแก้
คนคุมงานจึงแยกออกได้ว่าจอแดงเป็นคำตอบของด่าน ไม่ใช่ระบบเสีย

Examples:
  python -X utf8 collector/add_keyword.py "หางาน ต่างประเทศ" --prefix FP
  python -X utf8 collector/add_keyword.py --finalize FP690
  python -X utf8 collector/add_keyword.py --finalize FP690 --family FPF03
  python -X utf8 collector/add_keyword.py --remove FP690
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.check_keyword import check_keyword  # noqa: E402
from collector.round_guard import STOPPED_ON_PURPOSE, assert_no_round_in_flight  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_CSV = ROOT / "keywords.csv"
TRIED_CSV = ROOT / "reference" / "keywords_tried.csv"
BACKUP_DIR = ROOT / ".tools" / "keywords-backup"
INCOMING = ROOT / "incoming"

# Verified against all 1,183 rows of keywords_tried.csv: the first letter is the
# labour-market segment and the second is the direction, with no exceptions.
PREFIXES = {
    "FP": ("Formal", "Pull"),
    "FU": ("Formal", "Push"),
    "NP": ("Informal-New", "Pull"),
    "NU": ("Informal-New", "Push"),
    "TP": ("Informal-Traditional", "Pull"),
    "TU": ("Informal-Traditional", "Push"),
}
DEFAULT_STATUS = "active_current_official"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _read_rows() -> tuple[list[dict], list[str]]:
    with KEYWORDS_CSV.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _line_terminator() -> str:
    """Keep whatever the file already uses.

    Hardcoding one would rewrite every line on a checkout that uses the other,
    turning a one-row change into a whole-file diff.
    """

    return "\r\n" if b"\r\n" in KEYWORDS_CSV.read_bytes() else "\n"


def _write_rows(rows: list[dict], columns: list[str]) -> Path:
    """Back up first, then rewrite. keywords.csv is the authority for what may
    enter the archive, so every write keeps a restorable copy."""

    terminator = _line_terminator()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Two edits inside the same second must not overwrite each other's backup,
    # otherwise the only copy of the pre-edit state is the one already replaced.
    backup = BACKUP_DIR / f"keywords-{stamp}.csv"
    attempt = 1
    while backup.exists():
        attempt += 1
        backup = BACKUP_DIR / f"keywords-{stamp}-{attempt}.csv"
    shutil.copy2(KEYWORDS_CSV, backup)
    with KEYWORDS_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator=terminator)
        writer.writeheader()
        writer.writerows(rows)
    return backup


def _used_numbers(prefix: str) -> set[int]:
    """Numbers already spoken for, in the live set and in everything ever tried."""

    used: set[int] = set()
    for path, column in ((KEYWORDS_CSV, "Keyword_ID"), (TRIED_CSV, "keyword_id")):
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                value = (row.get(column) or "").strip().upper()
                match = re.fullmatch(rf"{prefix}(\d+)", value)
                if match:
                    used.add(int(match.group(1)))
    return used


def next_free_id(prefix: str) -> str:
    used = _used_numbers(prefix)
    number = max(used) + 1 if used else 1
    return f"{prefix}{number:03d}"


def prior_attempt(keyword: str) -> dict | None:
    if not TRIED_CSV.exists():
        return None
    target = _norm(keyword)
    with TRIED_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if _norm(row.get("canonical_keyword", "")) == target:
                return row
    return None


GROUP_MENU = [
    ("FP", "งานในระบบ - ดึงเข้าตลาดแรงงาน", "สมัครงาน, หางาน, สอบครูผู้ช่วย"),
    ("FU", "งานในระบบ - ผลักออกจากงาน", "ลาออกจากงาน, ลงทะเบียนว่างงาน"),
    ("NP", "นอกระบบแบบใหม่ - ดึงเข้า", "สมัครแกร็บ, สมัครไรเดอร์"),
    ("NU", "นอกระบบแบบใหม่ - ผลักออก", ""),
    ("TP", "นอกระบบดั้งเดิม - ดึงเข้า", "รถเข็นขายของ, ตลาดนัดขายของ"),
    ("TU", "นอกระบบดั้งเดิม - ผลักออก", "ปิดร้าน, เซ้งร้าน, ขายของไม่ดี"),
]


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def interactive_add(id_file: Path | None = None) -> int:
    """Ask the two questions a person actually has to answer, in Thai.

    The prompts live here rather than in toolkit.ps1 because Windows PowerShell
    5.1 reads a script without a BOM as ANSI, which would turn every Thai line
    into mojibake.
    """

    # ตรวจก่อนถามอะไรทั้งนั้น จะได้ไม่ให้คนพิมพ์คำเสร็จแล้วค่อยบอกว่าทำไม่ได้
    assert_no_round_in_flight(ROOT)

    print("เพิ่มคำค้นใหม่")
    print()
    keyword = _ask("คำที่อยากเพิ่ม (พิมพ์ให้ตรงกับที่คนค้นจริง): ")
    if not keyword:
        print("ยกเลิก")
        return STOPPED_ON_PURPOSE

    print()
    print("คำนี้อยู่กลุ่มไหน")
    for number, (_, label, examples) in enumerate(GROUP_MENU, start=1):
        suffix = f"   เช่น {examples}" if examples else ""
        print(f"  {number}) {label}{suffix}")
    print()
    choice = _ask("เลือก 1-6: ")
    if not choice.isdigit() or not 1 <= int(choice) <= len(GROUP_MENU):
        print("ต้องเลือกเลข 1 ถึง 6")
        return STOPPED_ON_PURPOSE

    prefix = GROUP_MENU[int(choice) - 1][0]
    print()
    return add(keyword, prefix, id_file=id_file)


def add(keyword: str, prefix: str, *, force: bool = False, id_file: Path | None = None) -> int:
    prefix = prefix.strip().upper()
    if prefix not in PREFIXES:
        print(f"prefix ต้องเป็นหนึ่งใน {sorted(PREFIXES)}")
        return 2

    rows, columns = _read_rows()
    for row in rows:
        if _norm(row["Keyword_TH"]) == _norm(keyword):
            print(f"มีคำนี้อยู่แล้วในชุด: {row['Keyword_ID']} {row['Keyword_TH']}")
            return STOPPED_ON_PURPOSE

    tried = prior_attempt(keyword)
    if tried and not force:
        print(f"เคยลองคำนี้แล้ว: {tried.get('keyword_id')}")
        print(f"  ไปได้ไกลสุด: {tried.get('best_stage')}")
        print("ถ้ายืนยันว่าจะลองใหม่ ให้ใส่ --force")
        return STOPPED_ON_PURPOSE

    segment, factor = PREFIXES[prefix]
    keyword_id = next_free_id(prefix)
    new_row = {column: "" for column in columns}
    new_row.update({
        "Keyword_ID": keyword_id,
        "Keyword_TH": keyword.strip(),
        "Tier": "T1",
        "Segment": segment,
        "Factor": factor,
        "Case_ID": keyword_id,
        "Case_Type": "keyword",
        "Current_Status": DEFAULT_STATUS,
    })
    rows.append(new_row)
    backup = _write_rows(rows, columns)

    print(f"เพิ่มแล้ว: {keyword_id}  {keyword.strip()}  [{segment} / {factor}]")
    print(f"สำรองไฟล์เดิมไว้ที่ {backup.relative_to(ROOT)}")
    # Stable machine-readable line so toolkit.ps1 can pick the id up without
    # parsing Thai output.
    print(f"NEW_KEYWORD_ID={keyword_id}")
    if id_file is not None:
        id_file.write_text(keyword_id, encoding="utf-8")
    return 0


def finalize(keyword_id: str, family_id: str | None) -> int:
    keyword_id = keyword_id.strip().upper()
    rows, columns = _read_rows()
    target = next((row for row in rows if row["Keyword_ID"].strip().upper() == keyword_id), None)
    if target is None:
        print(f"ไม่พบ {keyword_id} ใน keywords.csv")
        return 1

    verdict = check_keyword(keyword_id, ROOT, from_incoming=True)
    if not verdict["collected"]:
        verdict = check_keyword(keyword_id, ROOT)
    if not verdict["collected"]:
        print(f"{keyword_id}: {verdict['reason']}")
        return STOPPED_ON_PURPOSE

    suggested = verdict["suggested_tier"]
    print(f"ด่าน 1 National : {'ผ่าน' if verdict['national_pass'] else 'ไม่ผ่าน'} "
          f"(เดือนศูนย์ {verdict['national_zero_months']}/{verdict['national_window_months']} "
          f"เพดาน {verdict['national_max_allowed']})")
    print(f"ด่าน 2 Regional : มีสัญญาณ {verdict['regional_support']}/{verdict['regional_support_total']} จังหวัด")
    print(f"ด่าน 3 Tier     : {suggested}")
    print()

    if suggested == "T1":
        target.update(Tier="T1", Case_ID=keyword_id, Case_Type="keyword",
                      Family_ID="", Family_Name_TH="")
        backup = _write_rows(rows, columns)
        print(f"ตั้งเป็นคำเดี่ยว T1 แล้ว สำรองไว้ที่ {backup.relative_to(ROOT)}")
        print("ขั้นถัดไป: .\\scripts\\toolkit.ps1 monthly-finish")
        return 0

    if suggested == "T2":
        families = _families(rows, verdict_segment=target["Segment"], verdict_factor=target["Factor"])
        if not family_id:
            if not families:
                print("คำนี้ต้องเข้าครอบครัว แต่ยังไม่มีครอบครัวที่กลุ่มและทิศทางตรงกัน")
                print("ครอบครัวใหม่ต้องมีอย่างน้อย 2 คำ จึงต้องเพิ่มคำที่สองที่ความหมายใกล้กันพร้อมกัน")
                return STOPPED_ON_PURPOSE
            print("คำนี้มีสัญญาณไม่ครบ 5 จังหวัด จึงต้องเข้าครอบครัว")
            for number, (fid, name, count) in enumerate(families, start=1):
                print(f"  {number}) {fid}  {name}  ({count} คำ)")
            if not sys.stdin.isatty():
                print()
                print("เลือกแล้วรันซ้ำด้วย --family <ID>")
                return STOPPED_ON_PURPOSE
            print()
            choice = _ask(f"เลือก 1-{len(families)}: ")
            if not choice.isdigit() or not 1 <= int(choice) <= len(families):
                print("ยกเลิก ยังไม่ได้เปลี่ยนอะไร")
                return STOPPED_ON_PURPOSE
            family_id = families[int(choice) - 1][0]
        family_id = family_id.strip().upper()
        members = [row for row in rows if row["Family_ID"].strip().upper() == family_id]
        if not members:
            print(f"ไม่พบครอบครัว {family_id}")
            return 1
        head = members[0]
        if head["Segment"] != target["Segment"] or head["Factor"] != target["Factor"]:
            print(f"เข้าครอบครัวนี้ไม่ได้: {family_id} เป็น {head['Segment']}/{head['Factor']} "
                  f"แต่คำนี้เป็น {target['Segment']}/{target['Factor']}")
            return STOPPED_ON_PURPOSE
        target.update(Tier="T2", Case_ID=family_id, Case_Type="family_member",
                      Family_ID=family_id, Family_Name_TH=head["Family_Name_TH"])
        backup = _write_rows(rows, columns)
        print(f"ใส่เข้าครอบครัว {family_id} {head['Family_Name_TH']} แล้ว "
              f"สำรองไว้ที่ {backup.relative_to(ROOT)}")
        print("ขั้นถัดไป: .\\scripts\\toolkit.ps1 monthly-finish")
        return 0

    print(f"คำนี้ไม่ควรเข้าชุด ({verdict['reason']})")
    print(f"ถอนออกด้วย: python -X utf8 collector/add_keyword.py --remove {keyword_id}")
    print()
    print("(ไม่ใช่ข้อผิดพลาด ด่านคัดกรองตัดสินว่าคำนี้สัญญาณบางเกินไป)")
    return STOPPED_ON_PURPOSE


def _families(rows: list[dict], *, verdict_segment: str, verdict_factor: str) -> list[tuple[str, str, int]]:
    seen: dict[str, tuple[str, int]] = {}
    for row in rows:
        fid = row["Family_ID"].strip().upper()
        if not fid or row["Segment"] != verdict_segment or row["Factor"] != verdict_factor:
            continue
        name, count = seen.get(fid, (row["Family_Name_TH"], 0))
        seen[fid] = (name, count + 1)
    return sorted((fid, name, count) for fid, (name, count) in seen.items())


def remove(keyword_id: str) -> int:
    keyword_id = keyword_id.strip().upper()
    rows, columns = _read_rows()
    kept = [row for row in rows if row["Keyword_ID"].strip().upper() != keyword_id]
    if len(kept) == len(rows):
        print(f"ไม่พบ {keyword_id} ใน keywords.csv")
        return 1

    archived = sorted((ROOT / "data" / "series").glob(f"{keyword_id}__*.csv"))
    if archived:
        print(f"หยุดก่อน: {keyword_id} เข้าคลังไปแล้ว {len(archived)} ไฟล์")
        print("การถอนคำที่อยู่ในคลังต้องลบไฟล์ใน data/series และรายการใน data/catalog.json ด้วย")
        print("ดู MAINTAINER-GUIDE.md หัวข้อ 'ถอนคำออก' หรือให้ AI agent ทำให้")
        return STOPPED_ON_PURPOSE

    backup = _write_rows(kept, columns)
    print(f"ลบแถว {keyword_id} แล้ว สำรองไว้ที่ {backup.relative_to(ROOT)}")
    pending = sorted(INCOMING.glob(f"{keyword_id}__*.csv")) if INCOMING.is_dir() else []
    if pending:
        print(f"ยังมีไฟล์ค้างใน incoming/ อีก {len(pending)} ไฟล์ ให้ลบทิ้งด้วย:")
        for path in pending:
            print(f"  {path.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("keyword", nargs="?", help="คำค้นภาษาไทยที่จะเพิ่ม")
    parser.add_argument("--prefix", help=f"กลุ่มและทิศทาง: {sorted(PREFIXES)}")
    parser.add_argument("--force", action="store_true", help="เพิ่มทั้งที่เคยลองแล้ว")
    parser.add_argument("--finalize", metavar="ID", help="อ่านผลด่านคัดกรองแล้วตั้ง Tier ให้ถูก")
    parser.add_argument("--family", metavar="ID", help="ครอบครัวที่จะเข้า ใช้คู่กับ --finalize")
    parser.add_argument("--remove", metavar="ID", help="ถอนคำที่ยังไม่เข้าคลัง")
    parser.add_argument("--interactive", action="store_true", help="ถามทีละข้อเป็นภาษาไทย")
    parser.add_argument("--id-file", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.remove:
        return remove(args.remove)
    if args.finalize:
        return finalize(args.finalize, args.family)
    id_file = Path(args.id_file) if args.id_file else None
    if args.interactive:
        return interactive_add(id_file)
    if args.keyword and args.prefix:
        return add(args.keyword, args.prefix, force=args.force, id_file=id_file)
    parser.error('ต้องระบุ "คำ" พร้อม --prefix หรือใช้ --interactive / --finalize / --remove')


if __name__ == "__main__":
    sys.exit(main())
