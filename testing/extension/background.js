const HOST = "com.testing.tabprinter";

// Keep one persistent native port open to Python
let port = null;

function scrapeNotebook() {
  const cells = [];
  const cellElements = document.querySelectorAll(".cell, .jp-Cell, .code_cell, .text_cell, .markdown_cell");
  for (const cell of cellElements) {
    const cellData = {};
    if (cell.classList.contains("code_cell") || cell.classList.contains("jp-CodeCell") || cell.querySelector(".CodeMirror, .jp-Editor, .cm-editor")) {
      cellData.type = "code";
    } else {
      cellData.type = "markdown";
    }
    const codeEl = cell.querySelector(".CodeMirror-code, .jp-Editor .cm-content, .cm-editor .cm-content, .input_area pre, .jp-InputArea-editor pre, textarea");
    cellData.source = codeEl ? codeEl.innerText.trim() : "";
    const outputEl = cell.querySelector(".output, .jp-OutputArea, .output_area, .jp-Cell-outputArea");
    cellData.output = outputEl ? outputEl.innerText.trim() : "";
    cells.push(cellData);
  }
  return { title: document.title || "", cellCount: cells.length, cells: cells };
}

function getPort() {

  if (port) return port;
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener((msg) => {
    console.log("Python says:", msg);
    // Only deliver tab-scoped data to the originating tab to avoid cross-tab leakage.
    if (typeof msg?.tabId === "number") {
      chrome.tabs.sendMessage(msg.tabId, msg);
      return;
    }

    // Ignore untargeted host messages for UI-bound payloads.
    if (["CHAT_RESPONSE", "HISTORY_DATA", "HISTORY_CLEARED", "GRAPH_DATA"].includes(msg?.type)) {
      console.warn("Dropped untargeted host message:", msg?.type);
      return;
    }
  });
  port.onDisconnect.addListener(() => {
    console.warn("Native port disconnected:", chrome.runtime.lastError?.message);
    port = null;
  });
  return port;
}

// ── UI Injection ─────────────────────────────────────────────────────────────
function injectUI(tabId) {
  chrome.scripting.executeScript({
    target: { tabId: tabId },
    files: ["markdown-it.min.js", "ui_injector.js"]
  }).catch(e => console.warn("UI Injection failed:", e));
}

// ── Bridge UI messages to the Native Host ────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const p = getPort();
  if (p) {
    // Tag the message with the sender's tabId so we know where to send the AI's reply
    msg.tabId = sender.tab?.id;
    p.postMessage(msg);
  }
  return true; // Keep channel open for async
});

// ── Main scan logic (includes UI check) ──────────────────────────────────────
function sendTabs() {
  chrome.tabs.query({}, (tabs) => {
    const targets = tabs.filter(t => {
      if (!t.url) return false;
      try {
        const u = new URL(t.url);
        return (u.protocol === "http:" || u.protocol === "https:") && u.pathname.endsWith("/edit");
      } catch { return false; }
    });

    for (const tab of targets) {
      // Ensure UI is injected
      injectUI(tab.id);

      // (Rest of the scraping logic remains same...)
      chrome.scripting.executeScript({

          target: { tabId: tab.id, allFrames: false },
          func: () => {
            const found = [];
            for (const iframe of document.querySelectorAll("iframe")) {
              const src = iframe.getAttribute("src") || iframe.src || "";
              if (src) try { found.push(new URL(src, location.href).toString()); } catch {}
            }
            return found;
          }
      }, (results) => {
          if (chrome.runtime.lastError) return;
          const iframes = results?.[0]?.result || [];
          chrome.scripting.executeScript({
              target: { tabId: tab.id, allFrames: true },
              func: scrapeNotebook
          }, (scrapeResults) => {
              if (chrome.runtime.lastError) return;
              const allCells = [];
              let notebookTitle = "";
              for (const r of (scrapeResults || [])) {
                if (r?.result?.cellCount > 0) {
                  allCells.push(...r.result.cells);
                  if (r.result.title) notebookTitle = r.result.title;
                }
              }
              getPort().postMessage({
                type: "NOTEBOOK_DATA", tabUrl: tab.url, iframes: iframes,
                tabId: tab.id,
                title: notebookTitle, cellCount: allCells.length, cells: allCells
              });
          });
      });
    }
  });
}

// Events
chrome.runtime.onInstalled.addListener(sendTabs);
chrome.runtime.onStartup.addListener(sendTabs);
setInterval(sendTabs, 10000);
chrome.tabs.onUpdated.addListener((id, info) => {
  if (info.status === "complete") sendTabs();
});
sendTabs();
