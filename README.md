# Google Trends Toolkit

เครื่องมือเก็บ/อัพเดทข้อมูล Google Trends พร้อมหน้าแสดงผล สำหรับชุดคำค้นตลาดแรงงานภาคอีสาน 50 คำ
(ต่อยอดจากโปรเจกต์ Isan Labor Search-Intent Index, ธปท. สำนักงานภาคตะวันออกเฉียงเหนือ)

ระบบ production มีเส้นทางเดียว:

`Google Trends → Chrome extension → incoming/ → Python ingest/audit → data/ → GitHub Pages`

- **ตัวเก็บข้อมูลจริง** คือ Chrome extension ใน `extension/` ซึ่งรันบน Chrome profile ที่ลงชื่อเข้าใช้ Google แล้ว
- **Python** สร้างคิว ตรวจ CSV เข้าคลัง และตรวจ release gate; ไม่ได้เป็นตัวโหลดชุดใหญ่ในเส้นทาง production
- **GitHub** คือคลังถาวรของโค้ดและข้อมูล ไม่ใช่ตัวเก็บจาก Google Trends
- `incoming/`, `extension/data/jobs*.json` และ `.browser-runner/` เป็นสถานะชั่วคราวเฉพาะเครื่อง ไม่ขึ้น Git
- **หน้าแสดงผล** คือ `index.html` และข้อมูลที่สร้างอัตโนมัติใน `data.js`

ด้านบนของหน้าเว็บมี **Data health strip** บอกเดือนข้อมูลล่าสุด ความครอบคลุม จำนวนซีรีส์ในแต่ละ signal tier และวันที่เก็บข้อมูล พร้อมคำเตือนรายเส้นเมื่อสัญญาณบางหรืออีสานคอมโพสิตมีจังหวัดสนับสนุนไม่ครบ 5 จังหวัด คำเตือนนี้บอกคุณภาพการสังเกต ไม่ได้แปลว่า “ไม่มีความต้องการแรงงาน”

## เปิดหน้าแสดงผล

- ผ่านเว็บ: https://reload0981-ops.github.io/google-trends-toolkit/
- ในเครื่อง: เปิดไฟล์ `index.html` ด้วย browser ได้เลย (ข้อมูลฝังใน `data.js` ไม่ต้องรัน server)

## ย้ายไปเครื่องใหม่

1. Clone repo แล้วเปิด AI agent ที่ root ของ repo; `AGENTS.md` / `CLAUDE.md` จะชี้ให้ Agent อ่าน `SKILL.md`
2. ให้ Agent รัน `powershell -ExecutionPolicy Bypass -File .\scripts\toolkit.ps1 setup` คำสั่งเดียว เพื่อตรวจ Python 3.11–3.13, Git, Chrome, GitHub auth, เตรียม `.venv` + X-13 แบบ repo-local และรัน audits/tests ครบโดยห้ามข้าม analytical tests
3. ผู้ใช้ตั้ง Chrome ครั้งเดียว: Load unpacked จาก `extension/`, อนุญาต `trends.google.co.th`, ตั้ง Downloads ไป `incoming/` ของ clone ใหม่ และปิด **Ask where to save each file**
4. หลังจากนั้นบอก Agent เพียง “อัพเดทข้อมูลเดือนนี้” หรือ “เพิ่มคำว่า …” ได้เลย

ข้อมูลถาวรจะตามมาครบจาก GitHub แต่คิวที่กำลังรัน ไฟล์ใน `incoming/`, Chrome extension และ GitHub login ต้องตั้งใหม่ต่อเครื่อง

## Monthly update ทางการ

### Chrome extension + Python ingest

**เก็บชุดใหญ่: ใช้ Chrome extension ใน `extension/`** (พอร์ตจากตัวที่พิสูจน์แล้วในโปรเจคเดิม 300+ jobs) มันไล่โหลด CSV จากหน้า Google Trends ใน Chrome จริงตามคิวงาน พร้อมระบบ pause/retry/CAPTCHA และตั้งชื่อไฟล์ให้ ingest กินได้ทันที:

```powershell
.\scripts\toolkit.ps1 monthly-prepare                # สร้างคิว 300 jobs
# Controller > Import jobs.json > เลือก extension/data/jobs.json > Start
.\scripts\toolkit.ps1 monthly-finish                 # ingest + ทุก release gate
```

จำกัดคิวได้โดยส่ง argument เดิมของ `make_jobs.py` ต่อท้าย เช่น `.\scripts\toolkit.ps1 monthly-prepare --ids FP014 --geo TH` คำสั่งเดิมระดับล่างยังใช้ตรวจแก้ปัญหาได้ แต่รอบ production ให้เข้าผ่าน wrapper นี้เสมอ

ตั้งแต่ v0.6.0 extension ใช้หน้า Explore รุ่นใหม่ที่ `trends.google.co.th/explore?date=all` ซึ่งยืนยันแล้วว่า full history ส่งข้อมูลรายเดือน (`Time,<keyword>`) ใน Chrome ปกติของผู้ใช้ ตั้งแต่ v0.7.0 Controller import queue จากไฟล์ได้ จึงไม่ต้อง Reload extension ทุกครั้งที่สร้างคิวใหม่ ส่วน v0.7.1 บังคับหน้าต่าง scraper ให้กว้างพอสำหรับปุ่ม time series และปฏิเสธ CSV จาก widget อื่นแบบ fail closed; v0.7.2 รับคิว `date=all` ที่สร้างวันก่อนและให้ download-validation error หยุดงานจริง การอัพเกรดเป็น v0.7.2 ต้อง Reload ครั้งเดียว

ถ้าคู่คำค้น × พื้นที่ใดไม่มีข้อมูล Controller จะลองยืนยัน **no-data ติดต่อกันอย่างน้อย 2 ครั้ง** แล้วดาวน์โหลด `no_data_manifest__YYYY-MM-DD.json` เข้า `incoming/` อัตโนมัติ; `ingest.py` จะตรวจ manifest ก่อนบันทึกสถานะ โดยไม่ยอมเปลี่ยนคู่ที่มี CSV เดิมให้เป็น no-data

**เก็บมือไม่กี่ไฟล์:** โหลด CSV จากหน้าเว็บ Google Trends เอง (ช่วงเวลา = ยาวสุด 2004-01-01 ถึงปัจจุบัน ตามนโยบายข้อมูลหลัก) วางใน `incoming/` จากนั้น:

```powershell
.\scripts\toolkit.ps1 monthly-finish
```

ตัว ingest ใช้ Python standard library ส่วน analytical gates ใช้ `.venv` ที่ `setup` เตรียมไว้ มันรู้จักทั้งไฟล์ export ของหน้าเว็บ GT แบบ classic, ไฟล์หน้าใหม่ชื่อ `time_series_<GEO>_*.csv`, ไฟล์ที่ตั้งชื่อ `<ID>__<GEO>.csv`, `manual_<ID>.csv` และ no-data manifest ไฟล์ CSV ต้องเริ่ม `2004-01` (TH) หรือ `2014-01` (จังหวัด), ต่อเนื่องถึงเดือนสมบูรณ์ล่าสุด และมีค่า finite 0–100 จึงจะเขียนทับคลังได้ เดือนปัจจุบันที่หน้าใหม่ระบุว่าเป็น partial จะถูกตัดออก ไฟล์ที่ไม่ผ่านจะถูกย้ายไป `incoming/review/` พร้อมเหตุผล ไม่มีการเดา

`ingest.py --since` ถูกปิดใช้งานโดยตั้งใจ เพราะการตัดข้อมูลเก่าแล้วนำช่วงสั้นไปทับซีรีส์เดิมจะทำลาย canonical long-horizon archive ต้อง export ใหม่ทั้งช่วง `2004-01-01` ถึงวันนี้แล้ว ingest โดยไม่ใส่ `--since` เท่านั้น

### สร้างชุดวิเคราะห์ SA หลัง raw update

ขั้นนี้แยกจาก raw ingest ภายใน pipeline โดยตั้งใจ และไม่แก้ `data/` หรือ `data.js` แต่ `monthly-finish` จะเรียก build → byte-check → audit ให้ครบอัตโนมัติ เพื่อไม่ให้ raw กับ derived หลุดคนละ release

ผลลัพธ์อยู่ใน `derived/sa_pipeline_v3/` ครบทั้ง 30 cases สำหรับ `TH` และ `REG_ISAN5` พร้อม method log, rebase audit, X-13 diagnostics, quality sidecar และ manifest ที่ผูกกับ hash ของ raw source ดูสัญญาวิธีคำนวณและกติกา fallback ฉบับเต็มที่ `analysis/README.md`

### ทาง diagnostic (ห้ามใช้กับ canonical data หรือ publish)

#### Python browser runner

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

runner เก็บ browser profile/status และไฟล์ที่ตรวจแล้วไว้เฉพาะ `.browser-runner/` (gitignored) โดยไม่เขียน `incoming/`, หยุดรอคนเมื่อเจอ CAPTCHA และใช้ parser + canonical coverage guard ชุดเดียวกับ `ingest.py` ตรวจไฟล์ Playwright เก็บชื่อ download ภายในเป็น GUID จึงมี acknowledgment bridge ที่ extension ยอมรับเฉพาะ filename/job/time ที่ตรงกันและไฟล์ที่ผ่าน guard แล้วเท่านั้น ผลจาก runner เป็น diagnostic เท่านั้น: ห้ามนำเข้า canonical archive, stage หรือ publish

**สถานะที่ยืนยัน 2026-07-15:** Playwright profile แยกถูก Explore ใหม่หยุดที่ auth gate เพราะยังไม่ได้ลงชื่อเข้าใช้ Google; เปลี่ยน Chromium เป็น Chrome อย่างเดียวไม่ช่วย และหน้า classic ส่งเพียง `Year` 23 จุด จึงห้าม fallback และผลจาก runner ไม่ถือเป็น canonical ไม่ว่าสถานะ login จะเป็นอย่างไร

#### pytrends (diagnostic เท่านั้น)

ติดตั้งครั้งแรกใน working copy สำหรับทดลอง: `pip install -r requirements.txt` (Python 3.11–3.13)

```
python collector/collect.py --plan --all          # ดูก่อนว่าจะเก็บอะไรบ้าง ไม่ยิง API
python collector/collect.py --ids FP014,FU014     # ทดลองรายคำ (เกิด local changes)
python collector/collect.py --group FP,FU         # ทดลองรายกลุ่ม (prefix ของ ID)
python collector/collect.py --all                 # ทดลองทุกคำทุกพื้นที่ (ราว 1.5-2 ชม. เสี่ยงโดน rate limit)
```

โดน 429 จะรอแล้วลองใหม่เอง ถ้าโดนหนักจะหยุดทั้งรอบ รันคำสั่งเดิมซ้ำได้เลย ตัวที่สำเร็จแล้ววันนี้จะถูกข้าม อย่าลด `--sleep` ต่ำกว่า default

คำสั่งกลุ่มนี้อาจสร้าง local changes เพื่อใช้วินิจฉัย แต่ผลไม่ใช่ canonical data และห้าม ingest, stage หรือ publish การเพิ่ม/อัพเดทคำจริงต้องกลับไปสร้างคิว extension ด้วย `monthly-prepare`

`make_jobs.py` และ `collect.py` บังคับ canonical window เดียวกัน: `--start 2004-01-01`, `--end` ต้องเป็นวันที่วันนี้ และ `collect.py --sleep` ต้องไม่น้อยกว่า 15 วินาที ค่าอื่นจะถูกปฏิเสธก่อนเริ่มเก็บ เพื่อกันข้อมูลช่วงสั้นทับคลังหลัก

#### GitHub Actions (manual diagnostic only)

workflow `.github/workflows/experimental-pytrends.yml` เปิดให้สั่งมือเพื่อวินิจฉัย pytrends เท่านั้น ใช้สิทธิ์ read-only ไม่มี schedule และไม่มีขั้น commit/push

ข้อจำกัดที่ต้องรู้ (ทดสอบจริง 2026-07-09): Google บล็อก IP ของ GitHub runner ค่อนข้างแรง รอบทดสอบโดน 429 ตั้งแต่ request แรก จึงเป็นเพียง best-effort และห้ามใช้ผล publish แทน Monthly update ทางการ

ไม่ว่าผล workflow จะเป็นอย่างไร local changes บน runner จะถูกทิ้งเมื่อจบ และไม่ถือเป็น release

เส้นทาง production จะแปลงข้อมูลเป็นรายเดือน ตัดเดือนที่ยังไม่จบ และ **แทนที่ซีรีส์เดิมทั้งเส้น** (ค่า Google Trends เป็น index เทียบภายในช่วงที่ดึง การต่อท่อนคนละช่วงทำให้ scale เพี้ยน) แล้ว rebuild `data.js`

### ตรวจสุขภาพและเผยแพร่อย่างปลอดภัย

หลัง extension เก็บครบ ให้รันคำสั่งเดียว:

```powershell
.\scripts\toolkit.ps1 monthly-finish
```

wrapper จะรัน ingest dry-run → ingest จริง → raw structural/freshness audits → `data.js` check → analytical build/byte-check/audit → full tests → `git status` และหยุดทันทีเมื่อ native command ใดคืน exit code ผิดปกติ ระบุเดือน gate เองได้ด้วย `-RequireLatest 2026-06` คำสั่งนี้ **ไม่ stage, commit, push หรือ deploy**

- `--strict` fail เมื่อไฟล์ที่มีอยู่มี schema/ลำดับเดือนไม่ถูกต้อง, catalog ไม่สอดคล้อง หรือหลักฐาน no-data ผิดรูป; คู่ที่ยัง missing จะแสดงใน coverage แยก
- `--require-latest` คือ complete-release gate: ทั้ง 300 คู่ต้องเป็นซีรีส์ที่ถึงเดือนกำหนด หรือ confirmed no-data จาก canonical window หลังเดือนนั้น; missing, invalid, stale data และ stale no-data ทำให้ fail ระบุเดือนเองได้ เช่น `--require-latest 2026-06`
- `collector/audit.py --json` แสดงรายงาน machine-readable สำหรับตรวจต่อหรือเก็บหลักฐาน
- signal tier คำนวณจาก 64 เดือนล่าสุดของแต่ละซีรีส์: `VERY_GOOD` = ไม่มีเดือนศูนย์, `ACCEPTABLE` = 1–16 เดือนศูนย์, `WEAK` = มากกว่า 16 เดือนศูนย์ ซีรีส์ศูนย์ตลอดถูกระบุแยกด้วย

ถ้าทุก gate ผ่านและ `git status --short` มีเฉพาะ generated data ที่คาดไว้ ให้ stage แบบ allowlist แล้ว publish:

```
git add -- data/series data/catalog.json data.js derived/sa_pipeline_v3
git diff --cached --name-only
git commit -m "update data <รายละเอียดสั้น>"
git push
```

ถ้าเพิ่ม/แก้คำค้น ให้ตรวจและ stage `keywords.csv` แยกต่างหาก ห้ามใช้ `git add -A` ในรอบ publish ข้อมูล หน้าเว็บบน Pages จะอัพเดทในราว 1–2 นาที

workflow `validate.yml` แยก fast validation ออกจาก Windows X-13 exact byte-check; เมื่อ `data/`, `derived/`, `data.js` หรือ `keywords.csv` เปลี่ยนจะเพิ่ม freshness gate และ push ไป `main` จะ deploy Pages ด้วย official Pages Actions เฉพาะเมื่อ gate ที่ต้องใช้ผ่านทั้งหมด (repo ต้องตั้ง Pages source เป็น **GitHub Actions** หนึ่งครั้ง)

## สำหรับ AI agent

repo นี้มี `SKILL.md` เป็นคู่มือปฏิบัติงานสำหรับ AI: เปิด AI agent (Claude Code, Codex ฯลฯ) ในโฟลเดอร์นี้แล้วสั่งงานภาษาคน เช่น "อัพเดทข้อมูลเดือนนี้" หรือ "เพิ่มคำว่า X" ได้เลย agent จะทำตาม workflow และกติกาเหล็กในนั้น (`CLAUDE.md` และ `AGENTS.md` ชี้มาที่ `SKILL.md` ให้อัตโนมัติ)

Agent ต้องเสนอเฉพาะ `toolkit.ps1 setup` / `monthly-prepare` / `monthly-finish` ก่อนเสมอ และขอผู้ใช้เฉพาะสิ่งที่ automation ทำแทนไม่ได้: ตั้ง Chrome ครั้งแรก, กด Import/Start และแก้ CAPTCHA หากพบ ไม่ควรโยนรายชื่อสคริปต์หรือทางทดลองทั้งหมดให้ผู้ใช้เลือก

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

การเพิ่มคำใหม่: ให้ Agent เช็ค `reference/keywords_tried.csv`, เพิ่มแถวใน `keywords.csv` ด้วย ID ที่ไม่ซ้ำ แล้วสร้าง/นำเข้าคิว extension เฉพาะ ID นั้น; ห้ามใช้ pytrends เป็น release path

## ข้อควรรู้เรื่องตัวเลข

- ค่าเป็น Google Trends index 0-100 เทียบภายในช่วงเวลาที่ดึงของแต่ละคำและพื้นที่ **ไม่ใช่จำนวนการค้นจริง** และห้ามเทียบขนาดข้ามคำตรงๆ
- **นโยบายการเก็บ: โหลดยาวสุดเสมอ (2004-01-01 ถึงปัจจุบัน)** ข้อมูลหลักของชุดนี้คือ long horizon
- **ระดับจังหวัดใช้ได้ตั้งแต่ 2014-01 เท่านั้น** Google ปรับระบบระบุตำแหน่งช่วง 2011-2013 (จังหวัดชุดท้ายเริ่มต่อเนื่อง 2013-07) ข้อมูลจังหวัดก่อนหน้านั้นเป็นรู/break เครื่องมือทุกตัวตัดทิ้งให้อัตโนมัติ ระดับประเทศไม่ตัด
- ข้อมูลตั้งต้น seed จากชุด long horizon ของโปรเจกต์เดิม (ดึง 2026-05): ประเทศ 2004-01 ถึง 2026-04 ครบ 50 คำ, จังหวัด 2014-01 ถึง 2026-04; คู่ที่ยังไม่มีซีรีส์ต้องเก็บซ้ำและยืนยัน no-data ก่อนตีความ และไม่ใช่หลักฐานว่าไม่มีอุปสงค์แรงงาน

## ที่มา

ผู้จัดทำ: Nitisart Srijunpho, งานสหกิจศึกษา ธนาคารแห่งประเทศไทย สำนักงานภาคตะวันออกเฉียงเหนือ (2026)
