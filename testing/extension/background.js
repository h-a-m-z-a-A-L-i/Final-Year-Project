const HOST = "com.testing.tabprinter";

// Keep one persistent native port open to Python
let port = null;
let __lastPromptSignal = 0;

function scrapeNotebook() {
  const cells = [];
  const cellElements = document.querySelectorAll(".cell, .jp-Cell, .code_cell, .text_cell, .markdown_cell");
  const extractExecutionMeta = (cell) => {
    const buttonHost = cell.querySelector(
      ".cell-execution-button, [title*='Cell executed'], [title*='Cell started execution'], [title*='Cell execution queued'], [aria-label*='Cell executed'], [aria-label*='Cell started execution'], [aria-label*='Cell execution queued']"
    );
    const buttonText = buttonHost
      ? String(
          buttonHost.getAttribute("title") ||
          buttonHost.getAttribute("aria-label") ||
          buttonHost.title ||
          buttonHost.innerText ||
          buttonHost.textContent ||
          ""
        ).trim()
      : "";
    const promptEl = cell.querySelector(
      ".jp-InputPrompt.jp-InputArea-prompt, .jp-InputPrompt, .input_prompt"
    );
    const executionLabel = promptEl
      ? String(promptEl.innerText || promptEl.textContent || "").trim()
      : "";
    const promptText = executionLabel;
    const titleHost = promptEl || null;
    const executionTitle = titleHost
      ? String(
          titleHost.getAttribute("title") ||
          titleHost.title ||
          titleHost.getAttribute("aria-label") ||
          ""
        ).trim()
      : "";

    let executionOrder = null;
    if (executionLabel) {
      const labelMatch = executionLabel.match(/\d+/);
      if (labelMatch) {
        executionOrder = Number(labelMatch[0]);
      }
    }
    if (executionOrder == null && executionTitle) {
      const titleMatch = executionTitle.match(/executed\s+in\s+[^@]*@\s*.*?(\d+)/i);
      if (titleMatch) {
        executionOrder = Number(titleMatch[1]);
      }
    }

    let executionStatus = "idle";
    const combinedSignal = (buttonText + "\n" + promptText + "\n" + executionTitle).trim();
    if (/Cell execution queued/i.test(combinedSignal)) {
      executionStatus = "queued";
    } else if (/Cell started execution/i.test(combinedSignal)) {
      executionStatus = "running";
    } else if (/Cell executed/i.test(combinedSignal)) {
      executionStatus = "executed";
    }

    return {
      execution_order: Number.isFinite(executionOrder) ? executionOrder : null,
      execution_title: executionTitle,
      execution_status: executionStatus,
      execution_signal: buttonText,
    };
  };
  for (const cell of cellElements) {
    const cellData = {};
    if (cell.classList.contains("code_cell") || cell.classList.contains("jp-CodeCell") || cell.querySelector(".CodeMirror, .jp-Editor, .cm-editor")) {
      cellData.type = "code";
    } else {
      cellData.type = "markdown";
    }
    Object.assign(cellData, extractExecutionMeta(cell));
    const codeEl = cell.querySelector(".CodeMirror-code, .jp-Editor .cm-content, .cm-editor .cm-content, .input_area pre, .jp-InputArea-editor pre, textarea");
    cellData.source = codeEl ? codeEl.innerText.trim() : "";
    const outputEl = cell.querySelector(".output, .jp-OutputArea, .output_area, .jp-Cell-outputArea");
    cellData.output = outputEl ? outputEl.innerText.trim() : "";
    cells.push(cellData);
  }
  return { title: document.title || "", cellCount: cells.length, cells: cells };
}

function getKernelStatus() {
  const statusEl = document.querySelector(
    "#site-content > div.sc-cvANaB.lntgBg > div > div.sc-hpEunQ.efgyYB > div > div.sc-NOWJl.jHHMmT"
  );
  if (!statusEl) return null;
  const text = (statusEl.innerText || statusEl.textContent || "").trim();
  if (text.includes("Session started")) return "running";
  if (text.includes("off") && text.includes("run a cell to start")) return "off";
  return null;
}

function probeExecutionMetaFetch() {
  const promptSelector = ".jp-InputPrompt.jp-InputArea-prompt, .jp-InputPrompt, .input_prompt";
  return Array.from(document.querySelectorAll(".cell, .jp-Cell, .code_cell, .text_cell, .markdown_cell")).map((cell, index) => {
    const promptEl = cell.querySelector(promptSelector);
    const titleHost = promptEl || null;
    return {
      cellIndex: index + 1,
      promptFound: !!promptEl,
      promptText: promptEl ? String(promptEl.innerText || promptEl.textContent || "").trim() : "",
      promptTitle: titleHost
        ? String(
            titleHost.getAttribute("title") ||
            titleHost.title ||
            titleHost.getAttribute("aria-label") ||
            ""
          ).trim()
        : "",
    };
  });
}

function isTargetUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || ""));
    const host = parsed.hostname.toLowerCase();
    return (host === "kaggle.com" || host.endsWith(".kaggle.com")) && parsed.pathname.endsWith("/edit");
  } catch {
    return false;
  }
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
    files: ["markdown-it.min.js", "ui_injector.js", "prompt_observer.js"]
  }).catch(e => console.warn("UI Injection failed:", e));
}

// ── Bridge UI messages to the Native Host ────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Handle prompt observer signals locally to trigger an immediate re-scan (throttled)
  if (msg?.type === 'PROMPT_SIGNAL') {
    console.log(`[BG-SIGNAL] Received: "${msg.text}" from cell ${msg.cellIndex || '?'}`);
    const p = getPort();
    if (p) {
      p.postMessage({ ...msg, tabId: sender.tab?.id });
    }
    const now = Date.now();
    if (now - __lastPromptSignal > 250) {
      __lastPromptSignal = now;
      try { sendTabs(); } catch (e) { /* ignore */ }
    }
    return true;
  }

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
      return isTargetUrl(t.url);
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
              chrome.scripting.executeScript({
                target: { tabId: tab.id, allFrames: true },
                func: probeExecutionMetaFetch
              }, (probeResults) => {
                if (!chrome.runtime.lastError) {
                  const executionProbe = [];
                  for (const r of (probeResults || [])) {
                    if (Array.isArray(r?.result)) executionProbe.push(...r.result);
                  }
                  console.debug("[testing] execution meta probe:", executionProbe.slice(0, 5));
                }
              });
              chrome.scripting.executeScript({
                target: { tabId: tab.id, allFrames: false },
                func: getKernelStatus
              }, (statusResults) => {
                const kernelStatus = statusResults?.[0]?.result;
                getPort().postMessage({
                  type: "NOTEBOOK_DATA", tabUrl: tab.url, iframes: iframes,
                  tabId: tab.id,
                  title: notebookTitle, cellCount: allCells.length, cells: allCells,
                  kernelStatus: kernelStatus
                });
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
