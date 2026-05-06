(() => {
  if (window.__kaggleScanTickInstalled) {
    return;
  }
  window.__kaggleScanTickInstalled = true;

  const POLL_INTERVAL_MS = 5000;

  function isTargetEditUrl(url) {
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      return (host === "kaggle.com" || host.endsWith(".kaggle.com")) && parsed.pathname.endsWith("/edit");
    } catch {
      return false;
    }
  }

  function sendTick(reason) {
    if (!isTargetEditUrl(window.location.href)) {
      return;
    }

    chrome.runtime.sendMessage(
      {
        type: "SCAN_TICK",
        reason,
        tabUrl: window.location.href,
        sentAt: new Date().toISOString()
      },
      () => {
        void chrome.runtime.lastError;
      }
    );
  }

  sendTick("content_script_start");

  setInterval(() => {
    sendTick("interval");
  }, POLL_INTERVAL_MS);

  window.addEventListener("focus", () => {
    sendTick("window_focus");
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      sendTick("visibility_visible");
    }
  });
})();
