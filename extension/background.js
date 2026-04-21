// ─────────────────────────────────────────────────────────────────────────────
// background.js  –  Normal Chrome + Extension Native Messaging
//
// Key design decisions:
//   • connectNative()  instead of sendNativeMessage()
//     → ONE persistent Python process instead of a new one per message.
//     → Python reads from stdin in a loop; we never kill it between scans.
//   • Scans ALL open tabs immediately on install / startup / every 5 s.
//     → Existing tabs that were open before the extension loaded are caught.
//   • Any http/https tab is a candidate; we report URL + iframes to Python.
// ─────────────────────────────────────────────────────────────────────────────

const NATIVE_HOST_NAME = "com.normalchrome.scraper";
const POLL_INTERVAL_MS = 5000;

// ── Persistent native port ───────────────────────────────────────────────────
let nativePort = null;

function getNativePort() {
  if (nativePort) return nativePort;

  nativePort = chrome.runtime.connectNative(NATIVE_HOST_NAME);

  nativePort.onMessage.addListener((msg) => {
    console.log("[normal-chrome] Python →", msg);
  });

  nativePort.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    console.warn("[normal-chrome] Native port disconnected:", err?.message || "no error");
    nativePort = null;
  });

  return nativePort;
}

function sendToNative(payload) {
  try {
    getNativePort().postMessage(payload);
  } catch (err) {
    console.error("[normal-chrome] sendToNative failed:", err);
    nativePort = null; // force reconnect next time
  }
}

// ── Tab scanning ─────────────────────────────────────────────────────────────
let scanInFlight = false;
const lastIframeFingerprintByTab = new Map();
let lastTabsFingerprint = "";

async function runScan(trigger) {
  if (scanInFlight) return;
  scanInFlight = true;

  try {
    const allTabs = await queryAllTabs();
    const targets = allTabs.filter(
      (tab) => typeof tab?.id === "number" && isTargetUrl(tab?.url)
    );

    // ── 1. Report which target tabs exist (only when list changes) ──────────
    const tabsFingerprint = targets
      .map((t) => `${t.id}:${t.url}`)
      .sort()
      .join("|");

    if (tabsFingerprint !== lastTabsFingerprint) {
      lastTabsFingerprint = tabsFingerprint;
      sendToNative({
        type: "TARGET_TABS_DISCOVERED",
        count: targets.length,
        tabs: targets.map((t) => ({ tabId: t.id, tabUrl: String(t.url || "") })),
        trigger,
        discoveredAt: new Date().toISOString()
      });
    }

    // ── 2. Clean up stale tab entries ───────────────────────────────────────
    const activeIds = new Set(targets.map((t) => t.id));
    for (const tabId of lastIframeFingerprintByTab.keys()) {
      if (!activeIds.has(tabId)) lastIframeFingerprintByTab.delete(tabId);
    }

    // ── 3. Scan each target tab for iframes ─────────────────────────────────
    for (const tab of targets) {
      await scanSingleTab(tab.id, String(tab.url || ""), trigger);
    }
  } finally {
    scanInFlight = false;
  }
}

async function scanSingleTab(tabId, tabUrl, trigger) {
  const result = await getIframeUrls(tabId);

  if (!result.ok) {
    const fp = `error:${result.reason}`;
    if (lastIframeFingerprintByTab.get(tabId) === fp) return; // no change
    lastIframeFingerprintByTab.set(tabId, fp);
    sendToNative({
      type: "TAB_IFRAMES_FAILED",
      tabId,
      tabUrl,
      reason: result.reason,
      trigger,
      discoveredAt: new Date().toISOString()
    });
    return;
  }

  const fp = result.iframes.join("|");
  if (lastIframeFingerprintByTab.get(tabId) === fp) return; // no change
  lastIframeFingerprintByTab.set(tabId, fp);

  sendToNative({
    type: "TAB_IFRAMES_DISCOVERED",
    tabId,
    tabUrl,
    iframes: result.iframes,
    trigger,
    discoveredAt: new Date().toISOString()
  });
}

// ── Iframe extraction (injected into tab's main frame) ───────────────────────
async function getIframeUrls(tabId) {
  const injected = await injectScript(tabId, () => {
    const seen = new Set();
    const found = [];
    for (const iframe of document.querySelectorAll("iframe")) {
      const raw = iframe.getAttribute("src") || iframe.src || "";
      if (!raw) continue;
      try {
        const absolute = new URL(raw, window.location.href).toString();
        if (!seen.has(absolute)) {
          seen.add(absolute);
          found.push(absolute);
        }
      } catch {
        // ignore malformed src
      }
    }
    return found;
  });

  if (!injected.ok) {
    return { ok: false, reason: `inject_failed:${injected.error || "unknown"}` };
  }

  const raw = injected.results?.[0]?.result;
  const iframes = Array.isArray(raw)
    ? raw.filter((x) => typeof x === "string" && x.length > 0)
    : [];

  return { ok: true, iframes };
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function isTargetUrl(rawUrl) {
  try {
    const { protocol } = new URL(String(rawUrl || ""));
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

function queryAllTabs() {
  return new Promise((resolve) => {
    chrome.tabs.query({}, (tabs) => resolve(Array.isArray(tabs) ? tabs : []));
  });
}

function injectScript(tabId, func) {
  return new Promise((resolve) => {
    chrome.scripting.executeScript(
      { target: { tabId, allFrames: false }, func },
      (results) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
        } else {
          resolve({ ok: true, results: Array.isArray(results) ? results : [] });
        }
      }
    );
  });
}

// ── Event listeners ──────────────────────────────────────────────────────────

// Scan all already-open tabs immediately when extension installs or Chrome starts
chrome.runtime.onInstalled.addListener(() => void runScan("onInstalled"));
chrome.runtime.onStartup.addListener(() => void runScan("onStartup"));

// Also scan when any tab finishes loading
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") return;
  if (!isTargetUrl(tab?.url)) return;
  void scanSingleTab(tabId, String(tab.url || ""), "tabs_onUpdated");
});

// Clean up when a tab closes
chrome.tabs.onRemoved.addListener((tabId) => {
  lastIframeFingerprintByTab.delete(tabId);
});

// Heartbeat – catches tabs already open that never fired an onUpdated event
setInterval(() => void runScan("interval"), POLL_INTERVAL_MS);
