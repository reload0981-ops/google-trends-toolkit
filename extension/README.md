# Google Trends Toolkit Scraper (Chrome extension)

เครื่องมือเก็บชุดใหญ่: ไล่โหลด CSV จากหน้า Google Trends ใน Chrome จริงตามคิวงาน
ผ่านง่ายกว่า pytrends มาก (เป็น browser จริง IP จริงของผู้ใช้) พิสูจน์มาแล้วจากโปรเจคเดิมกว่า 300 jobs

ไฟล์ที่โหลดถูกตั้งชื่อ `<ID>__<GEO>.csv` อัตโนมัติ พร้อมให้ `collector/ingest.py` กินทันที
เมื่อคิวจบ Controller จะส่งออก `no_data_manifest__YYYY-MM-DD.json` อัตโนมัติถ้ามีงาน
`NO_DATA` ที่ตรวจซ้ำอย่างน้อย 2 ครั้ง เพื่อส่งสถานะนี้เข้า data catalog อย่างตรวจสอบย้อนหลังได้

## ติดตั้ง (ครั้งเดียว)

1. ใช้ Chrome profile ที่ลงชื่อเข้าใช้ Google แล้ว (ถ้าสร้าง profile แยกสำหรับงานนี้ ให้ sign in ก่อนเริ่ม)
2. ตั้งที่เก็บดาวน์โหลด (`chrome://settings/downloads`) เป็นโฟลเดอร์ `incoming/` ของ repo นี้
   ใช้ `Resolve-Path .\incoming` ที่ root repo เพื่อดู absolute path และปิด **Ask where to save each file**
3. ไปที่ `chrome://extensions/` เปิด Developer mode
4. กด Load unpacked แล้วเลือกโฟลเดอร์ `extension/` นี้
5. Pin extension ไว้ที่ toolbar

ถ้าอัพเดทจากรุ่นก่อนเป็น v0.7.2 ให้กด Reload หนึ่งครั้ง และอนุญาต host `trends.google.co.th` ถ้า Chrome แสดงคำขอสิทธิ์ ตั้งแต่ v0.7.0 Controller import `jobs.json` ได้ จึงไม่ต้อง Reload extension เมื่อสร้างคิวใหม่ในรอบถัดไป

## รอบการเก็บ

1. สร้างคิวงาน:
   ```powershell
   .\scripts\toolkit.ps1 monthly-prepare                          # ทุกคำทุกพื้นที่ (300 jobs)
   .\scripts\toolkit.ps1 monthly-prepare --ids FP014              # เฉพาะบางคำ
   .\scripts\toolkit.ps1 monthly-prepare --group FP --geo TH
   ```
2. คลิกไอคอน extension > Open Controller
3. กด **Import jobs.json** แล้วเลือก `extension/data/jobs.json` ที่เพิ่งสร้าง
4. กด **Start**
5. ปล่อยให้หน้าต่าง Chrome นั้นอยู่หน้าสุด อย่า minimize ระหว่างรัน
   ถ้าเจอ CAPTCHA: แก้ในแท็บที่เด้งขึ้น แล้วกด Resume
6. เก็บครบแล้ว กลับมาที่ repo:
   ```powershell
   .\scripts\toolkit.ps1 monthly-finish
   ```

   wrapper จะทำ ingest dry-run/จริง, raw freshness, site check, analytical build/byte-check/audit, full tests และแสดง `git status` ครบ คำสั่งนี้ไม่ stage/commit/push; ให้กลับไปตรวจ allowlist และเผยแพร่ตาม `README.md` / `SKILL.md` เท่านั้น ห้าม publish แบบ raw-only และห้ามใช้ `git add -A`

(jobs ดึงยาวสุด 2004-01-01 ถึงปัจจุบันตามนโยบายข้อมูลหลัก long horizon
ระดับจังหวัดที่ก่อน 2014-01 ถูกตัดทิ้งอัตโนมัติตอน ingest เพราะ geo break ของ Google)

## ปุ่มในหน้า Controller

- Start / Pause / Resume / Stop / Skip Current: คุมคิว
- Retry Failed/No Data: ลองใหม่เฉพาะตัวที่พลาด
- Reconcile Downloads: เทียบกับประวัติดาวน์โหลดของ Chrome แล้ว mark งานที่เสร็จแล้ว (ใช้ตอนเปิด controller ใหม่หลังเบราว์เซอร์ปิด)
  รายการเล็กกว่า 200 bytes เป็นเพียง heuristic `NO_DATA` และยังไม่ใช่หลักฐานสำหรับ manifest;
  กด Retry Failed/No Data ให้ Controller สังเกตซ้ำก่อน
- Copy Debug: ก๊อปรายงานสถานะไว้ส่งให้คนช่วยดู

## หมายเหตุ

- Extension นี้พอร์ตมาจากตัวที่ใช้จริงในโปรเจค Isan Labor และเพิ่ม release-safety ใน v0.4.0:
  no-data proof manifest, ชื่อไฟล์แบบ fail-closed, notification icon และตัวตรวจ block ภาษาไทย
- v0.5.0 เพิ่ม fail-closed acknowledgment bridge สำหรับ `collector/browser_runner.py`: extension จะยอมรับ download ชื่อ GUID ของ Playwright เฉพาะเมื่อ Python จับคู่กับ RUNNING job ได้หนึ่งตัวและไฟล์ผ่าน parser + canonical coverage guard ของ `ingest.py` แล้ว; runner เก็บผลไว้ใน `.browser-runner/` เท่านั้นและไม่เขียน production `incoming/`
- v0.6.0 เปลี่ยนไปใช้ `trends.google.co.th/explore?date=all`, รองรับ chart/download selector และชื่อไฟล์ `time_series_<GEO>_*.csv` ของหน้าใหม่ หน้าใหม่นี้ส่ง full-history รายเดือนใน Chrome ปกติ; หน้า classic/pytrends ยังส่ง `Year`
- v0.7.0 เพิ่ม Import `jobs.json` พร้อม validate canonical timeframe/schema ใน Controller เพื่อตัดขั้นตอน Reload extension ออกจากรอบปกติ
- v0.7.1 เปิดหน้าต่าง scraper แบบ maximized, เลือกปุ่มดาวน์โหลดเฉพาะใน time-series widget และ rename เฉพาะไฟล์ `time_series_<GEO>_*.csv` ที่ตรงกับ job เพื่อไม่ให้ CSV ของ Top searches ถูกนับเป็นงานสำเร็จ
- v0.7.2 รับคิว `date=all` ที่สร้างวันก่อนโดย normalize วันสังเกตจริง และเปลี่ยน download-validation exception เป็น ERROR แบบ fail closed
- ข้อจำกัดที่พบ 2026-07-15: profile แยกของ Python runner ติด auth gate ของ Explore ใหม่หากยังไม่ลงชื่อเข้าใช้ Google จึงยังใช้ publish ไม่ได้ ให้รัน extension ใน Chrome profile ปกติที่ลงชื่อเข้าใช้แล้วเป็นเส้นทางหลัก
- `data/jobs.json` และ `data/jobs_index.json` เป็นไฟล์ generate จาก `make_jobs.py` ไม่ commit เข้า git
