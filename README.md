# Google Trends Toolkit

เครื่องมือเก็บ/อัพเดทข้อมูล Google Trends พร้อมหน้าแสดงผล สำหรับชุดคำค้นตลาดแรงงานภาคอีสาน 50 คำ
(ต่อยอดจากโปรเจกต์ Isan Labor Search-Intent Index, ธปท. สำนักงานภาคตะวันออกเฉียงเหนือ)

มี 2 ส่วน:

1. **ตัวเก็บ/อัพเดทข้อมูล** (`collector/collect.py`) เลือกได้ว่าจะอัพเดทรายคำ รายกลุ่ม หรือทั้งหมด
2. **หน้าแสดงผล** (`index.html`) เลือกคำและพื้นที่ได้อิสระทั้งสองมิติ (คำที่ติ๊ก x พื้นที่ที่ติ๊ก รวมไม่เกิน 8 เส้นเพื่อให้อ่านออก) ครอบคลุมประเทศไทย + อีสานคอมโพสิต + 5 จังหวัด พร้อมช่วงเวลา (ทั้งหมด/10 ปี/5 ปี/3 ปี/1 ปี), ปรับฐานดัชนีเดือนอ้างอิง = 100, สลับค่าดิบ/MA3, มุมมองตาราง และดาวน์โหลด CSV ตามที่แสดงอยู่

ด้านบนของหน้าเว็บมี **Data health strip** บอกเดือนข้อมูลล่าสุด ความครอบคลุม จำนวนซีรีส์ในแต่ละ signal tier และวันที่เก็บข้อมูล พร้อมคำเตือนรายเส้นเมื่อสัญญาณบางหรืออีสานคอมโพสิตมีจังหวัดสนับสนุนไม่ครบ 5 จังหวัด คำเตือนนี้บอกคุณภาพการสังเกต ไม่ได้แปลว่า “ไม่มีความต้องการแรงงาน”

## เปิดหน้าแสดงผล

- ผ่านเว็บ: https://reload0981-ops.github.io/google-trends-toolkit/
- ในเครื่อง: เปิดไฟล์ `index.html` ด้วย browser ได้เลย (ข้อมูลฝังใน `data.js` ไม่ต้องรัน server)

## เก็บ/อัพเดทข้อมูล (3 ทาง)

### ทางที่ 1: Chrome extension + ingest (เส้นทางหลัก แม่นสุด)

**เก็บชุดใหญ่: ใช้ Chrome extension ใน `extension/`** (พอร์ตจากตัวที่พิสูจน์แล้วในโปรเจคเดิม 300+ jobs) มันไล่โหลด CSV จากหน้า Google Trends ใน Chrome จริงตามคิวงาน พร้อมระบบ pause/retry/CAPTCHA และตั้งชื่อไฟล์ให้ ingest กินได้ทันที:

```
python collector/make_jobs.py --all      # สร้างคิว 300 jobs (หรือ --ids / --group / --geo)
# โหลด extension แล้วกด Load Jobs > Start (วิธีติดตั้งดู extension/README.md)
python collector/ingest.py
```

ตั้งแต่ v0.6.0 extension ใช้หน้า Explore รุ่นใหม่ที่ `trends.google.co.th/explore?date=all` ซึ่งยืนยันแล้วว่า full history ยังส่งข้อมูลรายเดือน (`Time,<keyword>`) ใน Chrome ปกติของผู้ใช้ หลังอัพเดท extension ต้องกด Reload ใน `chrome://extensions/` และอนุญาต host `trends.google.co.th` ถ้า Chrome ถาม

ถ้าคู่คำค้น × พื้นที่ใดไม่มีข้อมูล Controller จะลองยืนยัน **no-data ติดต่อกันอย่างน้อย 2 ครั้ง** แล้วดาวน์โหลด `no_data_manifest__YYYY-MM-DD.json` เข้า `incoming/` อัตโนมัติ; `ingest.py` จะตรวจ manifest ก่อนบันทึกสถานะ โดยไม่ยอมเปลี่ยนคู่ที่มี CSV เดิมให้เป็น no-data

**เก็บมือไม่กี่ไฟล์:** โหลด CSV จากหน้าเว็บ Google Trends เอง (ช่วงเวลา = ยาวสุด 2004-01-01 ถึงปัจจุบัน ตามนโยบายข้อมูลหลัก) วางใน `incoming/` จากนั้น:

```
python collector/ingest.py --dry-run   # ตรวจการจับคู่ก่อน ไม่เขียนอะไร
python collector/ingest.py             # เข้าคลังจริง + rebuild หน้าเว็บ
```

ใช้ Python มาตรฐาน ไม่ต้องติดตั้งอะไรเลย รู้จักทั้งไฟล์ export ของหน้าเว็บ GT แบบ classic, ไฟล์หน้าใหม่ชื่อ `time_series_<GEO>_*.csv`, ไฟล์ที่ตั้งชื่อ `<ID>__<GEO>.csv`, `manual_<ID>.csv` และ no-data manifest ไฟล์ CSV ต้องเริ่ม `2004-01` (TH) หรือ `2014-01` (จังหวัด), ต่อเนื่องถึงเดือนสมบูรณ์ล่าสุด และมีค่า finite 0–100 จึงจะเขียนทับคลังได้ เดือนปัจจุบันที่หน้าใหม่ระบุว่าเป็น partial จะถูกตัดออก ไฟล์ที่ไม่ผ่านจะถูกย้ายไป `incoming/review/` พร้อมเหตุผล ไม่มีการเดา

`ingest.py --since` ถูกปิดใช้งานโดยตั้งใจ เพราะการตัดข้อมูลเก่าแล้วนำช่วงสั้นไปทับซีรีส์เดิมจะทำลาย canonical long-horizon archive ต้อง export ใหม่ทั้งช่วง `2004-01-01` ถึงวันนี้แล้ว ingest โดยไม่ใส่ `--since` เท่านั้น

#### Python browser runner สำหรับ AI (experimental)

`collector/browser_runner.py` เปิด persistent Playwright Chromium พร้อม extension ตัวเดิม ทำให้ AI คุม queue จาก terminal ได้โดยไม่ต้องพอร์ต retry/CAPTCHA/no-data logic ซ้ำ:

```
pip install -r requirements.txt
python -m playwright install chromium               # ครั้งแรกครั้งเดียว
python collector/make_jobs.py --all
python -X utf8 collector/browser_runner.py --plan --json
python -X utf8 collector/browser_runner.py --start
python -X utf8 collector/browser_runner.py --status --json   # เรียกจากอีก terminal ได้
python -X utf8 collector/browser_runner.py --resume
```

runner เก็บ browser profile/status/download ชั่วคราวใน `.browser-runner/` (gitignored), หยุดรอคนเมื่อเจอ CAPTCHA และใช้ parser + canonical coverage guard ชุดเดียวกับ `ingest.py` ตรวจไฟล์ก่อนวางใน `incoming/` เสมอ Playwright เก็บชื่อ download ภายในเป็น GUID จึงมี acknowledgment bridge ที่ extension ยอมรับเฉพาะ filename/job/time ที่ตรงกันและไฟล์ที่ผ่าน guard แล้วเท่านั้น

**สถานะที่ยืนยัน 2026-07-15:** หน้า classic และ pytrends ส่ง full-window เป็น `Year` 23 จุด แต่หน้า Explore รุ่นใหม่ใน Chrome ปกติส่ง `Time` รายเดือน 271 จุด (2004-01 ถึง 2026-07 partial); parser ตัดเดือน partial แล้วผ่าน canonical guard 270 เดือนถึง 2026-06 อย่างถูกต้อง อย่างไรก็ดี Playwright profile แยกของ runner ยังเข้าไม่ถึง chart รุ่นใหม่ (`CHART_TIMEOUT`) จึงยังห้ามใช้ Python runner publish รอบข้อมูล เส้นทาง release ที่พึ่งได้คือ extension v0.6.0 ใน Chrome ปกติของผู้ใช้

### ทางที่ 2: ดึงเองผ่าน pytrends (งานเบา ไม่กี่คำ)

ติดตั้งครั้งแรก: `pip install -r requirements.txt` (Python 3.9+)

```
python collector/collect.py --plan --all          # ดูก่อนว่าจะเก็บอะไรบ้าง ไม่ยิง API
python collector/collect.py --ids FP014,FU014     # อัพเดทรายคำ
python collector/collect.py --group FP,FU         # อัพเดทรายกลุ่ม (prefix ของ ID)
python collector/collect.py --all                 # ทุกคำทุกพื้นที่ (ราว 1.5-2 ชม. เสี่ยงโดน rate limit)
```

โดน 429 จะรอแล้วลองใหม่เอง ถ้าโดนหนักจะหยุดทั้งรอบ รันคำสั่งเดิมซ้ำได้เลย ตัวที่สำเร็จแล้ววันนี้จะถูกข้าม อย่าลด `--sleep` ต่ำกว่า default

`make_jobs.py` และ `collect.py` บังคับ canonical window เดียวกัน: `--start 2004-01-01`, `--end` ต้องเป็นวันที่วันนี้ และ `collect.py --sleep` ต้องไม่น้อยกว่า 15 วินาที ค่าอื่นจะถูกปฏิเสธก่อนเริ่มเก็บ เพื่อกันข้อมูลช่วงสั้นทับคลังหลัก

### ทางที่ 3: อัตโนมัติบน GitHub (ไม่ต้องมีคนรัน)

GitHub Actions (`.github/workflows/update-data.yml`) รัน `--all` ให้เอง **ทุกวันที่ 3 ของเดือน** แล้ว commit ข้อมูล หน้าเว็บอัพเดทเองครบวงจร สั่งรันทันทีได้ที่แท็บ Actions > update-data > Run workflow (ช่อง args รับ scope เช่น `--ids` หรือ `--geo` ได้ แต่รอบ publish รายเดือนควรใช้ `--all` เพราะ freshness gate ตรวจทุกซีรีส์ และเปลี่ยน canonical window ไม่ได้)

ข้อจำกัดที่ต้องรู้ (ทดสอบจริง 2026-07-09): Google บล็อก IP ของ GitHub runner ค่อนข้างแรง รอบทดสอบโดน 429 ตั้งแต่ request แรก ดังนั้นให้มองทางนี้เป็น best-effort ที่ตั้งทิ้งไว้ฟรีๆ **เส้นทางที่พึ่งได้จริงคือทางที่ 1** หรือถ้าอยากได้อัตโนมัติแท้ๆ: ตั้ง Task Scheduler บนเครื่องจริงให้รัน `python collector/collect.py --all` + `git push` รายเดือน (IP บุคคลผ่านง่ายกว่ามาก) หรือใช้ self-hosted runner

workflow นี้เป็นแบบ **fail-closed**: collector, complete-release audit, deterministic build check และ tests ต้องผ่านทั้งหมดจึงจะ publish; ถ้า origin เปลี่ยนระหว่างเก็บก็จะหยุด และอนุญาตให้ commit เฉพาะ `data/series/*.csv`, `data/catalog.json` และ `data.js` เท่านั้น จึงไม่มีการปล่อยข้อมูลบางส่วนหรือไฟล์แปลกปลอมโดยเงียบๆ

ทั้ง 3 ทางทำสิ่งเดียวกันเสมอ: แปลงข้อมูลเป็นรายเดือน, ตัดเดือนที่ยังไม่จบ, **แทนที่ซีรีส์เดิมทั้งเส้น** (ค่า Google Trends เป็น index เทียบภายในช่วงที่ดึง การต่อท่อนคนละช่วงทำให้ scale เพี้ยน) แล้ว rebuild `data.js`

### ตรวจสุขภาพและเผยแพร่อย่างปลอดภัย

หลัง ingest/collect ทุกครั้ง ให้รันตามลำดับนี้:

```
python -X utf8 collector/audit.py --strict
python -X utf8 collector/audit.py --strict --require-latest
python -X utf8 collector/build_site_data.py --check
python -X utf8 -m unittest discover -s tests -v
git status --short
```

- `--strict` fail เมื่อไฟล์ที่มีอยู่มี schema/ลำดับเดือนไม่ถูกต้อง, catalog ไม่สอดคล้อง หรือหลักฐาน no-data ผิดรูป; คู่ที่ยัง missing จะแสดงใน coverage แยก
- `--require-latest` คือ complete-release gate: ทั้ง 300 คู่ต้องเป็นซีรีส์ที่ถึงเดือนกำหนด หรือ confirmed no-data จาก canonical window หลังเดือนนั้น; missing, invalid, stale data และ stale no-data ทำให้ fail ระบุเดือนเองได้ เช่น `--require-latest 2026-06`
- `collector/audit.py --json` แสดงรายงาน machine-readable สำหรับตรวจต่อหรือเก็บหลักฐาน
- signal tier คำนวณจาก 64 เดือนล่าสุดของแต่ละซีรีส์: `VERY_GOOD` = ไม่มีเดือนศูนย์, `ACCEPTABLE` = 1–16 เดือนศูนย์, `WEAK` = มากกว่า 16 เดือนศูนย์ ซีรีส์ศูนย์ตลอดถูกระบุแยกด้วย

ถ้าทุก gate ผ่านและ `git status --short` มีเฉพาะ generated data ที่คาดไว้ ให้ stage แบบ allowlist แล้ว publish:

```
git add -- data/series data/catalog.json data.js
git diff --cached --name-only
git commit -m "update data <รายละเอียดสั้น>"
git push
```

ถ้าเพิ่ม/แก้คำค้น ให้ตรวจและ stage `keywords.csv` แยกต่างหาก ห้ามใช้ `git add -A` ในรอบ publish ข้อมูล หน้าเว็บบน Pages จะอัพเดทในราว 1–2 นาที

workflow `validate.yml` จะรัน tests, `audit.py --strict` และ deterministic build check ทุก push/PR โดยไม่เขียนข้อมูล และตั้งใจไม่ใช้ `--require-latest` เพราะ code validation ต้องแยกจากรอบ refresh/publish

## สำหรับ AI agent

repo นี้มี `SKILL.md` เป็นคู่มือปฏิบัติงานสำหรับ AI: เปิด AI agent (Claude Code, Codex ฯลฯ) ในโฟลเดอร์นี้แล้วสั่งงานภาษาคน เช่น "อัพเดทข้อมูลเดือนนี้" หรือ "เพิ่มคำว่า X" ได้เลย agent จะทำตาม workflow และกติกาเหล็กในนั้น (`CLAUDE.md` และ `AGENTS.md` ชี้มาที่ `SKILL.md` ให้อัตโนมัติ)

## ข้อมูลในชุด

| ไฟล์ | คืออะไร |
|---|---|
| `keywords.csv` | คำค้น 50 คำที่ใช้งานอยู่ (ผ่านการคัดกรองครบทุกขั้นจากโปรเจกต์เดิม) พร้อม Tier / Segment / Factor |
| `reference/keywords_tried.csv` | คำค้น 1,192 คำที่เคยถูกคิด/ทดสอบทั้งหมด พร้อมคอลัมน์ `best_stage` บอกว่าแต่ละคำไปไกลสุดถึงขั้นไหน (คำที่ไม่ผ่านอยู่ในนี้ ใช้เช็คก่อนคิดคำใหม่ว่าเคยลองแล้วหรือยัง) |
| `data/series/<ID>__<GEO>.csv` | ข้อมูลรายเดือนต่อคำต่อพื้นที่ (`Month,Value`) |
| `data/catalog.json` | บันทึกเวลา/ช่วงเก็บและสถานะ `available` หรือ confirmed `no_data` ของแต่ละคู่ |
| `data.js` | ข้อมูลรวมสำหรับหน้าแสดงผล (สร้างอัตโนมัติ อย่าแก้มือ) |
| `collector/audit.py` | ตรวจ coverage, โครงสร้าง, signal quality และ freshness gate โดยไม่แก้ข้อมูล |

พื้นที่ที่รองรับ: `TH` ประเทศไทย, `TH-30` นครราชสีมา, `TH-31` บุรีรัมย์, `TH-34` อุบลราชธานี, `TH-40` ขอนแก่น, `TH-41` อุดรธานี
(เพิ่ม/ลดได้ที่ตัวแปร `GEOS` ใน `collector/collect.py` และ `collector/build_site_data.py`)

พื้นที่พิเศษ `ISAN` "อีสาน (คอมโพสิต)" เป็น**ซีรีส์คำนวณ** ไม่ได้เก็บจาก Google โดยตรง: rebase จังหวัดที่มีสัญญาณ (ค่าสูงสุดมากกว่า 0) ให้ max = 100 → เฉลี่ยด้วยน้ำหนักเท่ากัน → rebase ผลรวมให้ max = 100 (สูตรเดียวกับ REG_ISAN5 ของโปรเจกต์เดิม) คำนวณใหม่อัตโนมัติทุกครั้งที่ rebuild `data.js`

คอมโพสิตไม่ได้มีครบ 5 จังหวัดทุกคำเสมอไป แต่ละซีรีส์จึงแนบ `support_n`, `support_total=5` และ `support_geos` ใน `data.js`; หน้าเว็บจะแสดง `N/5` และเตือนเมื่อใช้เพียง 2–4 จังหวัด ห้ามตีความหรือเรียกเส้นดังกล่าวว่าเป็นผลรวมครบทั้ง 5 จังหวัด

การเพิ่มคำใหม่: เพิ่มแถวใน `keywords.csv` (ตั้ง `Keyword_ID` ไม่ให้ซ้ำ และเช็ค `reference/keywords_tried.csv` ก่อนว่าเคยลองแล้วหรือยัง) แล้วรัน `python collector/collect.py --ids <ID ใหม่>`

## ข้อควรรู้เรื่องตัวเลข

- ค่าเป็น Google Trends index 0-100 เทียบภายในช่วงเวลาที่ดึงของแต่ละคำและพื้นที่ **ไม่ใช่จำนวนการค้นจริง** และห้ามเทียบขนาดข้ามคำตรงๆ
- **นโยบายการเก็บ: โหลดยาวสุดเสมอ (2004-01-01 ถึงปัจจุบัน)** ข้อมูลหลักของชุดนี้คือ long horizon
- **ระดับจังหวัดใช้ได้ตั้งแต่ 2014-01 เท่านั้น** Google ปรับระบบระบุตำแหน่งช่วง 2011-2013 (จังหวัดชุดท้ายเริ่มต่อเนื่อง 2013-07) ข้อมูลจังหวัดก่อนหน้านั้นเป็นรู/break เครื่องมือทุกตัวตัดทิ้งให้อัตโนมัติ ระดับประเทศไม่ตัด
- ข้อมูลตั้งต้น seed จากชุด long horizon ของโปรเจกต์เดิม (ดึง 2026-05): ประเทศ 2004-01 ถึง 2026-04 ครบ 50 คำ, จังหวัด 2014-01 ถึง 2026-04; คู่ที่ยังไม่มีซีรีส์ต้องเก็บซ้ำและยืนยัน no-data ก่อนตีความ และไม่ใช่หลักฐานว่าไม่มีอุปสงค์แรงงาน

## ที่มา

ผู้จัดทำ: Nitisart Srijunpho, งานสหกิจศึกษา ธนาคารแห่งประเทศไทย สำนักงานภาคตะวันออกเฉียงเหนือ (2026)
