# Portable SA Pipeline

ส่วนนี้แปลงคลังข้อมูลรายเดือนใน `data/series/` เป็นชุดวิเคราะห์ที่ Agent เครื่องอื่นสร้างซ้ำและตรวจสอบได้ โดยไม่แก้ `data/`, `data.js` หรือเส้นทางเก็บข้อมูลดิบ

## ติดตั้งบน Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-analysis-windows.ps1
```

สคริปต์จะสร้าง `.venv`, ติดตั้ง `requirements-analysis.txt` และเตรียม X-13ARIMA-SEATS v1.1 Build 62 จาก U.S. Census Bureau ไว้ใน `.tools/` หลังตรวจ SHA-256 แล้ว ทั้ง `.venv/` และ `.tools/` เป็นของเฉพาะเครื่องและไม่ขึ้น Git

## คำสั่ง

```powershell
# สร้างผลวิเคราะห์ใหม่
.\.venv\Scripts\python.exe -X utf8 -m analysis.build

# สร้างใน staging แล้วเทียบผลแบบ byte-for-byte
.\.venv\Scripts\python.exe -X utf8 -m analysis.build --check

# ตรวจ schema, hashes, source digest และ coverage โดยไม่เรียก X-13
.\.venv\Scripts\python.exe -X utf8 -m analysis.build --audit
```

ถ้าใช้ Python environment อื่น ให้ใช้ `python -X utf8 -m analysis.build` และระบุ binary ด้วย `--x13-path <path>` ได้

## Contract การคำนวณ

- อ่าน case จาก `keywords.csv` โดยตรง: 22 T1 keywords และ 8 T2 families รวม 30 cases
- `TH` ใช้ช่วง `2011-01` เป็นต้นไป
- `REG_ISAN5` ใช้ข้อมูลจริงของ `TH-30`, `TH-31`, `TH-34`, `TH-40`, `TH-41` ตั้งแต่ `2014-01` เป็นต้นไป ห้ามเติมศูนย์ปลอมให้ปี 2011–2013
- T1 ภาค: rebase max100 แยกรายจังหวัด (A) → เฉลี่ยครบ 5 จังหวัด → rebase ภาค (C)
- T2 ภาค: rebase ราย member×จังหวัด (A) → เฉลี่ย members ในจังหวัด → rebase family×จังหวัด (B) → เฉลี่ยครบ 5 จังหวัด → rebase ภาค (C)
- ระดับประเทศใช้ลำดับเดียวกันกับ geography เดียว; T2 ยังทำ member rebase และ family rebase
- X-13 ใช้ additive mode, `log=False`, `outlier=False`; ค่า 0 เปลี่ยนเป็น `0.001` เฉพาะสำเนาที่ส่งเข้า X-13
- หาก X-13 ประมวลผล series หนึ่งไม่ได้ ให้ใช้ robust STL และบันทึก `STL_FALLBACK` พร้อมเหตุผล แต่ถ้าไม่พบ binary ตั้งแต่ต้น build ต้อง fail
- หลัง SA: floor ค่าติดลบเป็น 0 → rebase max100 → centered MA3 (`window=3`, `min_periods=1`)
- series ดิบที่เป็นศูนย์ล้วนต้องคงเป็นศูนย์และติดสถานะ `NO_SIGNAL`; ห้ามทำ epsilon ก่อน rebase เพราะจะกลายเป็นค่าคงที่ 100
- support รายเดือนหรือ geography ขาดต้อง fail; pipeline ไม่เดา ไม่ pad และไม่เฉลี่ยเฉพาะส่วนที่เหลือ
- คำนวณ full precision และปัดเป็น 10 ตำแหน่งเฉพาะตอนเขียน CSV

## ผลลัพธ์

ไฟล์ canonical อยู่ใน `derived/sa_pipeline_v3/`:

| ไฟล์ | เนื้อหา |
|---|---|
| `series.csv` | long-format monthly series: input rebased, SA, floor0, post-SA rebase และ centered MA3 |
| `method_log.csv` | วิธีที่ใช้จริง, fallback reason, signal/support และ post-SA status ต่อ case×scope |
| `rebase_audit.csv` | ค่า pre-max และจำนวน contributor ในขั้น A/B/C/D |
| `x13_diagnostics.csv` | M1–M11, Q และ seasonality tests ที่อ่านได้จาก X-13 output |
| `manifest.json` | method contract, windows, package/X-13 versions, source digest, row counts และ hashes |

`REG_ISAN5` ในชุดนี้ไม่ใช่ `ISAN` ของ dashboard: dashboard เดิมเป็น raw keyword-level composite และมี client-side rebase/trailing MA3 จึงยังไม่ควรนำ analytical series ชุดนี้ไปเสียบตรง ๆ เพราะจะเกิดการแปลงซ้ำ

## หลังอัปเดตข้อมูลดิบ

เมื่อ ingest/audit/build ของ raw ผ่านแล้ว ให้รัน analytical build เป็นขั้นแยก จากนั้นรัน `--check` และ `--audit` ก่อน stage `derived/sa_pipeline_v3/` การแยกขั้นนี้รักษา raw collector ให้ใช้ Python มาตรฐานได้ และทำให้ความล้มเหลวของ X-13 ไม่กระทบคลังดิบ
