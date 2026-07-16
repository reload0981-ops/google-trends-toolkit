// background.js — service worker
//
// Responsibilities:
//   1. Rename Google Trends CSV downloads to toolkit schema (<ID>__<GEO>.csv,
//      as queued by content.js from each job's filename field).
//   2. Coordinate filename mapping with content.js via in-memory + storage queue.

// Files land directly in Chrome's configured Downloads location.
// Set that location (chrome://settings/downloads) to this repo's incoming/ folder
// so finished downloads are immediately ready for: python collector/ingest.py
const SUBFOLDER = "";

// The controller runs one download at a time. Keep exactly one expected filename:
// replacing it on PREPARE_DOWNLOAD prevents a failed/late attempt from renaming the
// next job with a stale filename. Backed by session storage for worker restarts.
let pendingFilenames = [];
const TOOLKIT_FILENAME = /^[A-Z]{2}\d{3}__(TH(?:-\d{2})?)\.csv$/;

async function loadQueue() {
  const { pending_filenames = [] } = await chrome.storage.session.get("pending_filenames");
  pendingFilenames = Array.isArray(pending_filenames)
    ? pending_filenames.filter(name => typeof name === "string" && TOOLKIT_FILENAME.test(name))
    : [];
}

async function saveQueue() {
  await chrome.storage.session.set({ pending_filenames: pendingFilenames });
}

function basename(path) {
  return String(path || "").split(/[\\/]/).pop().toLowerCase();
}

function looksLikeExpectedTimeseriesDownload(item, expectedFilename) {
  const expected = TOOLKIT_FILENAME.exec(expectedFilename || "");
  if (!expected) return false;
  const geo = expected[1].toLowerCase();
  const raw = basename(item && item.filename);
  const prefix = `time_series_${geo}_`;
  return raw.startsWith(prefix) &&
    /^\d{8}-\d{4}_\d{8}-\d{4}(?: \(\d+\))?\.csv$/.test(raw.slice(prefix.length));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.cmd === "PREPARE_DOWNLOAD") {
    (async () => {
      await loadQueue();
      if (typeof msg.filename !== "string" || !TOOLKIT_FILENAME.test(msg.filename)) {
        console.error("[bg] rejected invalid toolkit filename:", msg.filename);
        sendResponse({ ok: false, error: "INVALID_TOOLKIT_FILENAME" });
        return;
      }
      const replaced = pendingFilenames.length;
      pendingFilenames = [msg.filename];
      await saveQueue();
      console.log("[bg] prepared filename:", msg.filename, `(replaced ${replaced} stale)`);
      sendResponse({ ok: true, replaced });
    })();
    return true;
  }
  if (msg && msg.cmd === "GET_PENDING") {
    (async () => {
      await loadQueue();
      sendResponse({ queue: pendingFilenames });
    })();
    return true;
  }
});

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  (async () => {
    await loadQueue();
    if (pendingFilenames.length === 0) {
      console.warn("[bg] trends download but queue empty — leaving filename:", item.filename);
      suggest();
      return;
    }
    const next = pendingFilenames[0];
    if (!looksLikeExpectedTimeseriesDownload(item, next)) {
      console.warn("[bg] download does not match expected time-series geo — leaving filename:", item.filename);
      suggest();
      return;
    }
    pendingFilenames.shift();
    await saveQueue();
    const final = SUBFOLDER ? `${SUBFOLDER}/${next}` : next;
    console.log("[bg] renaming download →", final);
    suggest({ filename: final, conflictAction: "overwrite" });
  })();
  return true; // async suggest
});

console.log("[bg] service worker ready");
