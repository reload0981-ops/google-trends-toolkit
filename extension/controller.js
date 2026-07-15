// controller.js - main orchestrator loaded as a long-lived extension tab.
//
// State machine: idle | running | paused | fatal
// Each tick: pick next pending job -> open tab -> message content script ->
// close tab -> validate download -> sleep -> next.

const STATE_KEY = "scraper_state_v1";
const JOBS_SOURCE_KEY = "scraper_jobs_source_v1";
const BROWSER_RUNNER_MODE_KEY = "browser_runner_mode_v1";
const BROWSER_RUNNER_DOWNLOAD_ACK_KEY = "browser_runner_download_ack_v1";
const DEFAULT_JOBS_FILE = "data/jobs.json";
const HIDE_FOREGROUND_WARNING_KEY = "hide_foreground_warning_v1";
const MAX_TABLE_ROWS = 40;
const MIN_VALID_DOWNLOAD_BYTES = 200;
const NO_DATA_EXPORT_REASON = "NO_DATA_EXPORT";
const NO_DATA_MANIFEST_SCHEMA = "google-trends-toolkit/no-data-manifest-v1";
const MIN_NO_DATA_ATTEMPTS = 2;
// chrome.notifications accepts data URLs. This real raster icon replaces the
// missing icons/48.png resource that made CAPTCHA notifications fail.
const NOTIFICATION_ICON_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAAA5UlEQVR42u2Y0Q6DIAxFYeEL5Tv5RvZEZlxVsJe0bJcnAyaeQ1tojGGrNSw8XmHxQQEKUIACFFCN5Bmuls9zzPI70eNNvAf/As6sAbvdl9YZAQoMpM9PFnFaZffP7oG02rnvrgY08OYCWnhTAQQ8rAZ6mq4Z8NAItA/Xcg2IhIcINKAj2J0IAl7dTo8AzoBXRQAFb9ILSfCPUyArozeSQme7LkE8acymRmAEvs1La20OAd8dAUTK1IKDHooAKt9nwN8KIIvV5BQ6wnqD70ohdNGZ3ANe4flXggIUoAAFKEABCvy7wBulk0dExkEezwAAAABJRU5ErkJggg==";

// ----- DOM helpers ---------------------------------------------------------
const $ = id => document.getElementById(id);
const els = {
  btnStart: $("btn-start"),
  btnPause: $("btn-pause"),
  btnResume: $("btn-resume"),
  btnStop: $("btn-stop"),
  btnSkip: $("btn-skip"),
  btnReset: $("btn-reset"),
  btnReflag: $("btn-reflag"),
  btnRetryFailed: $("btn-retry-failed"),
  btnReconcileDownloads: $("btn-reconcile-downloads"),
  btnCopyDebug: $("btn-copy-debug"),
  btnClearLog: $("btn-clear-log"),
  btnRefocusWindow: $("btn-refocus-window"),
  jobsSourceSelect: $("jobs-source-select"),
  jobsSourceCurrent: $("jobs-source-current"),
  btnLoadJobs: $("btn-load-jobs"),
  btnImportJobs: $("btn-import-jobs"),
  jobsFileInput: $("jobs-file-input"),
  reflagInput: $("reflag-input"),
  throttleMin: $("throttle-min"),
  throttleMax: $("throttle-max"),
  longEvery: $("long-every"),
  longBreak: $("long-break"),
  maxRetries: $("max-retries"),
  loadTimeout: $("load-timeout"),
  warmEvery: $("warm-every"),
  warmDwell: $("warm-dwell"),
  fgWarning: $("foreground-warning"),
  btnHideWarning: $("btn-hide-warning"),
  statusPill: $("status-pill"),
  barFill: $("bar-fill"),
  progressText: $("progress-text"),
  statDone: $("stat-done"),
  statFailed: $("stat-failed"),
  statNoData: $("stat-nodata"),
  statRetry: $("stat-retry"),
  statPending: $("stat-pending"),
  statTotal: $("stat-total"),
  jobFilter: $("job-filter"),
  jobCountLabel: $("job-count-label"),
  geoSummary: $("geo-summary"),
  jobTableBody: $("job-table-body"),
  currentJob: $("current-job"),
  log: $("log")
};

function log(line, cls = "info") {
  const ts = new Date().toTimeString().slice(0, 8);
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = `${ts}  ${line}`;
  els.log.appendChild(div);
  els.log.scrollTop = els.log.scrollHeight;

  const fn = cls === "err" ? console.error : cls === "warn" ? console.warn : console.log;
  fn(`[ctrl] ${line}`);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitOrPause(ms) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (abortRequested || (state && state.status === "paused")) return;
    await sleep(Math.min(1000, deadline - Date.now()));
  }
}

function randInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function urlEncode(s) {
  return encodeURIComponent(s);
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function basename(path) {
  return String(path || "").split(/[\\/]/).pop().toLowerCase();
}

function localIsoTimestamp(value = new Date()) {
  const pad = number => String(number).padStart(2, "0");
  const offsetMinutes = -value.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const offset = Math.abs(offsetMinutes);
  return (
    `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}` +
    `T${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}` +
    `${sign}${pad(Math.floor(offset / 60))}:${pad(offset % 60)}`
  );
}

function localDateString(value = new Date()) {
  return localIsoTimestamp(value).slice(0, 10);
}

function downloadSize(dl) {
  if (typeof dl.fileSize === "number") return dl.fileSize;
  if (typeof dl.totalBytes === "number") return dl.totalBytes;
  return 0;
}

function buildExploreUrl(job) {
  const params = [
    `q=${urlEncode(job.keyword)}`,
    "date=all",
    `geo=${urlEncode(job.geo_code)}`,
    "hl=th"
  ].join("&");
  return `https://trends.google.co.th/explore?${params}`;
}

// ----- State management ---------------------------------------------------
let state = null;

async function loadState() {
  const raw = await chrome.storage.local.get(STATE_KEY);
  state = raw[STATE_KEY] || null;
}

function getCounts() {
  const counts = {
    total: state && state.jobs ? state.jobs.length : 0,
    done: 0,
    failed: 0,
    noData: 0,
    retry: 0,
    pending: 0,
    running: 0
  };
  if (!state || !state.jobs) return counts;

  for (const job of state.jobs) {
    const status = job.status || "PENDING";
    if (status === "DONE") counts.done += 1;
    else if (status === "FAILED") counts.failed += 1;
    else if (status === "NO_DATA") counts.noData += 1;
    else if (status === "RETRY") counts.retry += 1;
    else if (status === "RUNNING") counts.running += 1;
    else counts.pending += 1;
  }
  return counts;
}

function syncStateCounters() {
  if (!state || !state.jobs) return;
  const counts = getCounts();
  state.completed = counts.done;
  state.failed = counts.failed + counts.noData;
}

async function saveState() {
  syncStateCounters();
  await chrome.storage.local.set({ [STATE_KEY]: state });
}

async function getActiveJobsFile() {
  const { [JOBS_SOURCE_KEY]: file } = await chrome.storage.local.get(JOBS_SOURCE_KEY);
  return file || DEFAULT_JOBS_FILE;
}

async function setActiveJobsFile(file) {
  await chrome.storage.local.set({ [JOBS_SOURCE_KEY]: file });
}

function validateJobs(jobs) {
  if (!Array.isArray(jobs) || jobs.length === 0) {
    throw new Error("jobs.json must contain a non-empty array");
  }
  const expectedTimeframe = `2004-01-01 ${localDateString()}`;
  const validGeos = new Set(["TH", "TH-30", "TH-31", "TH-34", "TH-40", "TH-41"]);
  const jobIds = new Set();
  const filenames = new Set();

  for (const [index, job] of jobs.entries()) {
    const label = `job ${index + 1}`;
    if (!job || typeof job !== "object" || Array.isArray(job)) {
      throw new Error(`${label} must be an object`);
    }
    if (!/^j\d{4,}$/.test(job.job_id || "") || jobIds.has(job.job_id)) {
      throw new Error(`${label} has an invalid or duplicate job_id`);
    }
    if (!/^[A-Z]{2}\d{3}$/.test(job.keyword_id || "") || !(job.keyword || "").trim()) {
      throw new Error(`${label} has an invalid keyword`);
    }
    if (!validGeos.has(job.geo_code)) {
      throw new Error(`${label} has unsupported geo_code ${job.geo_code || "(empty)"}`);
    }
    if (job.timeframe !== expectedTimeframe) {
      throw new Error(`${label} timeframe must be ${expectedTimeframe}`);
    }
    const expectedFilename = `${job.keyword_id}__${job.geo_code}.csv`;
    if (job.filename !== expectedFilename || filenames.has(job.filename)) {
      throw new Error(`${label} has an invalid or duplicate filename`);
    }
    if ((job.kind || "timeseries") !== "timeseries") {
      throw new Error(`${label} kind must be timeseries`);
    }
    jobIds.add(job.job_id);
    filenames.add(job.filename);
  }
  return jobs;
}

async function resetStateFromJobs(jobs, source) {
  validateJobs(jobs);
  state = {
    jobs: jobs.map(j => ({
      ...j,
      status: "PENDING",
      attempts: 0,
      no_data_attempts: 0,
      no_data_observed_at: null,
      error: null
    })),
    status: "idle",
    cursor: 0,
    completed: 0,
    failed: 0,
    started_at: null,
    last_block_at: null,
    scraper_window_id: null,
    captcha_tab_id: null,
    human_action_reason: null,
    fatal_error: null,
    no_data_manifest_exported_at: null,
    jobs_source: source
  };
  await saveState();
}

async function resetState(jobsFilePath = null) {
  const file = jobsFilePath || await getActiveJobsFile();
  const resp = await fetch(chrome.runtime.getURL(file));
  if (!resp.ok) {
    throw new Error(`Failed to load ${file}: HTTP ${resp.status}`);
  }
  const jobs = await resp.json();
  await resetStateFromJobs(jobs, file);
  if (jobsFilePath) {
    await setActiveJobsFile(jobsFilePath);
  }
}

async function importJobsFile(file) {
  if (!file || !file.name.toLowerCase().endsWith(".json")) {
    throw new Error("Select the jobs.json generated by collector/make_jobs.py");
  }
  let jobs;
  try {
    jobs = JSON.parse(await file.text());
  } catch (error) {
    throw new Error(`Invalid JSON: ${error.message || error}`);
  }
  await resetStateFromJobs(jobs, `import:${file.name}`);
}

async function loadJobsIndex() {
  try {
    const resp = await fetch(chrome.runtime.getURL("data/jobs_index.json"));
    if (!resp.ok) return [];
    return await resp.json();
  } catch (e) {
    console.warn("[ctrl] jobs_index.json load failed:", e);
    return [];
  }
}

async function populateJobsSourceUI() {
  if (!els.jobsSourceSelect) return;
  const index = await loadJobsIndex();
  els.jobsSourceSelect.innerHTML = "";
  for (const entry of index) {
    const opt = document.createElement("option");
    opt.value = entry.file;
    opt.textContent = entry.label + (entry.mode ? `  [${entry.mode}]` : "");
    opt.title = entry.description || "";
    els.jobsSourceSelect.appendChild(opt);
  }
  const active = await getActiveJobsFile();
  els.jobsSourceSelect.value = active;
  updateJobsSourceLabel();
}

function updateJobsSourceLabel() {
  if (!els.jobsSourceCurrent) return;
  const file = state && state.jobs_source ? state.jobs_source : "(none loaded)";
  const n = state && state.jobs ? state.jobs.length : 0;
  els.jobsSourceCurrent.textContent = `Active: ${file}  (${n} jobs)`;
}

async function repairRestoredState() {
  if (!state || !state.jobs) return false;
  let changed = false;

  for (const job of state.jobs) {
    if (job.status === "RUNNING") {
      job.status = "RETRY";
      job.error = job.error || "RECOVERED_AFTER_CONTROLLER_RESTART";
      changed = true;
    }
  }

  if (state.status === "running") {
    state.status = "idle";
    changed = true;
  }

  if (!["idle", "running", "paused", "fatal"].includes(state.status)) {
    state.status = "idle";
    changed = true;
  }

  syncStateCounters();
  if (changed) await saveState();
  return changed;
}

async function loadWarningPreference() {
  const raw = await chrome.storage.local.get(HIDE_FOREGROUND_WARNING_KEY);
  if (raw[HIDE_FOREGROUND_WARNING_KEY] && els.fgWarning) {
    els.fgWarning.style.display = "none";
  }
}

function nextPendingIndex(fromCursor = 0) {
  if (!state || !state.jobs) return -1;
  for (let i = fromCursor; i < state.jobs.length; i++) {
    if (state.jobs[i].status === "PENDING" || state.jobs[i].status === "RETRY") {
      return i;
    }
  }
  for (let i = 0; i < fromCursor; i++) {
    if (state.jobs[i].status === "PENDING" || state.jobs[i].status === "RETRY") {
      return i;
    }
  }
  return -1;
}

// ----- Tab/window helpers ------------------------------------------------
async function ensureScraperWindow() {
  if (state && state.scraper_window_id) {
    try {
      await chrome.windows.get(state.scraper_window_id);
      await focusScraperWindow(state.scraper_window_id, "existing");
      return state.scraper_window_id;
    } catch (_) {
      state.scraper_window_id = null;
    }
  }

  const win = await chrome.windows.create({
    url: "about:blank",
    focused: true,
    state: "normal",
    type: "normal",
    width: 980,
    height: 760,
    left: 60,
    top: 60
  });
  state.scraper_window_id = win.id;
  await saveState();
  log(`Opened scraper window (id=${win.id}); keep it in front while jobs run.`, "info");
  return win.id;
}

async function focusScraperWindow(winId, reason = "") {
  try {
    await chrome.windows.update(winId, { focused: true, state: "normal" });
    await sleep(500);
  } catch (e) {
    log(`Could not focus scraper window${reason ? " (" + reason + ")" : ""}: ${e.message || e}`, "warn");
  }
}

async function waitForTabComplete(tabId, timeoutMs) {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
  } catch (_) {
    throw new Error("TAB_NOT_FOUND");
  }

  return new Promise((resolve, reject) => {
    let done = false;
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("TAB_LOAD_TIMEOUT"));
    }, timeoutMs);

    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") {
        if (done) return;
        done = true;
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function sendJobWithRetry(tabId, job, maxRetries = 3) {
  let lastErr = null;
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await chrome.tabs.sendMessage(tabId, { cmd: "RUN_JOB", job });
    } catch (e) {
      lastErr = e;
      await sleep(700);
    }
  }
  throw lastErr || new Error("SEND_FAILED");
}

async function closeTab(tabId) {
  try {
    await chrome.tabs.remove(tabId);
  } catch (_) {
    // Tab may already be closed.
  }
}

// ----- Settings -----------------------------------------------------------
function readSettings() {
  const throttleMin = Math.max(10, +els.throttleMin.value || 60);
  const throttleMax = Math.max(throttleMin, +els.throttleMax.value || throttleMin);
  return {
    throttleMin,
    throttleMax,
    longEvery: Math.max(0, +els.longEvery.value || 0),
    longBreak: Math.max(0, +els.longBreak.value || 0),
    maxRetries: Math.max(0, +els.maxRetries.value || 0),
    loadTimeout: Math.max(10, +els.loadTimeout.value || 45),
    warmEvery: Math.max(0, +els.warmEvery.value || 0),
    warmDwell: Math.max(0, +els.warmDwell.value || 30)
  };
}

// Warming visit: open Google Trends homepage, dwell, close.
async function warmingVisit(dwellSec) {
  if (dwellSec <= 0) return;
  log(`Warming visit: trends.google.co.th homepage (${dwellSec}s)`, "info");
  let tab = null;
  try {
    const winId = await ensureScraperWindow();
    await focusScraperWindow(winId, "warming");
    tab = await chrome.tabs.create({
      url: "https://trends.google.co.th/home?geo=TH&hl=th",
      active: true,
      windowId: winId
    });
    await waitForTabComplete(tab.id, 30000);
  } catch (e) {
    log(`Warming load issue: ${e.message || e}`, "warn");
  }

  try {
    if (tab) {
      await chrome.tabs.update(tab.id, { active: true });
      await focusScraperWindow(tab.windowId, "warming dwell");
      await waitOrPause(dwellSec * 1000);
      await closeTab(tab.id);
    }
  } catch (_) {
    // Ignore cleanup failures.
  }
  log("Warming visit done", "info");
}

// ----- UI rendering -------------------------------------------------------
function statusClass(status) {
  if (status === "running") return "running";
  if (status === "paused") return "paused";
  if (status === "fatal") return "fatal";
  return "idle";
}

function renderStatus() {
  if (!state) return;
  const label = loopRunning && state.status === "idle" ? "STOPPING" : state.status.toUpperCase();
  els.statusPill.textContent = label;
  els.statusPill.className = `pill ${statusClass(state.status)}`;
}

function renderStats() {
  if (!state) return;
  const counts = getCounts();
  const processed = counts.done + counts.failed + counts.noData;
  const pct = counts.total ? Math.round((processed * 100) / counts.total) : 0;

  els.statTotal.textContent = counts.total;
  els.statDone.textContent = counts.done;
  els.statFailed.textContent = counts.failed;
  els.statNoData.textContent = counts.noData;
  els.statRetry.textContent = counts.retry;
  els.statPending.textContent = counts.pending;
  els.barFill.style.width = `${pct}%`;
  els.progressText.textContent =
    `${processed} / ${counts.total} processed (${pct}%) | status: ${state.status}`;
}

function summarizeGeo() {
  if (!state || !state.jobs) return [];
  const byGeo = new Map();
  for (const job of state.jobs) {
    const key = job.geo_code || "UNKNOWN";
    if (!byGeo.has(key)) byGeo.set(key, { total: 0, done: 0, failed: 0, noData: 0 });
    const item = byGeo.get(key);
    item.total += 1;
    if (job.status === "DONE") item.done += 1;
    if (job.status === "FAILED") item.failed += 1;
    if (job.status === "NO_DATA") item.noData += 1;
  }
  return Array.from(byGeo.entries()).map(([geo, item]) => ({ geo, ...item }));
}

function renderGeoSummary() {
  els.geoSummary.replaceChildren();
  for (const item of summarizeGeo()) {
    const span = document.createElement("span");
    span.textContent = `${item.geo}: ${item.done}/${item.total} done, ${item.failed} failed, ${item.noData} no data`;
    els.geoSummary.appendChild(span);
  }
}

function jobMatchesFilter(job, filter) {
  const status = job.status || "PENDING";
  if (filter === "all") return true;
  if (filter === "active") return !["DONE", "NO_DATA"].includes(status);
  if (filter === "pending") return status === "PENDING";
  if (filter === "retry") return status === "RETRY";
  if (filter === "failed") return status === "FAILED";
  if (filter === "done") return status === "DONE";
  if (filter === "nodata") return status === "NO_DATA";
  if (filter === "th") return job.geo_code === "TH";
  if (filter === "province") return job.geo_code !== "TH";
  return true;
}

function statusCss(status) {
  if (status === "DONE") return "status-done";
  if (status === "FAILED") return "status-failed";
  if (status === "RETRY") return "status-retry";
  if (status === "RUNNING") return "status-running";
  if (status === "NO_DATA") return "status-retry";
  return "status-pending";
}

function makeCell(text, className = "") {
  const td = document.createElement("td");
  td.textContent = text == null ? "" : String(text);
  if (className) td.className = className;
  return td;
}

function renderJobTable() {
  if (!state || !state.jobs) return;
  const filter = els.jobFilter.value || "active";
  const matches = state.jobs
    .map((job, index) => ({ job, index }))
    .filter(({ job }) => jobMatchesFilter(job, filter));
  const visible = matches.slice(0, MAX_TABLE_ROWS);

  els.jobTableBody.replaceChildren();
  for (const { job, index } of visible) {
    const tr = document.createElement("tr");
    tr.appendChild(makeCell(job.job_id));
    tr.appendChild(makeCell(job.geo_code));
    tr.appendChild(makeCell(`${job.keyword_id} ${job.keyword}`));
    tr.appendChild(makeCell(job.status || "PENDING", statusCss(job.status || "PENDING")));
    tr.appendChild(makeCell(job.attempts || 0));
    tr.appendChild(makeCell(job.error || ""));

    const actions = document.createElement("td");
    actions.className = "actions";

    const retry = document.createElement("button");
    retry.textContent = "Retry";
    retry.dataset.action = "retry";
    retry.dataset.index = String(index);
    retry.disabled = job.status === "RUNNING";
    actions.appendChild(retry);

    const open = document.createElement("button");
    open.textContent = "Open";
    open.dataset.action = "open";
    open.dataset.index = String(index);
    open.style.marginLeft = "4px";
    actions.appendChild(open);

    tr.appendChild(actions);
    els.jobTableBody.appendChild(tr);
  }

  els.jobCountLabel.textContent =
    `${visible.length} shown${matches.length > visible.length ? ` of ${matches.length}` : ""}`;
}

function refreshUI() {
  if (!state) return;
  renderStatus();
  renderStats();
  renderGeoSummary();
  renderJobTable();
  updateJobsSourceLabel();

  const isIdle = state.status === "idle";
  const isRunning = state.status === "running";
  const isPaused = state.status === "paused";
  const canReplaceQueue = isIdle && !loopRunning;
  els.btnStart.disabled = !isIdle || loopRunning;
  els.btnPause.disabled = !isRunning;
  els.btnResume.disabled = !isPaused;
  els.btnStop.disabled = !(isRunning || isPaused || loopRunning);
  els.btnSkip.disabled = !isRunning;
  els.btnReset.disabled = !canReplaceQueue;
  if (els.btnLoadJobs) els.btnLoadJobs.disabled = !canReplaceQueue;
  if (els.btnImportJobs) els.btnImportJobs.disabled = !canReplaceQueue;
}

// ----- Main loop ----------------------------------------------------------
let abortRequested = false;
let skipRequested = false;
let runningTabId = null;
let loopRunning = false;
let loopPromise = null;

async function validateRecentDownload(job, jobStartIso) {
  const filenamePattern = `.*${escapeRegExp(job.filename)}$`;
  const downloads = await chrome.downloads.search({
    startedAfter: jobStartIso,
    filenameRegex: filenamePattern,
    orderBy: ["-startTime"],
    limit: 5
  });
  if (downloads.length === 0) {
    // Playwright's persistent Chromium stores browser downloads under GUID
    // paths, so chrome.downloads cannot match the extension-suggested filename.
    // The Python runner may bridge that gap, but only after ingest.py's parser
    // and canonical coverage guard validate the captured file.
    const bridge = await chrome.storage.local.get([
      BROWSER_RUNNER_MODE_KEY,
      BROWSER_RUNNER_DOWNLOAD_ACK_KEY
    ]);
    const runnerMode = bridge[BROWSER_RUNNER_MODE_KEY] === true;
    const ack = bridge[BROWSER_RUNNER_DOWNLOAD_ACK_KEY];
    const ackIsCurrent = ack && ack.filename === job.filename &&
      Date.parse(ack.observed_at || "") >= Date.parse(jobStartIso);
    if (runnerMode && ackIsCurrent) {
      if (ack.status === "valid") return null;
      if (ack.status === "no_data_candidate") {
        return { result: "NO_DATA", reason: ack.reason || NO_DATA_EXPORT_REASON };
      }
      if (ack.status === "invalid") {
        return {
          result: "ERROR",
          reason: `BROWSER_RUNNER_INVALID_DOWNLOAD: ${ack.reason || "unknown"}`
        };
      }
    }
    return { result: "ERROR", reason: "NO_DOWNLOAD_FOUND" };
  }

  const dl = downloads[0];
  if (dl.state !== "complete") {
    return { result: "ERROR", reason: `DOWNLOAD_INCOMPLETE_${dl.state}` };
  }
  const size = downloadSize(dl);
  if (size > 0 && size < MIN_VALID_DOWNLOAD_BYTES) {
    // A canonical monthly export cannot plausibly fit under 200 bytes. Treat this
    // only as a retryable no-data heuristic; collector/ingest.py remains the final validator.
    return { result: "NO_DATA", reason: `${NO_DATA_EXPORT_REASON}_${size}B` };
  }
  return null;
}

async function processOne(job, settings) {
  const url = buildExploreUrl(job);
  log(`${job.job_id} | ${job.geo_code} | ${job.keyword_id} | "${job.keyword}"`, "info");
  els.currentJob.textContent =
    `${job.job_id} -> ${job.geo_code} ${job.geo_name} | ${job.keyword_id} "${job.keyword}"\n-> ${url}`;

  const winId = await ensureScraperWindow();
  await focusScraperWindow(winId, "before job");
  const tab = await chrome.tabs.create({ url, active: true, windowId: winId });
  runningTabId = tab.id;

  try {
    await waitForTabComplete(tab.id, settings.loadTimeout * 1000);
  } catch (e) {
    await closeTab(tab.id);
    runningTabId = null;
    return { result: "ERROR", reason: e.message || "TAB_LOAD_TIMEOUT" };
  }

  try {
    await chrome.tabs.update(tab.id, { active: true });
    await focusScraperWindow(winId, "before content job");
  } catch (_) {
    // Continue; content script will report PAGE_NOT_FOREGROUND if this matters.
  }
  await sleep(1800);

  if (skipRequested) {
    skipRequested = false;
    await closeTab(tab.id);
    runningTabId = null;
    return { result: "ERROR", reason: "SKIPPED_BY_USER" };
  }

  const jobStartIso = new Date().toISOString();

  let response;
  try {
    // Messaging retry only; job-level retry is controlled by settings.maxRetries.
    response = await sendJobWithRetry(tab.id, job, 4);
  } catch (e) {
    response = { result: "ERROR", reason: `MSG_FAIL: ${e.message || e}` };
  }

  if (response && response.result === "ERROR" &&
      ["CSV_BUTTON_NOT_FOUND", "CHART_TIMEOUT", "PAGE_NOT_FOREGROUND"].includes(response.reason)) {
    log(`${job.job_id} ${response.reason}; foreground reload once before job retry accounting`, "warn");
    try {
      await chrome.tabs.update(tab.id, { active: true });
      await focusScraperWindow(winId, "foreground reload");
      await chrome.tabs.reload(tab.id, { bypassCache: true });
      await waitForTabComplete(tab.id, settings.loadTimeout * 1000);
      await chrome.tabs.update(tab.id, { active: true });
      await focusScraperWindow(winId, "after reload");
      await sleep(2500);
      // Messaging retry only; still not a job retry.
      response = await sendJobWithRetry(tab.id, job, 4);
    } catch (e) {
      response = { result: "ERROR", reason: `FOREGROUND_RELOAD_FAIL: ${e.message || e}` };
    }
  }

  if (response && response.result === "BLOCKED" &&
      ["CAPTCHA", "AUTH_REQUIRED"].includes(response.reason)) {
    try {
      await chrome.tabs.update(tab.id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true, drawAttention: true });
    } catch (_) {
      // Ignore focus failures.
    }
    state.captcha_tab_id = tab.id;
    await saveState();
    runningTabId = null;
    return response; // Keep CAPTCHA tab open.
  }

  await closeTab(tab.id);
  runningTabId = null;

  if (response && response.result === "DONE") {
    try {
      await sleep(2500);
      const validation = await validateRecentDownload(job, jobStartIso);
      if (validation) response = validation;
    } catch (e) {
      log(`Download validation skipped: ${e.message || e}`, "warn");
    }
  }

  return response || { result: "ERROR", reason: "NO_RESPONSE" };
}

async function handleHumanAction(job, resp) {
  const authRequired = resp.reason === "AUTH_REQUIRED";
  const instruction = authRequired
    ? "Sign in to Google in the opened runner window once, then click Resume."
    : "Solve the CAPTCHA in the opened tab, then click Resume.";
  log(`${resp.reason} on ${job.job_id}. ${instruction}`, "err");
  state.status = "paused";
  state.last_block_at = Date.now();
  state.human_action_reason = resp.reason;
  job.status = "RETRY";
  job.attempts = Math.max(0, (job.attempts || 1) - 1);
  await saveState();
  refreshUI();

  try {
    await chrome.notifications.create(`captcha-${Date.now()}`, {
      type: "basic",
      iconUrl: NOTIFICATION_ICON_DATA_URL,
      title: `GT Toolkit Scraper - ${resp.reason}`,
      message: `${job.job_id}: ${instruction}`,
      priority: 2,
      requireInteraction: true
    });
  } catch (e) {
    log(`Human-action notification failed: ${e.message || e}`, "warn");
  }

  els.currentJob.textContent =
    `${resp.reason} - ${instruction}\n` +
    `Stuck on: ${job.job_id} ${job.geo_code} ${job.keyword_id} "${job.keyword}"`;
}

async function handleBlocked(job, resp, settings, idx) {
  const reason = resp.reason || "";
  const isSoft = reason === "SOFT_ERROR";

  if (job.attempts >= settings.maxRetries + 1) {
    job.status = "FAILED";
    job.error = reason;
    log(`${job.job_id} FAILED after ${job.attempts} blocked attempts: ${reason}`, "err");
    state.cursor = idx + 1;
    await saveState();
    refreshUI();
    const sleepSec = randInt(settings.throttleMin, settings.throttleMax);
    await waitOrPause(sleepSec * 1000);
    return;
  }

  const coolSec = isSoft
    ? Math.max(60, Math.floor((settings.longBreak || 300) / 2))
    : (settings.longBreak || 600);
  log(`${job.job_id} ${isSoft ? "SOFT_ERROR" : "BLOCKED"} attempt ${job.attempts}/${settings.maxRetries + 1}; cooldown ${coolSec}s`, "warn");
  job.status = "RETRY";
  job.error = reason;
  state.last_block_at = Date.now();
  await saveState();
  refreshUI();
  await waitOrPause(coolSec * 1000);
}

async function exportNoDataManifest() {
  if (state.no_data_manifest_exported_at) return;
  const noDataJobs = state.jobs.filter(job => job.status === "NO_DATA");
  if (noDataJobs.length === 0) {
    state.no_data_manifest_exported_at = localIsoTimestamp();
    await saveState();
    return;
  }
  const incomplete = noDataJobs.filter(job =>
    !job.no_data_observed_at || (job.no_data_attempts || 0) < MIN_NO_DATA_ATTEMPTS
  );
  if (incomplete.length) {
    throw new Error(
      `Cannot export no-data proof: ${incomplete.length} jobs lack repeated observation metadata`
    );
  }

  const generatedAt = localIsoTimestamp();
  const manifest = {
    schema: NO_DATA_MANIFEST_SCHEMA,
    generated_at: generatedAt,
    jobs_source: state.jobs_source || DEFAULT_JOBS_FILE,
    entries: noDataJobs.map(job => ({
      job_id: job.job_id,
      keyword_id: job.keyword_id,
      keyword: job.keyword,
      geo_code: job.geo_code,
      timeframe: job.timeframe,
      status: "NO_DATA",
      attempts: job.attempts,
      no_data_attempts: job.no_data_attempts,
      reason: job.error || "NO_DATA",
      observed_at: job.no_data_observed_at
    }))
  };
  const url = `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(manifest, null, 2))}`;
  const filename = `no_data_manifest__${localDateString()}.json`;
  const downloadId = await chrome.downloads.download({
    url,
    filename,
    conflictAction: "uniquify",
    saveAs: false
  });
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const [download] = await chrome.downloads.search({ id: downloadId });
    if (download && download.state === "complete") break;
    if (download && download.state === "interrupted") {
      throw new Error(`No-data manifest download interrupted: ${download.error || "unknown"}`);
    }
    await sleep(250);
  }
  const [completed] = await chrome.downloads.search({ id: downloadId });
  if (!completed || completed.state !== "complete") {
    throw new Error("No-data manifest download did not complete within 15 seconds");
  }
  state.no_data_manifest_exported_at = generatedAt;
  await saveState();
  log(`Exported ${filename} with ${noDataJobs.length} confirmed no-data cells`, "ok");
}

async function mainLoop() {
  state.status = "running";
  state.fatal_error = null;
  if (!state.started_at) state.started_at = Date.now();
  await saveState();
  refreshUI();

  const initialSettings = readSettings();
  if (initialSettings.warmEvery > 0) {
    await warmingVisit(initialSettings.warmDwell);
  }

  while (!abortRequested) {
    if (state.status === "paused") {
      await sleep(800);
      continue;
    }

    const settings = readSettings();
    const idx = nextPendingIndex(state.cursor);
    if (idx === -1) {
      log("All jobs processed.", "ok");
      try {
        await exportNoDataManifest();
      } catch (e) {
        log(`No-data manifest export failed: ${e.message || e}`, "err");
        state.status = "paused";
        await saveState();
        refreshUI();
        return;
      }
      state.status = "idle";
      await saveState();
      refreshUI();
      return;
    }

    state.cursor = idx;
    const job = state.jobs[idx];
    job.attempts = (job.attempts || 0) + 1;
    job.status = "RUNNING";
    await saveState();
    refreshUI();

    const resp = await processOne(job, settings);

    if (resp.result === "DONE") {
      job.status = "DONE";
      job.error = null;
      job.no_data_attempts = 0;
      job.no_data_observed_at = null;
      log(`${job.job_id} done (file: ${job.filename})`, "ok");
    } else if (resp.result === "NO_DATA") {
      job.no_data_attempts = (job.no_data_attempts || 0) + 1;
      const requiredNoDataAttempts = MIN_NO_DATA_ATTEMPTS;
      if (job.no_data_attempts < requiredNoDataAttempts) {
        job.status = "RETRY";
        job.error = resp.reason;
        job.no_data_observed_at = null;
        log(`${job.job_id} possible NO DATA (${resp.reason}); will retry (${job.no_data_attempts}/${requiredNoDataAttempts} no-data observations)`, "warn");
      } else {
        job.status = "NO_DATA";
        job.error = resp.reason;
        job.no_data_observed_at = localIsoTimestamp();
        log(`${job.job_id} NO DATA (${resp.reason}) after ${job.no_data_attempts} consecutive no-data observations; recorded, moving on`, "warn");
      }
    } else if (resp.result === "BLOCKED" &&
               ["CAPTCHA", "AUTH_REQUIRED"].includes(resp.reason)) {
      job.no_data_attempts = 0;
      job.no_data_observed_at = null;
      await handleHumanAction(job, resp);
      continue;
    } else if (resp.result === "BLOCKED") {
      job.no_data_attempts = 0;
      job.no_data_observed_at = null;
      await handleBlocked(job, resp, settings, idx);
      continue;
    } else {
      job.no_data_attempts = 0;
      job.no_data_observed_at = null;
      if (job.attempts < settings.maxRetries + 1) {
        job.status = "RETRY";
        job.error = resp.reason;
        log(`${job.job_id} error (${resp.reason}); will retry (${job.attempts}/${settings.maxRetries + 1})`, "warn");
      } else {
        job.status = "FAILED";
        job.error = resp.reason;
        log(`${job.job_id} FAILED after ${job.attempts} attempts: ${resp.reason}`, "err");
      }
    }

    if (job.status !== "RETRY") {
      state.cursor = idx + 1;
    }
    await saveState();
    refreshUI();

    const sleepSec = randInt(settings.throttleMin, settings.throttleMax);
    log(`Sleeping ${sleepSec}s`, "info");
    await waitOrPause(sleepSec * 1000);

    const counts = getCounts();
    const processed = counts.done + counts.failed + counts.noData;
    if (!abortRequested && settings.longEvery > 0 && processed > 0 && processed % settings.longEvery === 0) {
      log(`Long break ${settings.longBreak}s (every ${settings.longEvery} processed jobs)`, "warn");
      await waitOrPause(settings.longBreak * 1000);
    }
    if (!abortRequested && settings.warmEvery > 0 && processed > 0 && processed % settings.warmEvery === 0) {
      await warmingVisit(settings.warmDwell);
    }
  }

  state.status = "idle";
  await saveState();
  refreshUI();
}

function launchMainLoop() {
  if (loopRunning) {
    log("Main loop is already running.", "warn");
    return loopPromise;
  }

  abortRequested = false;
  loopRunning = true;
  loopPromise = mainLoop()
    .catch(async e => {
      const msg = e && (e.stack || e.message) ? (e.stack || e.message) : String(e);
      log(`FATAL: ${msg}`, "err");
      if (state) {
        state.status = "fatal";
        state.fatal_error = msg;
        await saveState().catch(err => console.error("[ctrl] save after fatal failed", err));
      }
    })
    .finally(() => {
      loopRunning = false;
      loopPromise = null;
      refreshUI();
    });
  return loopPromise;
}

// ----- Queue utilities ----------------------------------------------------
function requeueJob(job) {
  job.status = "PENDING";
  job.attempts = 0;
  job.error = null;
  job.no_data_attempts = 0;
  job.no_data_observed_at = null;
  if (state) state.no_data_manifest_exported_at = null;
}

async function retryFailedJobs() {
  if (!state) await loadState();
  let count = 0;
  let first = null;
  for (let i = 0; i < state.jobs.length; i++) {
    const job = state.jobs[i];
    if (job.status !== "FAILED" && job.status !== "NO_DATA") continue;
    requeueJob(job);
    if (first == null) first = i;
    count += 1;
  }
  if (first != null) state.cursor = first;
  await saveState();
  log(`Retry Failed/No Data: re-queued ${count} jobs`, count ? "ok" : "warn");
  refreshUI();
}

async function reconcileDownloads() {
  if (!state) await loadState();

  let downloads;
  try {
    downloads = await chrome.downloads.search({
      filenameRegex: ".*__TH.*\\.csv$",
      state: "complete",
      exists: true,
      limit: 1000
    });
  } catch (_) {
    downloads = await chrome.downloads.search({
      filenameRegex: ".*__TH.*\\.csv$",
      state: "complete",
      limit: 1000
    });
  }

  const existing = new Set();
  const noDataExports = new Map();
  for (const dl of downloads) {
    const name = basename(dl.filename);
    const size = downloadSize(dl);
    if (size > 0 && size < MIN_VALID_DOWNLOAD_BYTES) {
      noDataExports.set(name, size);
      continue;
    }
    existing.add(name);
  }
  let marked = 0;
  let markedNoData = 0;
  let firstPending = null;

  for (let i = 0; i < state.jobs.length; i++) {
    const job = state.jobs[i];
    const target = basename(job.filename);
    // A later valid full export wins over an earlier tiny heuristic export.
    if (existing.has(target)) {
      if (job.status !== "DONE") {
        job.status = "DONE";
        job.error = null;
        job.no_data_attempts = 0;
        job.no_data_observed_at = null;
        state.no_data_manifest_exported_at = null;
        marked += 1;
      }
      continue;
    }
    if (noDataExports.has(target)) {
      if (job.status !== "DONE" && job.status !== "NO_DATA") {
        job.status = "NO_DATA";
        job.error = `${NO_DATA_EXPORT_REASON}_${noDataExports.get(target)}B`;
        job.no_data_attempts = 0;
        job.no_data_observed_at = null;
        state.no_data_manifest_exported_at = null;
        markedNoData += 1;
      }
      continue;
    }
    if ((job.status === "PENDING" || job.status === "RETRY") && firstPending == null) {
      firstPending = i;
    }
  }

  if (firstPending != null) state.cursor = firstPending;
  await saveState();
  log(
    `Reconcile Downloads: ${downloads.length} panel CSVs in Chrome history, ` +
    `${marked} jobs marked DONE, ${markedNoData} tiny exports marked NO_DATA`,
    (marked || markedNoData) ? "ok" : "info"
  );
  refreshUI();
}

async function copyDebugReport() {
  if (!state) await loadState();
  const counts = getCounts();
  let pendingQueue = "unavailable";
  try {
    const resp = await chrome.runtime.sendMessage({ cmd: "GET_PENDING" });
    pendingQueue = JSON.stringify(resp.queue || []);
  } catch (e) {
    pendingQueue = `error: ${e.message || e}`;
  }

  const recentLogs = Array.from(els.log.querySelectorAll("div"))
    .slice(-80)
    .map(div => div.textContent)
    .join("\n");

  const report = [
    "GT Toolkit Scraper Debug Report",
    `time=${new Date().toISOString()}`,
    `status=${state.status}`,
    `loopRunning=${loopRunning}`,
    `cursor=${state.cursor}`,
    `counts=${JSON.stringify(counts)}`,
    `scraper_window_id=${state.scraper_window_id || ""}`,
    `captcha_tab_id=${state.captcha_tab_id || ""}`,
    `pending_download_queue=${pendingQueue}`,
    "",
    "Recent log:",
    recentLogs
  ].join("\n");

  await navigator.clipboard.writeText(report);
  log("Debug report copied to clipboard", "ok");
}

// ----- Button handlers ----------------------------------------------------
els.btnStart.addEventListener("click", async () => {
  if (!state) await loadState();
  if (!state) await resetState();
  if (state.status !== "idle") {
    log(`Start is only available from idle. Current status: ${state.status}`, "warn");
    refreshUI();
    return;
  }
  log("Start", "info");
  launchMainLoop();
});

els.btnPause.addEventListener("click", async () => {
  if (!state) return;
  state.status = "paused";
  await saveState();
  log("Paused", "warn");
  refreshUI();
});

els.btnResume.addEventListener("click", async () => {
  if (!state) await loadState();
  if (!state) return;

  if (state.captcha_tab_id) {
    try {
      await chrome.tabs.remove(state.captcha_tab_id);
    } catch (_) {
      // Tab may already be gone.
    }
    log(`${state.human_action_reason || "Human-action"} tab closed (was ${state.captcha_tab_id}); retrying the job`, "ok");
    state.captcha_tab_id = null;
    state.human_action_reason = null;
  }

  state.status = "running";
  await saveState();
  log("Resumed", "info");
  refreshUI();
  if (!loopRunning) launchMainLoop();
});

els.btnStop.addEventListener("click", async () => {
  if (!state) return;
  abortRequested = true;
  state.status = "idle";
  await saveState();
  log("Stop requested; current wait/job will finish before loop exits", "warn");
  refreshUI();
});

els.btnSkip.addEventListener("click", () => {
  skipRequested = true;
  log("Skip current requested", "warn");
});

els.btnReset.addEventListener("click", async () => {
  if (loopRunning || !state || state.status !== "idle") {
    log("Stop the active queue before resetting it", "warn");
    return;
  }
  if (!confirm("Reset queue: all progress in extension state will be cleared. Continue?")) return;
  abortRequested = true;
  if (runningTabId) await closeTab(runningTabId);
  if (state && Array.isArray(state.jobs) && state.jobs.length) {
    await resetStateFromJobs(state.jobs, state.jobs_source || "current queue");
  } else {
    await resetState();
  }
  log("Queue reset", "warn");
  refreshUI();
});

els.btnReflag.addEventListener("click", async () => {
  if (!state) await loadState();
  const raw = (els.reflagInput.value || "").trim();
  if (!raw) {
    log("Re-flag: input empty", "warn");
    return;
  }

  const tokens = raw.split(/[\s,]+/).filter(Boolean).map(t => t.toUpperCase());
  let count = 0;
  let first = null;
  for (let i = 0; i < state.jobs.length; i++) {
    const job = state.jobs[i];
    const match = tokens.includes(job.job_id.toUpperCase()) ||
      tokens.includes(job.keyword_id.toUpperCase()) ||
      tokens.includes(`${job.geo_code}_${job.keyword_id}`.toUpperCase());
    if (!match) continue;

    requeueJob(job);
    if (first == null) first = i;
    count += 1;
  }
  if (first != null) state.cursor = first;
  await saveState();
  log(`Re-flagged ${count} jobs as PENDING${first != null ? ` (cursor=${first})` : ""}`, count ? "ok" : "warn");
  els.reflagInput.value = "";
  refreshUI();
});

els.btnRetryFailed.addEventListener("click", retryFailedJobs);
els.btnReconcileDownloads.addEventListener("click", reconcileDownloads);
els.btnCopyDebug.addEventListener("click", () => {
  copyDebugReport().catch(e => log(`Copy debug failed: ${e.message || e}`, "err"));
});
els.btnClearLog.addEventListener("click", () => {
  els.log.replaceChildren();
});
els.btnRefocusWindow.addEventListener("click", async () => {
  if (!state) await loadState();
  if (!state) return;
  const winId = await ensureScraperWindow();
  await focusScraperWindow(winId, "manual refocus");
  log("Scraper window refocused", "ok");
});

els.jobFilter.addEventListener("change", renderJobTable);
els.jobTableBody.addEventListener("click", async event => {
  const btn = event.target.closest("button[data-action]");
  if (!btn || !state) return;
  const index = Number(btn.dataset.index);
  const job = state.jobs[index];
  if (!job) return;

  if (btn.dataset.action === "retry") {
    requeueJob(job);
    state.cursor = index;
    await saveState();
    log(`${job.job_id} re-queued`, "ok");
    refreshUI();
  } else if (btn.dataset.action === "open") {
    const winId = await ensureScraperWindow();
    await chrome.tabs.create({ url: buildExploreUrl(job), active: true, windowId: winId });
    await focusScraperWindow(winId, "manual open");
  }
});

if (els.btnHideWarning) {
  els.btnHideWarning.addEventListener("click", async () => {
    await chrome.storage.local.set({ [HIDE_FOREGROUND_WARNING_KEY]: true });
    if (els.fgWarning) els.fgWarning.style.display = "none";
  });
}

if (els.btnLoadJobs) {
  els.btnLoadJobs.addEventListener("click", async () => {
    if (loopRunning || (state && state.status !== "idle")) {
      log("Stop the active queue before loading another jobs file", "warn");
      return;
    }
    const file = els.jobsSourceSelect ? els.jobsSourceSelect.value : DEFAULT_JOBS_FILE;
    const active = await getActiveJobsFile();
    const hasState = state && state.jobs && state.jobs.length;
    const msg = hasState
      ? `Load "${file}"?\n\nThis will RESET the current queue (${state.jobs.length} jobs from ${active}) and load fresh jobs.`
      : `Load "${file}" as the active jobs file?`;
    if (!confirm(msg)) return;
    try {
      await resetState(file);
      log(`Loaded ${state.jobs.length} jobs from ${file}`, "info");
      updateJobsSourceLabel();
      refreshUI();
    } catch (e) {
      log(`Failed to load ${file}: ${e.message}`, "err");
      alert(`Could not load ${file}\n\n${e.message}`);
    }
  });
}

if (els.btnImportJobs && els.jobsFileInput) {
  els.btnImportJobs.addEventListener("click", () => {
    if (loopRunning || (state && state.status !== "idle")) {
      log("Stop the active queue before importing another jobs file", "warn");
      return;
    }
    els.jobsFileInput.value = "";
    els.jobsFileInput.click();
  });
  els.jobsFileInput.addEventListener("change", async () => {
    const file = els.jobsFileInput.files && els.jobsFileInput.files[0];
    if (!file) return;
    if (loopRunning || (state && state.status !== "idle")) {
      log("Queue state changed while choosing the file; stop it before importing", "warn");
      return;
    }
    const hasState = state && state.jobs && state.jobs.length;
    if (hasState && !confirm(
      `Import "${file.name}"?\n\nThis will RESET the current queue (${state.jobs.length} jobs).`
    )) return;
    try {
      await importJobsFile(file);
      log(`Imported ${state.jobs.length} jobs from ${file.name}`, "ok");
      updateJobsSourceLabel();
      refreshUI();
    } catch (error) {
      log(`Failed to import ${file.name}: ${error.message || error}`, "err");
      alert(`Could not import ${file.name}\n\n${error.message || error}`);
    }
  });
}

// ----- Boot ----------------------------------------------------------------
(async () => {
  await loadWarningPreference();
  await loadState();
  if (!state) {
    await resetState();
    log(`Loaded ${state.jobs.length} jobs from ${state.jobs_source || DEFAULT_JOBS_FILE}`, "info");
  } else {
    const repaired = await repairRestoredState();
    const counts = getCounts();
    log(
      `Restored state: ${counts.done} done, ${counts.failed} failed, ${counts.noData} no data, ` +
      `${counts.retry + counts.pending} pending/retry${repaired ? " (repaired interrupted run)" : ""} ` +
      `(source: ${state.jobs_source || "unknown"})`,
      "info"
    );
  }
  await populateJobsSourceUI();
  refreshUI();
})().catch(e => {
  log(`BOOT FATAL: ${e.message || e}`, "err");
});
