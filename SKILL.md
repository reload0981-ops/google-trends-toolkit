---
name: google-trends-toolkit
description: Operate the Google Trends Toolkit - bulk-collect via the Chrome extension queue, ingest CSV downloads, run the pytrends collector for light updates, add/check keywords, verify data health, and publish to GitHub Pages. Trigger when the user asks to update data, run the scraper extension, ingest downloaded CSVs, add a keyword, check the dashboard, or troubleshoot collection (429/rate limit/CAPTCHA) in this repository.
---

# Google Trends Toolkit - คู่มือปฏิบัติงานสำหรับ AI

คุณคือผู้ดูแลชุดข้อมูล Google Trends ของคำค้นตลาดแรงงานภาคอีสาน หน้าที่: อัพเดทข้อมูล ตรวจสุขภาพข้อมูล และเผยแพร่ อย่างปลอดภัยตามกติกาในไฟล์นี้ ถ้าคุณรันคำสั่งเองไม่ได้ ให้บอกคำสั่งทีละขั้นแล้วขอผลลัพธ์กลับมาตรวจ

## เส้นทาง production มีเส้นเดียว

`Google Trends Explore ใหม่ → Chrome extension ปกติ → incoming/ → ingest/audit → GitHub Pages`

| ขั้น | เจ้าของงาน |
|---|---|
| สร้างคิว | Agent รัน `collector/make_jobs.py` |
| ดาวน์โหลด CSV รายเดือน | extension v0.7.1 ใน Chrome profile ที่ลงชื่อเข้าใช้ Google แล้ว |
| ขั้นที่ผู้ใช้ต้องทำ | ครั้งแรกติดตั้ง extension/ตั้ง Downloads; แต่ละรอบ Import `jobs.json` + Start; แก้ CAPTCHA เมื่อพบ |
| ตรวจ/เข้าคลัง/publish | Agent รัน ingest dry-run/จริง, gates, stage allowlist และ push |

เมื่อผู้ใช้สั่ง “อัพเดทข้อมูล” ให้ใช้เส้นทางนี้ทันที ห้ามแจกแจงหลายเครื่องมือให้ผู้ใช้เลือก Python browser runner, pytrends และ GitHub Actions เป็นทางทดลอง/สำรองสำหรับนักพัฒนาเท่านั้น และ **ห้ามใช้ publish** จนมีหลักฐาน monthly canonical smoke ของทางนั้น

บนเครื่องใหม่ ให้ Agent รัน `powershell -ExecutionPolicy Bypass -File .\bootstrap-windows.ps1` ก่อน ข้อมูลถาวรอยู่ใน GitHub แต่ extension, download path, GitHub auth, `incoming/`, jobs และ queue state เป็นของเฉพาะเครื่อง

## โครง repo

| ที่อยู่ | คืออะไร |
|---|---|
| `keywords.csv` | คำค้นที่ใช้งาน 50 คำ (ID, คำ, Tier, Segment, Factor) แก้ไฟล์นี้เมื่อเพิ่ม/ถอดคำ |
| `reference/keywords_tried.csv` | คำ 1,192 คำที่เคยลองทั้งหมด คอลัมน์ `best_stage` บอกว่าไปไกลสุดขั้นไหน เช็คที่นี่ก่อนเพิ่มคำใหม่เสมอ |
| `extension/` | ตัวเก็บ production (MV3, มีระบบคิว/retry/CAPTCHA/Import jobs) ติดตั้งครั้งเดียว ดู `extension/README.md` |
| `extension/data/jobs.json` + `jobs_index.json` | คิวงานของ extension สร้างโดย `make_jobs.py` (generated, ไม่ commit) |
| `collector/make_jobs.py` | สร้างคิวงานจาก keywords.csv (`--all/--ids/--group/--geo/--start/--end`) |
| `collector/browser_runner.py` | เปิด Playwright Chromium + extension, start/resume/status สำหรับ AI; ตรวจ download ด้วย ingest guard ก่อนเข้า incoming |
| `collector/ingest.py` | ตรวจ CSV/no-data manifest จาก `incoming/` แล้วเข้าคลัง (Python มาตรฐาน) |
| `collector/collect.py` | ดึงเองผ่าน pytrends (ต้อง `pip install -r requirements.txt`) |
| `collector/audit.py` | ตรวจ coverage, โครงสร้าง, signal quality และ freshness; ไม่แก้ข้อมูล |
| `incoming/` | จุดรับ CSV และ `no_data_manifest__*.json` จาก extension; ไฟล์มีปัญหาถูกย้ายเข้า `incoming/review/` |
| `data/series/<ID>__<GEO>.csv` | คลังข้อมูลรายเดือนต่อคำต่อพื้นที่ |
| `data/catalog.json` | บันทึกการเก็บ (เมื่อไหร่ ช่วงไหน) ใช้เป็นกลไก resume |
| `data.js` | ข้อมูลรวมของหน้าเว็บ สร้างอัตโนมัติ ห้ามแก้มือ |
| `index.html` | หน้าแสดงผล (เปิด local ได้ หรือผ่าน GitHub Pages) |
| `.github/workflows/experimental-pytrends.yml` | manual diagnostic สำหรับ pytrends; read-only และไม่ publish |
| `.github/workflows/validate.yml` | ตรวจ tests + audit + deterministic build บน push/PR โดยไม่แก้ข้อมูล |

พื้นที่: `TH` ประเทศไทย, `TH-30` นครราชสีมา, `TH-31` บุรีรัมย์, `TH-34` อุบลราชธานี, `TH-40` ขอนแก่น, `TH-41` อุดรธานี
พื้นที่พิเศษ `ISAN` (อีสานคอมโพสิต) = ซีรีส์ derived ใน `build_site_data.py` (rebase จังหวัดที่มีค่าสูงสุด >0 → เฉลี่ยน้ำหนักเท่ากัน → rebase max=100) **เก็บ/ingest ไม่ได้** มันคำนวณใหม่เองทุกครั้งที่ rebuild data.js และแนบ `support_n`, `support_total=5`, `support_geos` ไว้ทุกเส้น จึงต้องรายงานเป็น `N/5`; ห้ามเรียกว่า “รวม 5 จังหวัด” หาก support ไม่ครบ

## กติกาเหล็ก (ห้ามละเมิดไม่ว่าผู้ใช้จะรีบแค่ไหน)

1. **ห้ามต่อท่อนข้อมูลคนละช่วงเวลาเข้าซีรีส์เดียว** ค่า Google Trends เป็น index 0-100 เทียบภายในช่วงที่ดึงครั้งนั้น การอัพเดทที่ถูกต้องคือดึง/โหลดทั้งช่วงใหม่แล้วแทนที่ทั้งเส้น (เครื่องเก็บทุกตัวทำแบบนี้อยู่แล้ว อย่าไปทำมือนอกระบบ)
2. **นโยบาย window: โหลดยาวสุดเสมอ (2004-01-01 ถึงวันนี้)** ข้อมูลหลักคือ long horizon ห้ามอัพเดทด้วยช่วงสั้น และระดับจังหวัดที่ก่อน 2014-01 ถูกตัดอัตโนมัติ (Google ปรับระบบ geo ช่วง 2011-2013 ข้อมูลจังหวัดก่อนหน้าเป็น break ใช้ไม่ได้ ระดับประเทศเริ่ม 2004 ได้ปกติ)
3. **ห้ามแก้ `data.js` และ `data/` ด้วยมือ** ให้ผ่าน ingest/collect เท่านั้น
4. **collector ทุกตัวต้องใช้ canonical window เท่านั้น:** start = `2004-01-01`, end = วันนี้ และ `collect.py --sleep` ต้องไม่น้อยกว่า 15 วินาที; guard จะปฏิเสธค่าอื่นก่อนยิง request ห้ามแก้/หลบ guard
5. **`ingest.py --since` ถูกปิดใช้งานโดยตั้งใจ** และ ingest ต้องผ่าน canonical coverage guard (TH 2004-01/จังหวัด 2014-01 ถึงเดือนสมบูรณ์ล่าสุด, เดือนไม่ขาด, ค่า finite 0–100) ก่อนเขียนทับคลัง
6. **เพิ่มคำใหม่ต้องเช็ค `reference/keywords_tried.csv` ก่อน** ถ้าคำนั้น (หรือรูปสะกดใกล้เคียง) เคยลองแล้วไปตายที่ขั้นไหน ให้บอกผู้ใช้ก่อนเพิ่มซ้ำ
7. **ตัวเลขคือ index ไม่ใช่จำนวนการค้นจริง** ห้ามสรุปเป็นจำนวนคน และห้ามเทียบขนาดข้ามคำตรงๆ ในรายงานใดๆ
8. **รายงานเป็นตัวเลขนับได้เสมอ** (กี่ไฟล์ กี่ซีรีส์ กี่เดือน ช่วงไหน) ผลไม่ตรงคาด = หยุดแล้วบอกผู้ใช้ ห้ามเดินต่อเงียบๆ

## Workflow

### A. Monthly update production

1. `python collector/make_jobs.py --all` (หรือ `--ids FP014` / `--group FP` / `--geo TH`) สร้างคิวงาน
   default timeframe = 2004-01-01 ถึงวันนี้; extension เปิด `trends.google.co.th/explore?date=all` เพื่อรับรายเดือนแท้ (จังหวัดก่อน 2014 ถูกตัดตอน ingest)
2. ให้ผู้ใช้ทำใน Chrome: คลิกไอคอน > Open Controller > **Import jobs.json** > เลือก `extension/data/jobs.json` > **Start**
   (ครั้งแรก: ติดตั้งแบบ Load unpacked + ตั้ง Downloads เป็น `incoming/` + ปิด Ask where to save ดู `extension/README.md`; การอัพเกรดเป็น v0.7.1 ต้อง Reload ครั้งเดียว หลังจากนั้นไม่ต้อง Reload เมื่อคิวเปลี่ยน)
3. ระหว่างรัน: หน้าต่าง Chrome ต้องอยู่หน้าสุด เจอ CAPTCHA = ผู้ใช้แก้ในแท็บที่เด้ง แล้วกด Resume
4. คิวจบ ไฟล์ `<ID>__<GEO>.csv` จะอยู่ใน `incoming/`; คู่ NO_DATA ที่พบติดต่อกันอย่างน้อย 2 ครั้งจะมี `no_data_manifest__YYYY-MM-DD.json` อัตโนมัติ แล้วรัน:
   ```
   python collector/ingest.py --dry-run
   python collector/ingest.py
   ```
5. ตรวจ + เผยแพร่ตามส่วน F

### B. เก็บมือไม่กี่ไฟล์ + ingest

1. ให้ผู้ใช้โหลด CSV จากหน้าเว็บ Google Trends วางใน `incoming/`
   เงื่อนไข: ช่วงเวลา = ยาวสุด 2004-01-01 ถึงปัจจุบัน (นโยบายข้อมูลหลัก) และเลือกพื้นที่ให้ตรง
2. `python collector/ingest.py --dry-run` ดูการจับคู่ แล้วรันจริง
3. ingest รู้จัก: export หน้า classic, export หน้าใหม่ `time_series_<GEO>_*.csv` (`Time,<keyword>`), `<ID>__<GEO>.csv`, `manual_<ID>.csv` และแปลง "<1" เป็น 0 ให้เอง

### B2. ทดลอง Python browser runner (ห้าม publish)

```
pip install -r requirements.txt
python -m playwright install chromium
python collector/make_jobs.py --ids FP014 --geo TH
python -X utf8 collector/browser_runner.py --plan --json
python -X utf8 collector/browser_runner.py --start
python -X utf8 collector/browser_runner.py --status --json
python -X utf8 collector/browser_runner.py --resume
```

runner ใช้ persistent profile ใน `.browser-runner/` และบันทึกไฟล์เข้า `incoming/` เฉพาะเมื่อ parser + canonical guard ผ่าน หลักฐาน 2026-07-15: profile แยกที่ยังไม่ลงชื่อเข้าใช้ Google ถูก Explore ใหม่หยุดที่ auth gate; Chromium และ Chrome ให้ผลเหมือนกัน ส่วน classic/pytrends ได้ `Year` 23 จุด ห้าม fallback และห้ามใช้ runner publish จน authenticated-profile monthly smoke ผ่าน

### C. ทดลอง pytrends (diagnostic เท่านั้น ห้าม publish)

```
pip install -r requirements.txt        (ครั้งแรกครั้งเดียว)
python collector/collect.py --plan --ids FP014,FU014     ดูงานก่อน
python collector/collect.py --ids FP014,FU014            เก็บจริง
```
scope อื่น: `--group FP,FU` / `--all` / `--geo TH` ส่วน `--start` และ `--end` มีไว้แสดง canonical window เท่านั้น ค่าอื่นจะถูก guard ปฏิเสธ ผล full-window ที่ตรวจ 2026-07-15 เป็นรายปี จึงใช้เพื่อวินิจฉัยเท่านั้นและห้าม ingest/publish

### D. GitHub Actions diagnostic (ห้าม publish)

- ไม่มี schedule และไม่มี write permission; สั่งมือจาก Actions > experimental-pytrends-diagnostic เพื่อวินิจฉัยเท่านั้น
- workflow รัน collector, structural audit, build check และ tests แต่ไม่ commit/push ไม่ว่าผลเป็นอย่างไร
- `validate.yml` รัน tests + `audit.py --strict` + build `--check` ทุก push/PR แบบ read-only และไม่ใช้ freshness gate เพราะการตรวจโค้ดต้องแยกจากรอบ refresh
- ข้อจำกัดที่พิสูจน์แล้ว (2026-07-09): Google บล็อก IP ของ GitHub runner โดน 429 ตั้งแต่ request แรก จึงห้ามนับ workflow นี้เป็น release path

### E. เพิ่มคำใหม่

1. เช็ค `reference/keywords_tried.csv` ว่าเคยลองหรือยัง (กติกาเหล็กข้อ 6)
2. เพิ่มแถวใน `keywords.csv`: ตั้ง `Keyword_ID` ตาม pattern กลุ่ม (FP/FU/NP/NU/TP/TU + เลข 3 หลักที่ไม่ซ้ำทั้งใน keywords.csv และ keywords_tried.csv)
3. เก็บข้อมูลเฉพาะ ID นั้นด้วยเส้นทาง A (`make_jobs.py --ids <ID>`) หรือ C
4. ตรวจว่าโผล่ในหน้าเว็บแล้วค่อย push

### F. ตรวจก่อน push (ทำทุกครั้งที่ข้อมูลเปลี่ยน)

1. รัน structural gate: `python -X utf8 collector/audit.py --strict`
2. รัน freshness gate: `python -X utf8 collector/audit.py --strict --require-latest` (ค่าอัตโนมัติ = เดือนที่จบแล้วล่าสุด; ล็อกเดือนได้ เช่น `--require-latest 2026-06`)
3. ตรวจ generated output โดยไม่เขียน: `python -X utf8 collector/build_site_data.py --check`
4. รัน tests: `python -X utf8 -m unittest discover -s tests -v`
5. `git status --short` ต้องมีเฉพาะ `data/series/*.csv`, `data/catalog.json`, `data.js` (และ `keywords.csv` หากตั้งใจแก้คำ) อย่างอื่นโผล่ = หยุดตรวจ
6. เปิด `index.html` ดู health strip และกราฟคำที่เพิ่งอัพเดท เดือนล่าสุดต้องงอกและเส้นไม่กระโดดผิดธรรมชาติ; ตรวจคำเตือน signal tier และ support `N/5` ของ ISAN ด้วย
7. stage เฉพาะ allowlist แล้วตรวจรายชื่อก่อน commit:
   ```
   git add -- data/series data/catalog.json data.js
   git diff --cached --name-only
   git commit -m "update data <รายละเอียดสั้น>"
   git push
   ```
   ถ้าแก้ `keywords.csv` ให้ stage แยกโดยตั้งใจ ห้ามใช้ `git add -A` แล้วรอ Pages rebuild ราว 1-2 นาที

### G. อ่านผล data health ให้ถูก

- `collector/audit.py --json` ให้รายงาน machine-readable ทั้ง coverage, range, catalog, structural errors, freshness และสุขภาพรายซีรีส์
- signal tier ใช้ 64 เดือนล่าสุด: `VERY_GOOD` = 0 เดือนที่ค่าเป็นศูนย์, `ACCEPTABLE` = 1–16 เดือน, `WEAK` = มากกว่า 16 เดือน; all-zero ระบุแยก
- `WEAK`, all-zero หรือ no-data คือข้อจำกัดของสัญญาณค้นหา **ไม่ใช่หลักฐานว่าไม่มีอุปสงค์แรงงาน**
- `--strict` ตรวจ schema/ลำดับเดือน/catalog และรูปหลักฐาน no-data; valid no-data แยกจาก missing/all-zero
- `--require-latest` เป็น complete-release gate: ทุกคู่ต้องมีข้อมูลถึงเดือนกำหนด หรือ confirmed no-data ที่เก็บจาก canonical window หลังเดือนนั้น; missing/invalid/stale ทุกชนิดทำให้ fail
- หน้าเว็บอ่าน health metadata ชุดเดียวกันมาแสดงเดือนล่าสุด coverage tiers และคำเตือนรายเส้น

## เมื่อเจอปัญหา

| อาการ | ทำยังไง |
|---|---|
| Extension: คิวไม่ตรงที่เพิ่ง generate | กด Import jobs.json แล้วเลือก `extension/data/jobs.json` ที่เพิ่งสร้างใหม่; Controller จะ validate วันที่/schema ก่อน reset queue |
| Extension: คิวจบแต่ `incoming/` ว่าง | download folder ของ Chrome ไม่ได้ชี้ `incoming/` เช็ค `chrome://settings/downloads` แล้วกด Reconcile Downloads เพื่อ mark งานที่เสร็จ + ย้ายไฟล์ตามมา |
| Extension: เจอ CAPTCHA | ปกติของงานชุดใหญ่ ผู้ใช้แก้ในแท็บที่เด้งขึ้น แล้วกด Resume ห้ามปิดหน้าต่าง |
| Extension: job FAIL หลายตัว | กด Retry Failed/No Data ก่อน ถ้ายัง FAIL ซ้ำ เปิดดูคำนั้นในหน้า GT เองว่าคำเงียบจริงไหม |
| Python runner: `BROWSER_RUNNER_INVALID_DOWNLOAD` | เปิด `.browser-runner/captured/` ตรวจชนิด export; ถ้า header เป็น `Year` ให้หยุด ห้ามแปลงหรือ ingest เพราะไม่ใช่ canonical monthly series |
| Python runner: `CHART_TIMEOUT`/หน้าให้ลงชื่อเข้าใช้ | Explore ใหม่ต้องใช้ authenticated profile; ใช้ extension v0.7.1 ใน Chrome ปกติ ห้าม fallback กลับหน้า classic เพื่อ publish |
| 429 / TooManyRequests (pytrends) | สคริปต์ backoff เองแล้ว ถ้ามันหยุดทั้งรอบ = พัก 1 ชม. แล้วรันซ้ำ |
| ไฟล์เข้า `incoming/review/` | อ่านเหตุผลที่พิมพ์ไว้ อย่าเดา ถ้าคำไม่อยู่ใน keywords.csv ให้ถามผู้ใช้ก่อนเพิ่ม |
| กราฟเส้นกระโดดผิดปกติหลังอัพเดท | สงสัย scale คนละช่วง ให้ดึงคำนั้นใหม่ทั้งช่วงเต็มแล้วแทนที่ |
| หน้าเว็บไม่อัพเดทหลัง push | เช็ค Pages build ใน repo รอ 2-3 นาที แล้ว hard refresh |
| Actions ล้มเหลว | เปิด log ดู ถ้าเป็น 429 = ปกติของ runner ปล่อยรอบหน้า หรือใช้เส้นทาง A |
| `audit.py --strict` fail | อ่าน `ERROR:` หรือ JSON `structural_errors` แล้วแก้ที่ source/collector; ห้ามแก้ `data/` หรือ `data.js` ด้วยมือ |
| freshness gate fail | ดูรายการ stale/missing/invalid/no-data stale แล้วเก็บใหม่ทั้ง canonical window; extension ต้องได้ manifest จาก no-data ติดต่อกัน 2 ครั้ง ห้าม publish บางส่วน |
