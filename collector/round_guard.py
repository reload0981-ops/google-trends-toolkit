#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""กันไม่ให้สร้างคิวใหม่ทับรอบเก็บที่ยังไม่จบ

หน้าต่างคุมงานถือคิวได้ทีละอัน ถ้าสร้างคิวใหม่แล้ว Import ระหว่างที่รอบเดิมยังวิ่ง
งานที่เก็บมาแล้วจะหายทั้งหมด

ข้อความอยู่ที่นี่ ไม่ใช่ใน toolkit.ps1 เพราะ Windows PowerShell 5.1 อ่านสคริปต์ที่ไม่มี BOM
เป็น ANSI ภาษาไทยในไฟล์นั้นจะกลายเป็นตัวยึกยือ
"""

from __future__ import annotations

import sys
from pathlib import Path

# ต่างจาก error ทั่วไป ตัวเรียกใช้รหัสนี้แยกได้ว่าเป็นการหยุดตามตั้งใจ ไม่ใช่ของพัง
# ใช้ร่วมกันทุกที่ที่ "หยุดเองแล้วอธิบายเป็นไทยไว้แล้ว" ไม่ใช่แค่รอบเก็บที่ค้าง
# toolkit.ps1 กับไฟล์ .cmd เห็นรหัสนี้แล้วจะจบเงียบ ไม่ขึ้นให้ส่งหา maintainer
STOPPED_ON_PURPOSE = 9
ROUND_IN_FLIGHT_EXIT = STOPPED_ON_PURPOSE


def pending_downloads(root: Path) -> list[Path]:
    incoming = Path(root) / "incoming"
    if not incoming.is_dir():
        return []
    return sorted(incoming.glob("*.csv"))


def assert_no_round_in_flight(root: Path, *, allow: bool = False) -> None:
    """หยุดพร้อมบอกวิธีแก้ ถ้ายังมีไฟล์ค้างที่ยังไม่ได้เอาเข้าคลัง"""

    if allow:
        return
    pending = pending_downloads(root)
    if not pending:
        return

    print()
    print("=" * 60)
    print("  รอบเก็บข้อมูลยังไม่จบ จึงยังสร้างคิวใหม่ไม่ได้")
    print("=" * 60)
    print()
    print(f"  พบไฟล์ {len(pending)} ไฟล์ที่ยังไม่ได้เอาเข้าคลัง")
    print("  แปลว่ารอบเก็บก่อนหน้ายังค้างอยู่")
    print()
    print("  ถ้าสร้างคิวใหม่ตอนนี้ คิวที่กำลังวิ่งในหน้าต่างคุมงานจะถูกแทนที่")
    print("  แล้วงานที่เก็บมาแล้วทั้งหมดจะหายไป")
    print()
    print("  สิ่งที่ต้องทำ")
    print("    1. กลับไปที่หน้าต่างสีดำที่ค้างอยู่")
    print("    2. รอจนในหน้าต่างคุมงาน FAILED เหลือ 0")
    print("    3. พิมพ์ FINISH แล้ว Enter")
    print("    4. เสร็จแล้วค่อยกลับมาทำอันนี้ใหม่")
    print()
    print("  (ไม่ใช่ข้อผิดพลาด ระบบหยุดให้เพื่อไม่ให้งานที่เก็บมาหาย)")
    print()
    sys.exit(ROUND_IN_FLIGHT_EXIT)
