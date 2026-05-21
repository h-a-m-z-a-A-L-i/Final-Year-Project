const HOST = "com.testing.tabprinter";

// Keep one persistent native port open to Python
let port = null;
let __lastPromptSignal = 0;

// State machine for tracking kernel state transitions per tab
const kernelStateByTab = {}; // tabId -> { lastStatus, lastEditorLoading, scenario, timestamp }

function classifyKernelScenario(tabId, detectedState) {
  const isFirstScan = !kernelStateByTab[tabId];
  const current = kernelStateByTab[tabId] || {};
  let scenario = current.scenario || "unknown";

  // Priority 1: Editor loading always resets the machine (covers page reloads from any state).
  if (detectedState.editorLoading) {
    scenario = "editor_loading";
    console.log(`[Tab ${tabId}] Transition: any → editor_loading`);
  }
  // Priority 2: First time we see this tab — no prior state, classify directly from DOM flags.
  else if (isFirstScan) {
    if (detectedState.hdd) {
      scenario = "scenario_3_reload_running_kernel";
      console.log(`[Tab ${tabId}] First-scan with HDD → scenario_3_reload_running_kernel`);
    } else if (detectedState.off) {
      scenario = "scenario_1_new_notebook_off";
      console.log(`[Tab ${tabId}] First-scan with off → scenario_1_new_notebook_off`);
    }
  }
  // Priority 3: Normal state transitions on subsequent polls.
  else {
    // Editor loading finished → kernel is off (new notebook never started)
    if (current.scenario === "editor_loading" && detectedState.off && !detectedState.hdd) {
      scenario = "scenario_1_new_notebook_off";
      console.log(`[Tab ${tabId}] Transition: editor_loading → scenario_1_new_notebook_off`);
    }
    // Editor loading finished → HDD already present (tab reload with running kernel)
    else if (current.scenario === "editor_loading" && detectedState.hdd && !detectedState.off) {
      scenario = "scenario_3_reload_running_kernel";
      console.log(`[Tab ${tabId}] Transition: editor_loading → scenario_3_reload_running_kernel`);
    }
    // Kernel off → user clicked Run (fresh start)
    else if (current.scenario === "scenario_1_new_notebook_off" && detectedState.hdd) {
      scenario = "scenario_2_fresh_kernel_started";
      console.log(`[Tab ${tabId}] Transition: scenario_1 → scenario_2_fresh_kernel_started`);
    }
    // Kernel turned OFF from a running state → badge must update back to off
    else if (
      (current.scenario === "scenario_2_fresh_kernel_started" ||
       current.scenario === "scenario_3_reload_running_kernel") &&
      detectedState.off && !detectedState.hdd
    ) {
      scenario = "scenario_1_new_notebook_off";
      console.log(`[Tab ${tabId}] Transition: ${current.scenario} → scenario_1_new_notebook_off (kernel turned off)`);
    }
  }

  kernelStateByTab[tabId] = {
    lastStatus: detectedState.status,
    lastEditorLoading: detectedState.editorLoading,
    scenario: scenario,
    timestamp: Date.now(),
  };

  console.log(`[Tab ${tabId}] State: editorLoading=${detectedState.editorLoading}, off=${detectedState.off}, hdd=${detectedState.hdd} → scenario=${scenario}`);

  return scenario;
}

function setBadgeForScenario(tabId, scenario) {
  const badgeConfig = {
    "scenario_1_new_notebook_off": { text: "1️⃣OFF", color: "#FF6B6B" },
    "scenario_2_fresh_kernel_started": { text: "2️⃣RUN", color: "#4ECDC4" },
    "scenario_3_reload_running_kernel": { text: "3️⃣RLD", color: "#45B7D1" },
    "editor_loading": { text: "⏳", color: "#FFA07A" },
    "unknown": { text: "?", color: "#888888" },
  };
  
  const config = badgeConfig[scenario] || badgeConfig["unknown"];
  
  try {
    chrome.action.setBadgeText({ text: config.text, tabId: tabId });
    chrome.action.setBadgeBackgroundColor({ color: config.color, tabId: tabId });
    console.log(`[Badge] Tab ${tabId} set to "${config.text}" with color ${config.color}`);
  } catch (e) {
    console.error(`[Badge Error] Failed to set badge for tab ${tabId}:`, e);
  }
}


function scrapeNotebook() {
  const cells = [];
  const seen = new Set();
  const cellElements = [];

  const collectFromRoot = (root) => {
    if (!root || typeof root.querySelectorAll !== "function") return;
    const selectors = ".cell, .jp-Cell, .code_cell, .text_cell, .markdown_cell, [data-windowed-list-index]";
    for (const el of root.querySelectorAll(selectors)) {
      if (!seen.has(el)) {
        seen.add(el);
        cellElements.push(el);
      }
    }

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node && node.shadowRoot) {
        collectFromRoot(node.shadowRoot);
      }
      if (node && node.tagName === "IFRAME") {
        try {
          if (node.contentDocument) {
            collectFromRoot(node.contentDocument);
          }
        } catch {
          // ignore cross-origin or detached frames
        }
      }
    }
  };

  collectFromRoot(document);
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
    } else if (Number.isFinite(executionOrder)) {
      // No transient button, but a prompt number exists.
      // Execution detection for polling gaps — stale-data filtering is done in host.py.
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
    
    // Extract index from data-windowed-list-index for fault-tolerant identification
    const cellIndex = cell.getAttribute("data-windowed-list-index");
    if (cellIndex !== null) {
      cellData.index = parseInt(cellIndex, 10);
    }
    
    // Cell type detection: check for jp-MarkdownCell first, then code indicators
    const isMarkdownCollapsed = cell.classList.contains("jp-MarkdownCell") && cell.classList.contains("jp-mod-selected") && cell.classList.contains("jp-mod-active");

    if (cell.classList.contains("jp-MarkdownCell")) {
      cellData.type = "markdown";
      // For markdown cells, extract rendered text content only
      const markdownContent = cell.querySelector(".jp-Cell-outputArea, .jp-OutputArea, [class*='output'], .cell-content");
      cellData.input = markdownContent ? markdownContent.innerText.trim() : cell.innerText.trim();
      // Add state indicator using the distinct markdown cell class signatures
      cellData.state = isMarkdownCollapsed ? "collapsed" : "open";
      // Markdown cells have no execution metadata
    } else if (cell.classList.contains("code_cell") || cell.classList.contains("jp-CodeCell") || cell.querySelector(".CodeMirror, .jp-Editor, .cm-editor")) {
      cellData.type = "code";
      const codeEl = cell.querySelector(".CodeMirror-code, .jp-Editor .cm-content, .cm-editor .cm-content, .input_area pre, .jp-InputArea-editor pre, textarea");
      cellData.input = codeEl ? codeEl.innerText.trim() : "";
      
      // Execution metadata (for code cells only)
      Object.assign(cellData, extractExecutionMeta(cell));
      
      // Output extraction (for code cells only)
      const outputEl = cell.querySelector(".output, .jp-OutputArea, .output_area, .jp-Cell-outputArea");
      cellData.output = outputEl ? outputEl.innerText.trim() : "";
    } else {
      cellData.type = "markdown";
      const markdownContent = cell.querySelector(".jp-Cell-outputArea, .jp-OutputArea, [class*='output'], .cell-content");
      cellData.input = markdownContent ? markdownContent.innerText.trim() : cell.innerText.trim();
      // Add state indicator for fallback markdown detection
      cellData.state = cell.classList.contains("jp-mod-selected") && cell.classList.contains("jp-mod-active") ? "collapsed" : "open";
      // Markdown cells have no execution metadata
    }
    
    cells.push(cellData);
  }
  return { title: document.title || "", cellCount: cells.length, cells: cells };
}

function clickNotebookCellByIndex(cellIndex, options = {}) {
  const targetIndex = Number(cellIndex);
  if (!Number.isInteger(targetIndex) || targetIndex < 0) {
    return { ok: false, error: "Invalid cell index." };
  }

  const shouldScroll = options.scrollIntoView !== false;

  // Find the cell wrapper by data-windowed-list-index
  // First try main document, then search inside iframes
  let cell = document.querySelector(`[data-windowed-list-index="${targetIndex}"]`);
  
  if (!cell) {
    // Search inside all iframes (notebook is often in an iframe)
    const iframes = document.querySelectorAll("iframe");
    for (const iframe of iframes) {
      try {
        if (iframe.contentDocument) {
          cell = iframe.contentDocument.querySelector(`[data-windowed-list-index="${targetIndex}"]`);
          if (cell) {
            console.log("[clickNotebookCellByIndex] Found cell inside iframe");
            break;
          }
        }
      } catch (e) {
        console.warn("[clickNotebookCellByIndex] Could not access iframe:", e);
      }
    }
  }
  
  if (!cell) {
    return { ok: false, error: `Cell index ${targetIndex} not found in main document or iframes.` };
  }

  console.log("[clickNotebookCellByIndex] Found cell wrapper", cell);

  // Strategy 1: Look for a button/run button inside the cell
  let clickTarget = cell.querySelector(
    "button[aria-label*='Run'], button[title*='Run'], button[title*='Execute'], " +
    ".execution-button, [data-test-id*='run'], " +
    ".cell-execute-button, " +
    "button[aria-label*='Execute']"
  );

  // Strategy 2: Look for the editor/input area (for clicking to edit)
  if (!clickTarget) {
    clickTarget = cell.querySelector(
      ".cm-editor, .cm-content, " +
      "[contenteditable='true'], " +
      ".jp-InputArea-editor, " +
      ".input_area pre, " +
      ".cell-content, " +
      "[role='textbox']"
    );
  }

  // Strategy 3: Look for any button or interactive element
  if (!clickTarget) {
    clickTarget = cell.querySelector("button, [role='button'], a, input, textarea, [contenteditable]");
  }

  // Fallback: click the cell wrapper itself
  if (!clickTarget) {
    clickTarget = cell;
  }

  console.log("[clickNotebookCellByIndex] Click target found", {
    tagName: clickTarget?.tagName,
    className: clickTarget?.className,
    innerHTML: String(clickTarget?.innerHTML || "").slice(0, 100),
  });

  try {
    if (shouldScroll && clickTarget?.scrollIntoView) {
      clickTarget.scrollIntoView({ block: "center", inline: "nearest" });
    }

    if (clickTarget?.focus) {
      clickTarget.focus({ preventScroll: true });
    }

    // Dispatch comprehensive event sequence for React
    const rect = clickTarget.getBoundingClientRect?.();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: rect?.left + (rect?.width || 0) / 2,
      clientY: rect?.top + (rect?.height || 0) / 2,
      button: 0,
      view: window,
    };

    // Fire synthetic events that React listens to
    clickTarget.dispatchEvent?.(new MouseEvent("mouseenter", eventInit));
    clickTarget.dispatchEvent?.(new MouseEvent("mouseover", eventInit));
    clickTarget.dispatchEvent?.(new MouseEvent("mousedown", eventInit));
    clickTarget.dispatchEvent?.(new MouseEvent("mouseup", eventInit));
    clickTarget.dispatchEvent?.(new MouseEvent("click", eventInit));

    console.log("[clickNotebookCellByIndex] Events dispatched successfully");

    return {
      ok: true,
      cellIndex: targetIndex,
      clickedElement: clickTarget?.tagName?.toLowerCase(),
      strategy: "React-aware multi-strategy",
    };
  } catch (error) {
    console.error("[clickNotebookCellByIndex] Error:", error);
    return { ok: false, error: error?.message || String(error) };
  }
}

// Ping content script to check if it's ready
function pingContentScript(tabId, retryCount = 0) {
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, { type: 'PING' }, (response) => {
      if (chrome.runtime.lastError) {
        console.log(`[Ping] Tab ${tabId} not ready (attempt ${retryCount + 1}):`, chrome.runtime.lastError.message);

        // On the first failure, try injecting the content script into the tab (helps already-open tabs)
        if (retryCount === 0) {
          try {
            chrome.scripting.executeScript({ target: { tabId: tabId, allFrames: true }, files: ["kernel_state_listener.js"] })
              .then(() => {
                console.log(`[Ping] Injected kernel_state_listener into tab ${tabId}, retrying ping...`);
                setTimeout(() => pingContentScript(tabId, retryCount + 1).then(resolve), 200);
              })
              .catch((injErr) => {
                console.warn(`[Ping] Injection failed for tab ${tabId}:`, injErr);
                if (retryCount < 4) setTimeout(() => pingContentScript(tabId, retryCount + 1).then(resolve), 150 * (retryCount + 1));
                else { console.log(`[Ping] Tab ${tabId} maxed out retries, will send anyway (fallback)`); resolve(true); }
              });
            return;
          } catch (e) {
            console.warn(`[Ping] Injection attempt threw for tab ${tabId}:`, e);
          }
        }

        if (retryCount < 4) {
          setTimeout(() => pingContentScript(tabId, retryCount + 1).then(resolve), 150 * (retryCount + 1));
        } else {
          console.log(`[Ping] Tab ${tabId} maxed out retries, will send anyway (fallback)`);
          resolve(true); // Fallback: assume ready after max retries
        }
      } else {
        console.log(`[Ping] Tab ${tabId} is ready with response:`, response);
        resolve(true);
      }
    });
  });
}

// Send kernel state to content script with retry
async function sendKernelStateToTab(tabId, kernelData) {
  let retryCount = 0;
  const maxRetries = 3;

  const attempt = () => {
    return new Promise((resolve) => {
      chrome.tabs.sendMessage(tabId, kernelData, (response) => {
        if (chrome.runtime.lastError) {
          const errorMsg = chrome.runtime.lastError.message;
          console.log(`[SendState] Tab ${tabId} failed (attempt ${retryCount + 1}): ${errorMsg}`);
          // Try injecting content script on first failure (helps already-open tabs)
          if (retryCount === 0) {
            chrome.scripting.executeScript({ target: { tabId: tabId, allFrames: true }, files: ["kernel_state_listener.js"] })
              .then(() => {
                console.log(`[SendState] Injected kernel_state_listener into tab ${tabId}, retrying send...`);
                retryCount++;
                setTimeout(() => attempt().then(resolve), 250);
              })
              .catch((injErr) => {
                console.warn(`[SendState] Injection failed for tab ${tabId}:`, injErr);
                if (retryCount < maxRetries) {
                  retryCount++;
                  setTimeout(() => {
                    console.log(`[SendState] Retrying tab ${tabId} (${retryCount}/${maxRetries})...`);
                    attempt().then(resolve);
                  }, 200 * retryCount);
                } else {
                  resolve(false);
                }
              });
            return;
          }

          if (retryCount < maxRetries) {
            retryCount++;
            setTimeout(() => {
              console.log(`[SendState] Retrying tab ${tabId} (${retryCount}/${maxRetries})...`);
              attempt().then(resolve);
            }, 200 * retryCount);
          } else {
            resolve(false);
          }
        } else {
          console.log(`[SendState] Tab ${tabId} acknowledged:`, response);
          resolve(true);
        }
      });
    });
  };

  return attempt();
}

function getKernelStatus() {
  const statusEl = document.querySelector(
    "#site-content > div.sc-cvANaB.lntgBg > div > div.sc-hpEunQ.efgyYB > div > div.sc-NOWJl.jHHMmT"
  );
  const activeEl = document.querySelector(
    "#site-content > div.sc-cvANaB.lntgBg > div > div.sc-hpEunQ.efgyYB > div > div.sc-NOWJl.jHHMmT > button.sc-NoPZx.sc-anfIT.cgepwS.fqcZPa > div:nth-child(2)"
  );
  
  let statusText = (statusEl?.innerText || statusEl?.textContent || "").trim();
  let activeText = (activeEl?.innerText || activeEl?.textContent || "").trim();
  
  console.log('[getKernelStatus] Initial query - statusText:', statusText, 'activeText:', activeText);
  
  // Fallback: search entire page for these text patterns if not found
  if (statusText.length === 0 && activeText.length === 0) {
    const bodyText = document.body.innerText || document.body.textContent || "";
    console.log('[getKernelStatus] Using fallback - searching page body');
    statusText = bodyText;
    activeText = bodyText;
  }

  const hasEditorLoading = /Editor\s+loading/i.test(statusText) || /Editor\s+loading/i.test(activeText);
  const hasOff = /off\s*\(run a cell to start\)/i.test(statusText) || /off\s*\(run a cell to start\)/i.test(activeText) || /\bDraft Session Off\b/i.test(statusText) || /(^|\s)off(\s|$)/i.test(statusEl?.innerText || "");
  let hasHDD = /\bHDD\b/i.test(activeText) || /\bHDD\b/i.test(statusText);
  const hasSessionStarted = /Session started/i.test(statusText) || /Session started/i.test(activeText);

  // Priority: "off" is the authoritative kernel state. When the body-text fallback
  // is active, "HDD" can appear in unrelated page sections. Suppress it.
  if (hasOff) {
    hasHDD = false;
  }

  console.log('[getKernelStatus] Flags detected - editorLoading:', hasEditorLoading, 'off:', hasOff, 'hdd:', hasHDD, 'sessionStarted:', hasSessionStarted);

  return {
    status: (() => {
      if (hasOff) return "off";
      if (hasHDD || hasSessionStarted || activeText) return "running";
      return null;
    })(),
    editorLoading: hasEditorLoading,
    off: hasOff,
    hdd: hasHDD,
    statusText: statusText.substring(0, 200),
    activeText: activeText.substring(0, 200),
  };
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

function normalizeNotebookUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || ""));
    const path = (parsed.pathname || "/").replace(/\/+$/, "") || "/";
    return `${parsed.protocol}//${parsed.host}${path}`.toLowerCase();
  } catch {
    return String(rawUrl || "").split("#", 1)[0].split("?", 1)[0].replace(/\/+$/, "").toLowerCase();
  }
}

function resolveTabIdForUrl(rawUrl, sender, callback) {
  if (typeof sender?.tab?.id === "number") {
    callback(sender.tab.id);
    return;
  }

  const normalized = normalizeNotebookUrl(rawUrl);
  if (!normalized) {
    callback(null);
    return;
  }

  chrome.tabs.query({}, (tabs) => {
    if (chrome.runtime.lastError) {
      callback(null);
      return;
    }

    const match = (tabs || []).find((tab) => normalizeNotebookUrl(tab.url) === normalized);
    callback(typeof match?.id === "number" ? match.id : null);
  });
}

function getPort() {

  if (port) return port;
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener((msg) => {
    console.log("Python says:", msg);

    const dispatchToFrames = (tabId, payload, onResult) => {
      chrome.webNavigation.getAllFrames({ tabId }, async (frames) => {
        if (chrome.runtime.lastError) {
          chrome.tabs.sendMessage(tabId, payload, (response) => {
            const lastError = chrome.runtime.lastError;
            if (lastError) {
              onResult({ ok: false, error: lastError.message || String(lastError) });
              return;
            }
            onResult(response?.result || { ok: false, error: "No response from content script." });
          });
          return;
        }

        const orderedFrames = Array.isArray(frames) ? frames.slice().sort((a, b) => (a.frameId || 0) - (b.frameId || 0)) : [];
        let lastFailure = null;

        for (const frame of orderedFrames) {
          const response = await new Promise((resolve) => {
            chrome.tabs.sendMessage(tabId, payload, { frameId: frame.frameId }, (reply) => {
              const lastError = chrome.runtime.lastError;
              if (lastError) {
                resolve({ ok: false, error: lastError.message || String(lastError), frameId: frame.frameId });
                return;
              }
              resolve(reply?.result || { ok: false, error: "No response from content script.", frameId: frame.frameId });
            });
          });

          const isKeyDispatch = payload?.type === "SEND_KEY" || payload?.type === "SEND_KEYS";
          const landedOnIframe =
            isKeyDispatch &&
            String(response?.tagName || "").toUpperCase() === "IFRAME";

          if (landedOnIframe) {
            lastFailure = {
              ok: false,
              error: "Key event landed on iframe element; trying next frame.",
              frameId: frame.frameId,
              tagName: response?.tagName,
              key: response?.key,
            };
            continue;
          }

          if (response?.ok) {
            onResult(response);
            return;
          }

          lastFailure = response;
        }

        onResult(lastFailure || { ok: false, error: "No frame accepted the command." });
      });
    };

    if (msg?.type === "SELECT_CELL_BY_INDEX" && typeof msg?.tabId === "number") {
      const payload = {
        type: "SELECT_CELL_BY_INDEX",
        cellIndex: msg.cellIndex,
        scrollIntoView: msg.scrollIntoView,
        requestId: msg.requestId,
        url: msg.url,
      };

      dispatchToFrames(msg.tabId, payload, (result) => {
        getPort().postMessage({
          type: result.ok ? "SELECT_CELL_RESULT" : "SELECT_CELL_ERROR",
          tabId: msg.tabId,
          url: msg.url,
          requestId: msg.requestId,
          cellIndex: msg.cellIndex,
          result,
        });
      });
      return;
    }

    if (msg?.type === "INSERT_CELL" && typeof msg?.tabId === "number") {
      const payload = {
        type: "INSERT_CELL",
        direction: msg.direction,
        toMarkdown: msg.toMarkdown === true,
        markdownDelayMs: msg.markdownDelayMs,
        requestId: msg.requestId,
        url: msg.url,
      };

      dispatchToFrames(msg.tabId, payload, (result) => {
        getPort().postMessage({
          type: result.ok ? "INSERT_CELL_RESULT" : "INSERT_CELL_ERROR",
          tabId: msg.tabId,
          url: msg.url,
          requestId: msg.requestId,
          direction: msg.direction,
          result,
        });
      });
      return;
    }

    if (msg?.type === "CLICK_CELL_BY_INDEX" && typeof msg?.tabId === "number") {
      const payload = {
        type: "CLICK_CELL_BY_INDEX",
        cellIndex: msg.cellIndex,
        scrollIntoView: msg.scrollIntoView,
        runCell: msg.runCell,
        requestId: msg.requestId,
        url: msg.url,
      };

      dispatchToFrames(msg.tabId, payload, (result) => {
        getPort().postMessage({
          type: result.ok ? "CLICK_CELL_RESULT" : "CLICK_CELL_ERROR",
          tabId: msg.tabId,
          url: msg.url,
          requestId: msg.requestId,
          cellIndex: msg.cellIndex,
          result,
        });
      });
      return;
    }

    if (msg?.type === "CLICK_SELECTOR" && typeof msg?.tabId === "number") {
      const payload = {
        type: "CLICK_SELECTOR",
        selector: msg.selector,
        requestId: msg.requestId,
        url: msg.url,
      };

      dispatchToFrames(msg.tabId, payload, (result) => {
        getPort().postMessage({
          type: result.ok ? "CLICK_SELECTOR_RESULT" : "CLICK_SELECTOR_ERROR",
          tabId: msg.tabId,
          url: msg.url,
          requestId: msg.requestId,
          selector: msg.selector,
          result,
        });
      });
      return;
    }

    if (msg?.type === "DELETE_CELL" && typeof msg?.tabId === "number") {
      const payload = {
        type: "DELETE_CELL",
        requestId: msg.requestId,
        url: msg.url,
      };

      dispatchToFrames(msg.tabId, payload, (result) => {
        getPort().postMessage({
          type: result.ok ? "DELETE_CELL_RESULT" : "DELETE_CELL_ERROR",
          tabId: msg.tabId,
          url: msg.url,
          requestId: msg.requestId,
          result,
        });
      });
      return;
    }

    if (msg?.type === "SEND_KEY" && typeof msg?.tabId === "number") {
      const payload = {
        type: "SEND_KEY",
        key: msg.key,
        requestId: msg.requestId,
        url: msg.url,
      };

      dispatchToFrames(msg.tabId, payload, (result) => {
        getPort().postMessage({
          type: result.ok ? "SEND_KEY_RESULT" : "SEND_KEY_ERROR",
          tabId: msg.tabId,
          url: msg.url,
          requestId: msg.requestId,
          key: msg.key,
          result,
        });
      });
      return;
    }

    if (msg?.type === "SEND_KEYS" && typeof msg?.tabId === "number") {
      const payload = {
        type: "SEND_KEYS",
        keys: msg.keys,
        requestId: msg.requestId,
        url: msg.url,
      };

      dispatchToFrames(msg.tabId, payload, (result) => {
        getPort().postMessage({
          type: result.ok ? "SEND_KEYS_RESULT" : "SEND_KEYS_ERROR",
          tabId: msg.tabId,
          url: msg.url,
          requestId: msg.requestId,
          keys: msg.keys,
          result,
        });
      });
      return;
    }

    // Only deliver tab-scoped data to the originating tab to avoid cross-tab leakage.
    if (typeof msg?.tabId === "number") {
      chrome.tabs.sendMessage(msg.tabId, msg);
      return;
    }

    // Forward untargeted host messages by resolving the tab from the URL.
    if (["CHAT_RESPONSE", "HISTORY_DATA", "HISTORY_CLEARED", "GRAPH_DATA"].includes(msg?.type)) {
      const notebookUrl = msg?.url || msg?.tabUrl || null;
      if (!notebookUrl) {
        console.warn("No URL present on untargeted host message:", msg?.type);
        return;
      }
      resolveTabIdForUrl(notebookUrl, null, (resolvedTabId) => {
        if (typeof resolvedTabId === 'number') {
          chrome.tabs.sendMessage(resolvedTabId, msg);
        } else {
          console.warn("Could not resolve tab for host message:", msg?.type, notebookUrl);
        }
      });
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

  chrome.scripting.executeScript({
    target: { tabId: tabId, allFrames: true },
    files: ["prompt_observer.js"]
  }).catch(e => console.warn("Prompt observer injection failed:", e));

  // Ensure kernel_state_listener is present for already-open tabs
  chrome.scripting.executeScript({
    target: { tabId: tabId, allFrames: true },
    files: ["kernel_state_listener.js"]
  }).catch(e => console.warn("kernel_state_listener injection failed:", e));
}

// ── Bridge UI messages to the Native Host ────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Handle prompt observer signals locally to trigger an immediate re-scan (throttled)
  if (msg?.type === 'PROMPT_SIGNAL') {
    console.log(`[BG-SIGNAL] Received: "${msg.text}" from cell ${msg.cellIndex || '?'}`);
    const p = getPort();
    if (p) {
      p.postMessage({
        ...msg,
        tabId: sender.tab?.id,
        tabUrl: sender.tab?.url,
      });
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
    if (msg?.type === "GET_GRAPH" && typeof msg?.tabId !== "number") {
      resolveTabIdForUrl(msg?.url, sender, (tabId) => {
        if (typeof tabId === "number") {
          p.postMessage({ ...msg, tabId });
        }
      });
      return true;
    }

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

    console.log(`[sendTabs] Found ${targets.length} target tabs to process`);

    for (const tab of targets) {
      console.log(`[sendTabs] Processing tab ${tab.id}: ${tab.url}`);
      
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
          if (chrome.runtime.lastError) {
            console.log(`[sendTabs] Tab ${tab.id} - iframe query error:`, chrome.runtime.lastError);
            return;
          }
          const iframes = results?.[0]?.result || [];
          chrome.scripting.executeScript({
              target: { tabId: tab.id, allFrames: true },
              func: scrapeNotebook
          }, (scrapeResults) => {
              if (chrome.runtime.lastError) {
                console.log(`[sendTabs] Tab ${tab.id} - scrape error:`, chrome.runtime.lastError);
                return;
              }
              const allCells = [];
              const seenCells = new Set();
              let notebookTitle = "";
              for (const r of (scrapeResults || [])) {
                if (r?.result?.cellCount > 0) {
                  for (const cell of r.result.cells) {
                    const key = [
                      String(cell?.index ?? ""),
                      String(cell?.type ?? ""),
                      String(cell?.input ?? ""),
                      String(cell?.output ?? ""),
                    ].join("||");
                    if (seenCells.has(key)) continue;
                    seenCells.add(key);
                    allCells.push(cell);
                  }
                  if (r.result.title) notebookTitle = r.result.title;
                }
              }
              console.log(`[sendTabs] Tab ${tab.id} - scraped ${allCells.length} unique cells from ${Array.isArray(scrapeResults) ? scrapeResults.length : 0} frame results`);
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
                const detectedState = statusResults?.[0]?.result || { status: null, editorLoading: false, off: false, hdd: false };
                const scenario = classifyKernelScenario(tab.id, detectedState);
                setBadgeForScenario(tab.id, scenario);
                
                const kernelStatus = detectedState.status;
                
                console.log(`[Kernel-Detector] Tab ${tab.id}: scenario="${scenario}", status="${kernelStatus}"`, detectedState);
                
                // Send to Python host
                getPort().postMessage({
                  type: "NOTEBOOK_DATA", tabUrl: tab.url, iframes: iframes,
                  tabId: tab.id,
                  title: notebookTitle, cellCount: allCells.length, cells: allCells,
                  kernelStatus: kernelStatus,
                  kernelScenario: scenario,
                  kernelState: detectedState
                });
                
                // Send to content script on the tab to relay to page with ping-based handshake
                (async () => {
                  console.log(`[Background] Pinging tab ${tab.id} to check if content script is ready...`);
                  const isReady = await pingContentScript(tab.id);
                  
                  if (isReady) {
                    console.log(`[Background] Tab ${tab.id} is ready, sending NOTEBOOK_DATA...`);
                    const sent = await sendKernelStateToTab(tab.id, {
                      type: "NOTEBOOK_DATA",
                      tabUrl: tab.url,
                      tabId: tab.id,
                      kernelStatus: kernelStatus,
                      kernelScenario: scenario,
                      kernelState: detectedState
                    });
                    console.log(`[Background] Tab ${tab.id} kernel state send result: ${sent}`);
                  } else {
                    console.log(`[Background] Tab ${tab.id} content script never became ready`);
                  }
                })();
              });
          });
      });
    }
  });
}

// Events
chrome.runtime.onInstalled.addListener(() => {
  console.log("[Init] Extension installed/updated, initializing badges");
  sendTabs();
});

chrome.runtime.onStartup.addListener(() => {
  console.log("[Init] Browser startup, re-scanning tabs");
  sendTabs();
});

setInterval(sendTabs, 5000);

chrome.tabs.onUpdated.addListener((id, info) => {
  console.log(`[Tab ${id}] Updated: ${info.status}`);
  // When page finishes loading (including reloads), wait a bit for DOM to settle, then re-scan
  if (info.status === "complete") {
    console.log(`[Tab ${id}] Page complete, scheduling re-scan after 500ms...`);
    setTimeout(() => {
      console.log(`[Tab ${id}] Re-scanning after page load...`);
      sendTabs();
    }, 500);
  }
});

chrome.tabs.onRemoved.addListener((id) => {
  delete kernelStateByTab[id];
  console.log(`[Tab ${id}] Removed from state tracking`);
});

console.log("[Background] Service worker loaded, calling initial sendTabs()");
sendTabs();
