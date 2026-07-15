#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the proven Chrome extension from an AI-friendly Python CLI.

This is a control plane, not a second scraper implementation.  The extension
still owns navigation, retries, CAPTCHA detection, download validation and
no-data proof.  This runner gives terminal agents four stable operations:

  python collector/browser_runner.py --plan
  python collector/browser_runner.py --start
  python collector/browser_runner.py --resume
  python collector/browser_runner.py --status --json

Generate the queue with collector/make_jobs.py before --plan/--start.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXTENSION_DIR = ROOT / "extension"
JOBS_FILE = EXTENSION_DIR / "data" / "jobs.json"
INCOMING_DIR = ROOT / "incoming"
RUNNER_DIR = ROOT / ".browser-runner"
PROFILE_DIR = RUNNER_DIR / "profile"
DOWNLOADS_DIR = RUNNER_DIR / "downloads"
CAPTURED_DIR = RUNNER_DIR / "captured"
STATUS_FILE = RUNNER_DIR / "status.json"

STATE_KEY = "scraper_state_v1"
BROWSER_RUNNER_MODE_KEY = "browser_runner_mode_v1"
BROWSER_RUNNER_ACK_KEY = "browser_runner_download_ack_v1"
STATUS_SCHEMA = "google-trends-toolkit/browser-runner-status-v1"
CANONICAL_START = "2004-01-01"
SAFE_DOWNLOAD_RE = re.compile(
    r"^(?:[A-Z]{2}\d{3}__(?:TH|TH-\d{2})\.csv|no_data_manifest__\d{4}-\d{2}-\d{2}\.json)$"
)
REQUIRED_JOB_FIELDS = {
    "job_id", "keyword_id", "keyword", "geo_code", "timeframe", "filename"
}


class RunnerError(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def eprint(message):
    print(message, file=sys.stderr, flush=True)


def load_jobs(path=JOBS_FILE):
    try:
        jobs = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerError(
            "ไม่พบ extension/data/jobs.json; รัน collector/make_jobs.py ก่อน"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"jobs.json ไม่ใช่ JSON ที่ถูกต้อง: {exc}") from exc
    return jobs


def validate_jobs(jobs, canonical_end=None):
    """Fail closed before opening Google Trends."""
    canonical_end = canonical_end or str(date.today())
    expected_timeframe = f"{CANONICAL_START} {canonical_end}"
    if not isinstance(jobs, list) or not jobs:
        raise RunnerError("jobs.json ต้องเป็นรายการที่มีอย่างน้อย 1 job")

    job_ids = set()
    filenames = set()
    keywords = set()
    geos = set()
    for index, job in enumerate(jobs, 1):
        if not isinstance(job, dict):
            raise RunnerError(f"job ลำดับ {index} ต้องเป็น object")
        missing = REQUIRED_JOB_FIELDS - set(job)
        if missing:
            raise RunnerError(f"job ลำดับ {index} ขาด fields: {sorted(missing)}")
        if job["timeframe"] != expected_timeframe:
            raise RunnerError(
                f"{job['job_id']} ใช้ timeframe {job['timeframe']!r}; "
                f"ต้องเป็น {expected_timeframe!r} เท่านั้น"
            )
        expected_filename = f"{job['keyword_id']}__{job['geo_code']}.csv"
        if job["filename"] != expected_filename or not SAFE_DOWNLOAD_RE.fullmatch(job["filename"]):
            raise RunnerError(
                f"{job['job_id']} filename ไม่ผ่าน fail-closed schema: {job['filename']!r}"
            )
        if job["job_id"] in job_ids:
            raise RunnerError(f"job_id ซ้ำ: {job['job_id']}")
        if job["filename"] in filenames:
            raise RunnerError(f"filename ซ้ำ: {job['filename']}")
        job_ids.add(job["job_id"])
        filenames.add(job["filename"])
        keywords.add(job["keyword_id"])
        geos.add(job["geo_code"])

    return {
        "total": len(jobs),
        "keywords": len(keywords),
        "geos": len(geos),
        "timeframe": expected_timeframe,
    }


def summarize_state(state):
    jobs = state.get("jobs", []) if isinstance(state, dict) else []
    counts = {
        "total": len(jobs),
        "done": 0,
        "failed": 0,
        "no_data": 0,
        "retry": 0,
        "pending": 0,
        "running": 0,
    }
    for job in jobs:
        status = job.get("status") or "PENDING"
        key = {
            "DONE": "done",
            "FAILED": "failed",
            "NO_DATA": "no_data",
            "RETRY": "retry",
            "RUNNING": "running",
        }.get(status, "pending")
        counts[key] += 1

    processed = counts["done"] + counts["failed"] + counts["no_data"]
    complete = bool(counts["total"]) and processed == counts["total"]
    cursor = state.get("cursor", 0) if isinstance(state, dict) else 0
    current = None
    if jobs:
        running = next((job for job in jobs if job.get("status") == "RUNNING"), None)
        candidate = running or jobs[min(max(int(cursor or 0), 0), len(jobs) - 1)]
        current = {
            "job_id": candidate.get("job_id"),
            "keyword_id": candidate.get("keyword_id"),
            "geo_code": candidate.get("geo_code"),
            "status": candidate.get("status", "PENDING"),
            "error": candidate.get("error"),
        }

    status = state.get("status", "not_started") if isinstance(state, dict) else "not_started"
    return {
        "extension_status": status,
        "counts": counts,
        "current_job": current,
        "complete": complete,
        "successful": complete and counts["failed"] == 0,
        "human_action_required": bool(
            isinstance(state, dict) and (state.get("captcha_tab_id") or status == "paused")
        ),
        "fatal_error": state.get("fatal_error") if isinstance(state, dict) else None,
        "jobs_source": state.get("jobs_source") if isinstance(state, dict) else None,
    }


def pid_is_running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, PermissionError):
        return False
    return True


def write_status(summary, mode, message=None):
    RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": STATUS_SCHEMA,
        "updated_at": now_iso(),
        "pid": os.getpid(),
        "runner_mode": mode,
        **summary,
    }
    if message:
        payload["message"] = message
    temporary = STATUS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATUS_FILE)
    return payload


def read_status():
    if not STATUS_FILE.exists():
        return {
            "schema": STATUS_SCHEMA,
            "extension_status": "not_started",
            "runner_active": False,
            "message": "ยังไม่มี browser runner state; ใช้ --plan แล้ว --start",
        }
    try:
        payload = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"อ่าน runner status ไม่ได้: {exc}") from exc
    payload["runner_active"] = pid_is_running(payload.get("pid"))
    return payload


def print_payload(payload, as_json=False):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if "timeframe" in payload:
        print(
            f"Queue: {payload['total']} jobs | {payload['keywords']} คำ | "
            f"{payload['geos']} พื้นที่ | {payload['timeframe']}"
        )
        return
    counts = payload.get("counts") or {}
    print(
        f"Status: {payload.get('extension_status')} | "
        f"done {counts.get('done', 0)}/{counts.get('total', 0)} | "
        f"no-data {counts.get('no_data', 0)} | failed {counts.get('failed', 0)} | "
        f"runner {'active' if payload.get('runner_active') else 'inactive'}"
    )
    if payload.get("message"):
        print(payload["message"])


def read_extension_state(page):
    return page.evaluate(
        """async key => {
            const raw = await chrome.storage.local.get(key);
            return raw[key] || null;
        }""",
        STATE_KEY,
    )


def validate_captured_download(path):
    """Use the ingest parser and coverage gate before acknowledging a CSV."""
    try:
        from collector.ingest import (
            load_keyword_map, parse_file, validate_canonical_coverage,
        )
    except ModuleNotFoundError:  # Direct execution from collector/.
        from ingest import load_keyword_map, parse_file, validate_canonical_coverage

    keyword_map, keyword_ids = load_keyword_map()
    kid, geo, points = parse_file(path, keyword_map, keyword_ids)
    validate_canonical_coverage(geo, points)
    return {"keyword_id": kid, "geo_code": geo, "months": len(points)}


class DownloadBridge:
    """Persist Playwright downloads and acknowledge only validated exports."""

    def __init__(self):
        self.controller_page = None

    def set_ack(self, payload):
        if self.controller_page is None:
            raise RunnerError("controller page ยังไม่พร้อมรับ download acknowledgment")
        self.controller_page.evaluate(
            """async payload => {
                await chrome.storage.local.set({ [payload.key]: payload.value });
            }""",
            {"key": BROWSER_RUNNER_ACK_KEY, "value": payload},
        )

    def handle(self, download):
        try:
            filename = download.suggested_filename
            if filename.startswith("no_data_manifest__") and SAFE_DOWNLOAD_RE.fullmatch(filename):
                INCOMING_DIR.mkdir(parents=True, exist_ok=True)
                download.save_as(INCOMING_DIR / filename)
                eprint(f"[download] saved {filename}")
                return
            if filename != "multiTimeline.csv":
                eprint(f"[download] ปฏิเสธชื่อไฟล์นอก schema: {filename}")
                return

            state = read_extension_state(self.controller_page)
            running = [job for job in state.get("jobs", []) if job.get("status") == "RUNNING"]
            if len(running) != 1:
                raise RunnerError(
                    f"จับคู่ download ไม่ได้: ต้องมี RUNNING 1 job แต่พบ {len(running)}"
                )
            job = running[0]
            expected = job.get("filename")
            if not expected or not SAFE_DOWNLOAD_RE.fullmatch(expected):
                raise RunnerError(f"expected filename ไม่ผ่าน schema: {expected!r}")

            CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
            captured = CAPTURED_DIR / expected
            download.save_as(captured)
            size = captured.stat().st_size
            ack = {
                "filename": expected,
                "observed_at": now_iso(),
                "bytes": size,
            }
            if 0 < size < 200:
                ack.update({"status": "no_data_candidate", "reason": f"NO_DATA_EXPORT_{size}B"})
                self.set_ack(ack)
                eprint(f"[download] {expected}: tiny export {size}B; รอ no-data confirmation")
                return

            try:
                details = validate_captured_download(captured)
            except ValueError as exc:
                ack.update({"status": "invalid", "reason": str(exc)[:500]})
                self.set_ack(ack)
                eprint(f"[download] ปฏิเสธ {expected}: {exc}")
                return

            INCOMING_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(captured, INCOMING_DIR / expected)
            ack.update({"status": "valid", **details})
            self.set_ack(ack)
            eprint(f"[download] validated and saved {expected} ({details['months']} เดือน)")
        except Exception as exc:  # Playwright event callbacks must not kill the queue.
            eprint(f"[download] save failed: {exc}")


def attach_download_handler(page, bridge):
    page.on("download", bridge.handle)


def enable_browser_runner_mode(page):
    page.evaluate(
        """async keys => {
            await chrome.storage.local.set({
                [keys.mode]: true,
                [keys.ack]: null,
            });
        }""",
        {"mode": BROWSER_RUNNER_MODE_KEY, "ack": BROWSER_RUNNER_ACK_KEY},
    )


def open_controller(playwright):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        context = playwright.chromium.launch_persistent_context(
            PROFILE_DIR,
            channel="chromium",
            headless=False,
            no_viewport=True,
            accept_downloads=True,
            downloads_path=DOWNLOADS_DIR,
            args=[
                f"--disable-extensions-except={EXTENSION_DIR}",
                f"--load-extension={EXTENSION_DIR}",
                "--start-maximized",
            ],
        )
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message:
            message += "\nติดตั้งครั้งเดียวด้วย: py -m playwright install chromium"
        raise RunnerError(f"เปิด Playwright Chromium ไม่สำเร็จ: {message}") from exc

    bridge = DownloadBridge()
    for existing in context.pages:
        attach_download_handler(existing, bridge)
    context.on("page", lambda opened: attach_download_handler(opened, bridge))

    try:
        workers = context.service_workers
        worker = workers[0] if workers else context.wait_for_event("serviceworker", timeout=15000)
        extension_id = worker.url.split("/")[2]
        page = context.new_page()
        page.goto(f"chrome-extension://{extension_id}/controller.html")
        page.wait_for_selector("#status-pill", timeout=15000)
        page.wait_for_function(
            """async key => Boolean((await chrome.storage.local.get(key))[key])""",
            arg=STATE_KEY,
            timeout=15000,
        )
        bridge.controller_page = page
        return context, page
    except Exception:
        context.close()
        raise


def start_fresh_queue(page, expected_total):
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#btn-load-jobs").click()
    page.wait_for_function(
        """([key, total]) => new Promise(resolve => {
            chrome.storage.local.get(key).then(raw => {
                const state = raw[key];
                resolve(Boolean(state && state.jobs && state.jobs.length === total));
            });
        })""",
        arg=[STATE_KEY, expected_total],
        timeout=15000,
    )
    # The click handler updates storage before its in-page state/UI finishes.
    # Wait for the UI too, otherwise Start could race a queue-source change.
    page.wait_for_function(
        "total => Number(document.querySelector('#stat-total').textContent) === total",
        arg=expected_total,
        timeout=15000,
    )
    page.locator("#btn-start").click()


def resume_queue(page):
    state = read_extension_state(page)
    summary = summarize_state(state)
    if summary["complete"]:
        return
    if summary["fatal_error"]:
        raise RunnerError(f"extension อยู่ใน fatal state: {summary['fatal_error']}")
    if summary["extension_status"] == "idle":
        page.locator("#btn-start").click()
    else:
        page.locator("#btn-resume").click()


def monitor_queue(page, mode, poll_seconds=2.0):
    last_marker = None
    captcha_announced = False
    while True:
        state = read_extension_state(page)
        summary = summarize_state(state)
        payload = write_status(summary, mode)
        counts = summary["counts"]
        marker = (
            summary["extension_status"], counts["done"], counts["no_data"],
            counts["failed"], counts["retry"], counts["running"], counts["pending"],
        )
        if marker != last_marker:
            eprint(
                f"[{summary['extension_status']}] done {counts['done']}/{counts['total']} | "
                f"no-data {counts['no_data']} | failed {counts['failed']} | "
                f"retry/pending {counts['retry'] + counts['pending']}"
            )
            last_marker = marker

        if summary["fatal_error"] or summary["extension_status"] == "fatal":
            return 1, write_status(summary, mode, "extension fatal; ตรวจ fatal_error")
        if summary["human_action_required"]:
            if not captcha_announced:
                eprint("[action required] แก้ CAPTCHA ในแท็บที่เปิด แล้วกด Resume ใน Controller")
                captcha_announced = True
            time.sleep(poll_seconds)
            continue
        captcha_announced = False

        if summary["complete"] and summary["extension_status"] == "idle":
            message = (
                "queue complete; พร้อม ingest --dry-run"
                if summary["successful"]
                else "queue complete แต่มี FAILED jobs; ห้าม ingest/publish จน retry สำเร็จ"
            )
            return (0 if summary["successful"] else 1), write_status(summary, mode, message)

        if summary["extension_status"] == "idle" and not summary["complete"]:
            return 2, write_status(
                summary, mode, "runner หยุดก่อนคิวจบ; ใช้ --resume เพื่อทำต่อ"
            )
        time.sleep(poll_seconds)


def run_browser(mode, expected_total, poll_seconds):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RunnerError(
            "ยังไม่มี Playwright; รัน `py -m pip install -r requirements.txt` แล้ว "
            "`py -m playwright install chromium`"
        ) from exc

    with sync_playwright() as playwright:
        context, page = open_controller(playwright)
        try:
            enable_browser_runner_mode(page)
            if mode == "start":
                start_fresh_queue(page, expected_total)
            else:
                resume_queue(page)
            return monitor_queue(page, mode, poll_seconds)
        except KeyboardInterrupt:
            summary = summarize_state(read_extension_state(page))
            return 130, write_status(
                summary, mode, "ผู้ใช้หยุด browser runner; ใช้ --resume เพื่อทำต่อ"
            )
        finally:
            context.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="ตรวจ queue โดยไม่เปิด browser")
    action.add_argument("--start", action="store_true", help="reset state แล้วเริ่ม queue ปัจจุบัน")
    action.add_argument("--resume", action="store_true", help="ทำ queue ใน persistent profile ต่อ")
    action.add_argument("--status", action="store_true", help="อ่าน status snapshot โดยไม่เปิด browser")
    parser.add_argument("--json", action="store_true", help="พิมพ์ผลแบบ machine-readable JSON")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.poll_seconds < 0.2:
        parser.error("--poll-seconds ต้องไม่น้อยกว่า 0.2")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.status:
            print_payload(read_status(), args.json)
            return 0

        jobs = load_jobs()
        plan = validate_jobs(jobs)
        if args.plan:
            print_payload(plan, args.json)
            return 0

        mode = "start" if args.start else "resume"
        code, payload = run_browser(mode, plan["total"], args.poll_seconds)
        print_payload(payload, args.json)
        return code
    except RunnerError as exc:
        payload = {"error": str(exc), "ok": False}
        print_payload(payload, args.json) if args.json else eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
