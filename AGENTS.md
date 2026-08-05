# Agent instructions

คู่มือปฏิบัติงานฉบับเต็มของ repo นี้อยู่ที่ **`SKILL.md`** อ่านไฟล์นั้นก่อนทำงานทุกครั้ง
กติกาเหล็กที่ย้ำซ้ำตรงนี้: ห้ามแก้ `data/` และ `data.js` ด้วยมือ (production ต้องผ่าน extension + `collector/ingest.py`) และห้ามต่อท่อนข้อมูล Google Trends คนละช่วงเวลาเข้าซีรีส์เดียว

ใช้ `scripts/toolkit.ps1` เป็น entrypoint ทางการ: `setup`, `monthly-prepare`, `monthly-finish` คำสั่งระดับล่างมีไว้แก้ปัญหา/พัฒนา และ pytrends/browser runner เป็น diagnostic ที่ห้ามใช้กับ canonical data หรือ publish

มี `monthly-run` เพิ่มมาอีกหนึ่ง action ที่รวม prepare + Chrome checkpoint + finish ไว้ในคำสั่งเดียว **แต่มันไว้ให้คนดับเบิลคลิก `run-monthly-loop.cmd` เท่านั้น** เพราะมันหยุดรอ `Read-Host` ให้พิมพ์ `FINISH` Agent ที่รันแบบ non-interactive จะค้างตรงนั้น จึงต้องใช้ `monthly-prepare` แล้วรอผู้ใช้ยืนยัน แล้วค่อย `monthly-finish` เสมอ
