# Google Trends Toolkit Scraper (Chrome extension)

เครื่องมือเก็บชุดใหญ่: ไล่โหลด CSV จากหน้า Google Trends ใน Chrome จริงตามคิวงาน
ผ่านง่ายกว่า pytrends มาก (เป็น browser จริง IP จริงของผู้ใช้) พิสูจน์มาแล้วจากโปรเจคเดิมกว่า 300 jobs

ไฟล์ที่โหลดถูกตั้งชื่อ `<ID>__<GEO>.csv` อัตโนมัติ พร้อมให้ `collector/ingest.py` กินทันที

## ติดตั้ง (ครั้งเดียว)

1. เปิด Chrome แนะนำให้สร้าง profile แยกสำหรับงานนี้
2. ตั้งที่เก็บดาวน์โหลด (`chrome://settings/downloads`) เป็นโฟลเดอร์ `incoming/` ของ repo นี้
3. ไปที่ `chrome://extensions/` เปิด Developer mode
4. กด Load unpacked แล้วเลือกโฟลเดอร์ `extension/` นี้
5. Pin extension ไว้ที่ toolbar

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
   python collector/ingest.py --dry-run   # ตรวจก่อน
   python collector/ingest.py             # เข้าคลังจริง
   git add -A && git commit -m "update data" && git push
   ```

(jobs ดึงยาวสุด 2004-01-01 ถึงปัจจุบันตามนโยบายข้อมูลหลัก long horizon
ระดับจังหวัดที่ก่อน 2014-01 ถูกตัดทิ้งอัตโนมัติตอน ingest เพราะ geo break ของ Google)

## ปุ่มในหน้า Controller

- Start / Pause / Resume / Stop / Skip Current: คุมคิว
- Retry Failed/No Data: ลองใหม่เฉพาะตัวที่พลาด
- Reconcile Downloads: เทียบกับประวัติดาวน์โหลดของ Chrome แล้ว mark งานที่เสร็จแล้ว (ใช้ตอนเปิด controller ใหม่หลังเบราว์เซอร์ปิด)
- Copy Debug: ก๊อปรายงานสถานะไว้ส่งให้คนช่วยดู

## หมายเหตุ

- Extension นี้พอร์ตมาจากตัวที่ใช้จริงในโปรเจค Isan Labor (เวอร์ชัน 0.2.6 → 0.3.0)
  เปลี่ยนเฉพาะ schema ชื่อไฟล์กับข้อความ ตัว logic คิว/retry/CAPTCHA คงเดิมทั้งหมด
- `data/jobs.json` และ `data/jobs_index.json` เป็นไฟล์ generate จาก `make_jobs.py` ไม่ commit เข้า git
