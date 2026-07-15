# Google Trends Toolkit Scraper (Chrome extension)

เครื่องมือเก็บชุดใหญ่: ไล่โหลด CSV จากหน้า Google Trends ใน Chrome จริงตามคิวงาน
ผ่านง่ายกว่า pytrends มาก (เป็น browser จริง IP จริงของผู้ใช้) พิสูจน์มาแล้วจากโปรเจคเดิมกว่า 300 jobs

ไฟล์ที่โหลดถูกตั้งชื่อ `<ID>__<GEO>.csv` อัตโนมัติ พร้อมให้ `collector/ingest.py` กินทันที
เมื่อคิวจบ Controller จะส่งออก `no_data_manifest__YYYY-MM-DD.json` อัตโนมัติถ้ามีงาน
`NO_DATA` ที่ตรวจซ้ำอย่างน้อย 2 ครั้ง เพื่อส่งสถานะนี้เข้า data catalog อย่างตรวจสอบย้อนหลังได้

## ติดตั้ง (ครั้งเดียว)

1. เปิด Chrome แนะนำให้สร้าง profile แยกสำหรับงานนี้
2. ตั้งที่เก็บดาวน์โหลด (`chrome://settings/downloads`) เป็นโฟลเดอร์ `incoming/` ของ repo นี้
3. ไปที่ `chrome://extensions/` เปิด Developer mode
4. กด Load unpacked แล้วเลือกโฟลเดอร์ `extension/` นี้
5. Pin extension ไว้ที่ toolbar

ถ้าอัพเดทจากรุ่นก่อนเป็น v0.6.0 ให้กด Reload และอนุญาต host `trends.google.co.th` ถ้า Chrome แสดงคำขอสิทธิ์ รุ่นนี้ใช้หน้า Explore ใหม่เพราะหน้า classic ลด full-history export เหลือรายปี

## รอบการเก็บ

1. สร้างคิวงาน:
   ```
   python collector/make_jobs.py --all              # ทุกคำทุกพื้นที่ (300 jobs)
   python collector/make_jobs.py --ids FP014        # เฉพาะบางคำ
   python collector/make_jobs.py --group FP --geo TH
   ```
2. ไป `chrome://extensions/` กด Reload ที่ตัว extension (ให้มันเห็น jobs ใหม่)
3. คลิกไอคอน extension > Open Controller
4. กด **Load Jobs (reset queue)** แล้วกด **Start**
5. ปล่อยให้หน้าต่าง Chrome นั้นอยู่หน้าสุด อย่า minimize ระหว่างรัน
   ถ้าเจอ CAPTCHA: แก้ในแท็บที่เด้งขึ้น แล้วกด Resume
6. เก็บครบแล้ว กลับมาที่ repo:
   ```
   python -X utf8 collector/ingest.py --dry-run
   python -X utf8 collector/ingest.py
   python -X utf8 collector/audit.py --strict --require-latest
   python -X utf8 collector/build_site_data.py --check
   python -X utf8 -m unittest discover -s tests -v
   git add -- data/series data/catalog.json data.js
   git commit -m "update data YYYY-MM"
   git push
   ```

   ห้าม commit ถ้า audit/test/check ตัวใดตัวหนึ่งไม่ผ่าน และห้ามใช้ `git add -A`
   ในรอบเผยแพร่ข้อมูล เพราะอาจพาไฟล์คิวหรือไฟล์อื่นที่ไม่เกี่ยวเข้า commit

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
- v0.5.0 เพิ่ม fail-closed acknowledgment bridge สำหรับ `collector/browser_runner.py`: extension จะยอมรับ download ชื่อ GUID ของ Playwright เฉพาะเมื่อ Python จับคู่กับ RUNNING job ได้หนึ่งตัวและไฟล์ผ่าน parser + canonical coverage guard ของ `ingest.py` แล้ว
- v0.6.0 เปลี่ยนไปใช้ `trends.google.co.th/explore?date=all`, รองรับ chart/download selector และชื่อไฟล์ `time_series_<GEO>_*.csv` ของหน้าใหม่ หน้าใหม่นี้ส่ง full-history รายเดือนใน Chrome ปกติ; หน้า classic/pytrends ยังส่ง `Year`
- ข้อจำกัดที่พบ 2026-07-15: profile แยกของ Python runner ยังโหลด chart รุ่นใหม่ไม่สำเร็จ (`CHART_TIMEOUT`) จึงยังใช้ publish ไม่ได้ ให้รัน extension ใน Chrome ปกติเป็นเส้นทางหลัก
- `data/jobs.json` และ `data/jobs_index.json` เป็นไฟล์ generate จาก `make_jobs.py` ไม่ commit เข้า git
