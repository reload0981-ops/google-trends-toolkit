// content.js โ€” runs in every https://trends.google.com/trends/explore* tab
//
// On message RUN_JOB:
//   1. detect 429 / block page
//   2. wait for the timeseries chart to render
//   3. tell background to expect a download (with the project filename)
//   4. click the CSV download button
//   5. report DONE or ERROR back to the controller

const CHART_SELECTORS_TIMESERIES = [
  'widget[type="fe_line_chart"]',
  '[widget-name="TIMESERIES"]',
  'line-chart-directive',
  'div.fe-line-chart'
];

const CSV_SELECTORS_TIMESERIES = [
  'widget[type="fe_line_chart"] button.widget-actions-item.export',
  'widget[type="fe_line_chart"] [aria-label*="CSV"]',
  '[widget-name="TIMESERIES"] [aria-label*="CSV"]',
  '[widget-name="TIMESERIES"] button.widget-actions-item.export',
  'button.widget-actions-item.export',
  'button[aria-label="CSV"]',
  '[aria-label*="Download"]',
  '[aria-label*="download"]',
  '[title*="CSV"]',
  '[title*="Download"]'
];

// GeoMap (interest by subregion) widget — same Explore page, different widget.
const CHART_SELECTORS_GEOMAP = [
  'widget[type="fe_geo_chart"]',
  '[widget-name="GEO_MAP"]',
  'div.fe-geo-chart-generated'
];

const CSV_SELECTORS_GEOMAP = [
  'widget[type="fe_geo_chart"] button.widget-actions-item.export',
  'widget[type="fe_geo_chart"] [aria-label*="CSV"]',
  '[widget-name="GEO_MAP"] [aria-label*="CSV"]',
  '[widget-name="GEO_MAP"] button.widget-actions-item.export'
];

// Default = timeseries (back-compat). Re-assigned per-job in runJob().
let CHART_SELECTORS = CHART_SELECTORS_TIMESERIES;
let CSV_SELECTORS = CSV_SELECTORS_TIMESERIES;

const THAI_QUERY_NO_VOLUME_PHRASES = [
  "\u0e01\u0e32\u0e23\u0e04\u0e49\u0e19\u0e2b\u0e32\u0e02\u0e2d\u0e07\u0e04\u0e38\u0e13\u0e44\u0e21\u0e48\u0e21\u0e35\u0e02\u0e49\u0e2d\u0e21\u0e39\u0e25\u0e40\u0e1e\u0e35\u0e22\u0e07\u0e1e\u0e2d\u0e17\u0e35\u0e48\u0e08\u0e30\u0e41\u0e2a\u0e14\u0e07\u0e17\u0e35\u0e48\u0e19\u0e35\u0e48",
  "\u0e44\u0e21\u0e48\u0e21\u0e35\u0e02\u0e49\u0e2d\u0e21\u0e39\u0e25\u0e40\u0e1e\u0e35\u0e22\u0e07\u0e1e\u0e2d\u0e17\u0e35\u0e48\u0e08\u0e30\u0e41\u0e2a\u0e14\u0e07\u0e17\u0e35\u0e48\u0e19\u0e35\u0e48",
  "\u0e42\u0e1b\u0e23\u0e14\u0e15\u0e23\u0e27\u0e08\u0e2a\u0e2d\u0e1a\u0e27\u0e48\u0e32\u0e17\u0e38\u0e01\u0e04\u0e33\u0e2a\u0e30\u0e01\u0e14\u0e16\u0e39\u0e01\u0e15\u0e49\u0e2d\u0e07\u0e41\u0e25\u0e49\u0e27",
  "\u0e25\u0e2d\u0e07\u0e43\u0e0a\u0e49\u0e04\u0e33\u0e17\u0e31\u0e48\u0e27\u0e44\u0e1b\u0e01\u0e27\u0e48\u0e32\u0e19\u0e35\u0e49"
];

const EN_NO_VOLUME_PHRASES = [
  "not enough search volume",
  "not enough data",
  "no data available"
];

// Escaped Thai detector literals survive editor/encoding conversions intact.
const THAI_CAPTCHA_PHRASES = [
  "\u0e22\u0e37\u0e19\u0e22\u0e31\u0e19\u0e27\u0e48\u0e32\u0e04\u0e38\u0e13\u0e44\u0e21\u0e48\u0e43\u0e0a\u0e48\u0e2b\u0e38\u0e48\u0e19\u0e22\u0e19\u0e15\u0e4c",
  "\u0e23\u0e30\u0e1a\u0e1a\u0e02\u0e2d\u0e07\u0e40\u0e23\u0e32\u0e15\u0e23\u0e27\u0e08\u0e1e\u0e1a\u0e01\u0e32\u0e23\u0e40\u0e02\u0e49\u0e32\u0e43\u0e0a\u0e49\u0e07\u0e32\u0e19\u0e17\u0e35\u0e48\u0e1c\u0e34\u0e14\u0e1b\u0e01\u0e15\u0e34"
];
const THAI_SOFT_ERROR = "\u0e40\u0e01\u0e34\u0e14\u0e02\u0e49\u0e2d\u0e1c\u0e34\u0e14\u0e1e\u0e25\u0e32\u0e14\u0e1a\u0e32\u0e07\u0e2d\u0e22\u0e48\u0e32\u0e07";

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function compactText(s) {
  return (s || "")
    .normalize("NFC")
    .replace(/[\s\u200B-\u200D\uFEFF]+/g, "")
    .toLowerCase();
}

function includesCompact(text, phrases) {
  const compact = compactText(text);
  return phrases.some(p => compact.includes(compactText(p)));
}

function isForegroundPage() {
  return document.visibilityState === "visible" && !document.hidden;
}

async function waitUntilForeground(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (isForegroundPage()) return true;
    await sleep(500);
  }
  return isForegroundPage();
}

function detectBlock() {
  const title = document.title.toLowerCase();
  const bodyText = (document.body && document.body.innerText || "").slice(0, 4000);
  const lower = bodyText.toLowerCase();
  // CAPTCHA / "/sorry/" challenge page โ€” check BEFORE 429 so we don't misclassify
  if (location.href.includes("/sorry/") ||
      lower.includes("i'm not a robot") ||
      lower.includes("recaptcha") ||
      THAI_CAPTCHA_PHRASES.some(phrase => bodyText.includes(phrase))) return "CAPTCHA";
  if (title.includes("429") || title.includes("too many requests")) return "HTTP_429";
  if (lower.includes("unusual traffic")) return "UNUSUAL_TRAFFIC";
  if (lower.includes("we're sorry") && lower.includes("too many requests")) return "HTTP_429";
  // Google Trends in-app soft error (chart backend fail / soft throttle):
  //   EN: "Something went wrong. Please try again in a moment."
  // Thai equivalent is matched by THAI_SOFT_ERROR above.
  if (bodyText.includes(THAI_SOFT_ERROR) ||
      lower.includes("something went wrong") ||
      lower.includes("please try again in a moment")) return "SOFT_ERROR";
  return null;
}

function findChartElement() {
  for (const sel of CHART_SELECTORS) {
    const el = document.querySelector(sel);
    if (el) return el;
  }
  return null;
}

function detectNoVolume() {
  if (findChartElement()) return null;
  const pageText = (document.body && document.body.innerText || "").slice(0, 12000);

  // Query-level Thai no-volume message. Google can render this outside the
  // TIMESERIES widget, so match it against compact page text first.
  if (includesCompact(pageText, THAI_QUERY_NO_VOLUME_PHRASES.slice(0, 2))) {
    return "NO_VOLUME";
  }

  const timeseries = document.querySelector('widget[type="fe_line_chart"], [widget-name="TIMESERIES"]');
  if (!timeseries) return null;

  const bodyText = (timeseries.innerText || "").slice(0, 5000);
  const lower = bodyText.toLowerCase();
  if (includesCompact(bodyText, THAI_QUERY_NO_VOLUME_PHRASES) ||
      EN_NO_VOLUME_PHRASES.some(p => lower.includes(p))) {
    return "NO_VOLUME";
  }
  return null;
}
async function waitForChart(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastBlockPollAt = 0;
  let softErrorSince = 0;
  let noVolumeSince = 0;
  while (Date.now() < deadline) {
    const existingChart = findChartElement();
    if (existingChart) {
      // give chart time to actually render
      await sleep(2500);
      return { chart: existingChart, blocked: null, noData: null };
    }

    const now = Date.now();
    if (now - lastBlockPollAt >= 5000) {
      lastBlockPollAt = now;
      const block = detectBlock();
      if (block && block !== "SOFT_ERROR") {
        return { chart: null, blocked: block, noData: null };
      }
      if (block === "SOFT_ERROR") {
        softErrorSince = softErrorSince || now;
        if (now - softErrorSince >= 15000) {
          return { chart: null, blocked: block, noData: null };
        }
      } else {
        softErrorSince = 0;
      }
      const novol = detectNoVolume();
      if (novol) {
        noVolumeSince = noVolumeSince || now;
        if (now - noVolumeSince >= 15000) {
          return { chart: null, blocked: null, noData: novol };
        }
      } else {
        noVolumeSince = 0;
      }
    }
    for (const sel of CHART_SELECTORS) {
      const el = document.querySelector(sel);
      if (el) {
        // give chart time to actually render
        await sleep(2500);
        return { chart: el, blocked: null, noData: null };
      }
    }
    await sleep(800);
  }
  return { chart: null, blocked: null, noData: null };
}

function isClickableCandidate(el) {
  if (!el || !el.getBoundingClientRect) return false;
  const style = window.getComputedStyle(el);
  if (style.display === "none" || style.visibility === "hidden" || style.pointerEvents === "none") {
    return false;
  }
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function findCSVButton() {
  for (const sel of CSV_SELECTORS) {
    const el = document.querySelector(sel);
    if (el && isClickableCandidate(el)) return el;
  }
  const candidates = Array.from(document.querySelectorAll('button,[role="button"],a'));
  for (const el of candidates) {
    const label = [
      el.getAttribute("aria-label") || "",
      el.getAttribute("title") || "",
      el.textContent || "",
      el.className || ""
    ].join(" ").toLowerCase();
    if (isClickableCandidate(el) && (label.includes("csv") || label.includes("download") || label.includes("export"))) {
      return el;
    }
  }
  return null;
}

async function revealWidgetActions(chartEl) {
  const widget = chartEl && chartEl.closest ? (chartEl.closest("widget") || chartEl) : chartEl;
  if (!widget || !widget.getBoundingClientRect) return;
  try {
    widget.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(700);
    const r = widget.getBoundingClientRect();
    const x = r.left + Math.min(Math.max(20, r.width - 40), Math.max(20, r.width * 0.9));
    const y = r.top + Math.min(40, Math.max(10, r.height * 0.15));
    for (const type of ["mouseenter", "mouseover", "mousemove"]) {
      const ev = new MouseEvent(type, {
        bubbles: true, cancelable: true, view: window,
        clientX: x, clientY: y
      });
      widget.dispatchEvent(ev);
      chartEl.dispatchEvent(ev);
      document.dispatchEvent(ev);
    }
    await sleep(1200);
  } catch (_) { /* ignore */ }
}

async function humanSettle() {
  // First settle โ€” let page initial render finish + send subtle scroll signal
  await sleep(1200 + Math.random() * 1800);  // 1.2 โ€“ 3.0 s
  try {
    window.scrollBy({ top: 60 + Math.random() * 120, behavior: "smooth" });
    await sleep(700 + Math.random() * 800);
    window.scrollBy({ top: -(40 + Math.random() * 60), behavior: "smooth" });
    await sleep(500 + Math.random() * 700);
  } catch (e) { /* ignore */ }
}

async function humanDwellOnChart(chartEl) {
  // After chart appears, simulate a human "looking at the chart" before clicking CSV.
  // Random total dwell 5โ€“14 sec with scroll & mouseover events on the chart.
  const total = 5000 + Math.random() * 9000;
  const start = Date.now();
  try {
    while (Date.now() - start < total) {
      // Random small scroll
      const dy = (Math.random() - 0.5) * 150;
      window.scrollBy({ top: dy, behavior: "smooth" });
      // Synthetic mousemove over the chart element โ€” gives Google a hover signal
      try {
        if (chartEl && chartEl.getBoundingClientRect) {
          const r = chartEl.getBoundingClientRect();
          const x = r.left + Math.random() * Math.max(40, r.width);
          const y = r.top  + Math.random() * Math.max(40, r.height);
          const ev = new MouseEvent("mousemove", {
            bubbles: true, cancelable: true, view: window,
            clientX: x, clientY: y
          });
          chartEl.dispatchEvent(ev);
          document.dispatchEvent(ev);
        }
      } catch (_) { /* ignore */ }
      await sleep(700 + Math.random() * 1500);
    }
  } catch (e) { /* ignore */ }
}

async function runJob(job) {
  // Switch selectors based on job kind. Backward-compatible: jobs without
  // `kind` field default to timeseries.
  const kind = (job && job.kind) || "timeseries";
  if (kind === "geomap") {
    CHART_SELECTORS = CHART_SELECTORS_GEOMAP;
    CSV_SELECTORS = CSV_SELECTORS_GEOMAP;
  } else {
    CHART_SELECTORS = CHART_SELECTORS_TIMESERIES;
    CSV_SELECTORS = CSV_SELECTORS_TIMESERIES;
  }
  console.log("[content] runJob:", job.job_id, job.keyword, job.geo_code, "kind=" + kind);

  const foreground = await waitUntilForeground(15000);
  if (!foreground) {
    return { result: "ERROR", reason: "PAGE_NOT_FOREGROUND" };
  }

  // Let the page settle before checking anything โ€” soft-error pages often
  // resolve themselves after a few seconds when the backend recovers.
  await humanSettle();

  const earlyBlock = detectBlock();
  if (earlyBlock) {
    return { result: "BLOCKED", reason: earlyBlock };
  }

  const chartWait = await waitForChart(60000);
  if (chartWait.blocked) return { result: "BLOCKED", reason: chartWait.blocked };
  if (chartWait.noData) return { result: "NO_DATA", reason: chartWait.noData };
  const chart = chartWait.chart;
  if (!chart) {
    const late = detectBlock();
    if (late) return { result: "BLOCKED", reason: late };
    const novol = detectNoVolume();
    if (novol) return { result: "NO_DATA", reason: novol };
    return { result: "ERROR", reason: "CHART_TIMEOUT" };
  }

  // Human-like dwell on the chart โ€” view it for several seconds before exporting
  await humanDwellOnChart(chart);
  await revealWidgetActions(chart);

  // Re-detect block AFTER dwell (page state could have shifted during dwell)
  const midBlock = detectBlock();
  if (midBlock) return { result: "BLOCKED", reason: midBlock };

  let btn = findCSVButton();
  if (!btn) {
    // Try once more after a small wait โ€” action bar may render late
    await revealWidgetActions(chart);
    await sleep(2500);
    btn = findCSVButton();
  }
  if (!btn) {
    const novol = detectNoVolume();
    if (novol) return { result: "NO_DATA", reason: novol };
    return { result: "ERROR", reason: "CSV_BUTTON_NOT_FOUND" };
  }

  // Require an acknowledged filename mapping before clicking. Otherwise a
  // stale mapping could overwrite the wrong job's CSV.
  try {
    const prepared = await chrome.runtime.sendMessage({
      cmd: "PREPARE_DOWNLOAD",
      filename: job.filename
    });
    if (!prepared || !prepared.ok) {
      return { result: "ERROR", reason: prepared?.error || "PREPARE_DOWNLOAD_REJECTED" };
    }
  } catch (e) {
    console.warn("[content] PREPARE_DOWNLOAD failed:", e);
    return { result: "ERROR", reason: `PREPARE_DOWNLOAD_FAILED: ${e.message || e}` };
  }

  btn.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(600 + Math.random() * 900);  // small "I'm about to click" pause
  btn.click();

  // Give the download a moment to start
  await sleep(2500 + Math.random() * 1500);

  return { result: "DONE" };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.cmd === "RUN_JOB") {
    runJob(msg.job)
      .then(sendResponse)
      .catch(err => sendResponse({ result: "ERROR", reason: String(err) }));
    return true; // async response
  }
  if (msg && msg.cmd === "PING") {
    sendResponse({ ok: true, url: location.href, title: document.title });
    return false;
  }
});

console.log("[content] script loaded on", location.href);
