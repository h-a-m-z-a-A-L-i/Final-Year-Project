const HOST = "com.testing.tabprinter";

// Keep one persistent native port open to Python
let port = null;
let __lastPromptSignal = 0;

// State machine for tracking kernel state transitions per tab
const kernelStateByTab = {}; // tabId -> { lastStatus, lastEditorLoading, scenario, timestamp }

const KERNEL_SCENARIO_OFF = "scenario_1_new_notebook_off";
const KERNEL_SCENARIO_ON = "scenario_2_kernel_on";

function classifyKernelScenario(tabId, detectedState) {
  const current = kernelStateByTab[tabId] || {};

  // During editor loading keep the last stable scenario (reload while kernel on).
  if (detectedState.editorLoading) {
    kernelStateByTab[tabId] = {
      lastStatus: detectedState.status,
      lastEditorLoading: true,
      scenario: current.scenario || "editor_loading",
      timestamp: Date.now(),
    };
    console.log(`[Tab ${tabId}] Editor loading — holding scenario=${current.scenario || "editor_loading"}`);
    return current.scenario || "editor_loading";
  }

  const scenario = detectedState.off ? KERNEL_SCENARIO_OFF : KERNEL_SCENARIO_ON;

  kernelStateByTab[tabId] = {
    lastStatus: detectedState.status,
    lastEditorLoading: false,
    scenario: scenario,
    timestamp: Date.now(),
  };

  console.log(`[Tab ${tabId}] State: off=${detectedState.off} → scenario=${scenario}`);

  return scenario;
}

function setBadgeForScenario(tabId, scenario) {
  const badgeConfig = {
    "scenario_1_new_notebook_off": { text: "OFF", color: "#FF6B6B" },
    "scenario_2_kernel_on": { text: "ON", color: "#4ECDC4" },
    "scenario_2_fresh_kernel_started": { text: "ON", color: "#4ECDC4" },
    "scenario_3_reload_running_kernel": { text: "ON", color: "#4ECDC4" },
    "editor_loading": { text: "...", color: "#FFA07A" },
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


function domToAppIndex(domIndex) {
  const n = Number(domIndex);
  return Number.isFinite(n) ? n + 1 : null;
}

function appToDomIndex(appIndex) {
  const n = Number(appIndex);
  return Number.isFinite(n) ? n - 1 : null;
}

function scrapeNotebook() {
  const domToAppIndex = (domIndex) => {
    const n = Number(domIndex);
    return Number.isFinite(n) ? n + 1 : null;
  };

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
    // Scrape execution metadata from Jupyter DOM (prompt numbers, run buttons, titles).
    const KERNEL_EXECUTION_METADATA_ENABLED = true;
    if (!KERNEL_EXECUTION_METADATA_ENABLED) {
      return {
        execution_order: null,
        execution_title: "",
        execution_status: "idle",
        execution_signal: "",
      };
    }
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
  const extractCodeCellOutput = (cell) => {
    const wrapperSelectors = [
      ".jp-Cell-outputWrapper",
      ".jp-Cell-outputArea",
      ".jp-OutputArea",
      ".output_area",
      ".output_wrapper",
    ];
    for (const sel of wrapperSelectors) {
      const el = cell.querySelector(sel);
      if (el) {
        const text = (el.innerText || "").trim();
        if (text) return text;
      }
    }
    const chunks = [];
    const seen = new Set();
    const pieceSelectors = [
      ".jp-OutputArea-output",
      ".output_subarea",
      ".jp-RenderedText",
      ".jp-RenderedHTMLCommon",
      ".output_text",
      ".stream.stdout",
      ".stream.stderr",
      "pre",
    ];
    for (const sel of pieceSelectors) {
      cell.querySelectorAll(sel).forEach((node) => {
        if (!(node instanceof HTMLElement)) return;
        const text = (node.innerText || "").trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        chunks.push(text);
      });
    }
    return chunks.join("\n").trim();
  };

  for (const [i, cell] of cellElements.entries()) {
    const cellData = {};
    
    // 1-based index for JSON/tools; DOM windowed-list index is 0-based.
    const cellIndex = cell.getAttribute("data-windowed-list-index");
    if (cellIndex !== null) {
      cellData.index = domToAppIndex(parseInt(cellIndex, 10));
    } else {
      cellData.index = i + 1;
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
      cellData.output = extractCodeCellOutput(cell);
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
  const appIndex = Number(cellIndex);
  if (!Number.isInteger(appIndex) || appIndex < 1) {
    return { ok: false, error: "Invalid cell index." };
  }
  const targetIndex = appIndex - 1;

  const shouldScroll = options.scrollIntoView !== false;

  // Find the cell wrapper by data-windowed-list-index (0-based DOM index)
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
    return { ok: false, error: `Cell index ${appIndex} not found in main document or iframes.` };
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
      cellIndex: appIndex,
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
            chrome.scripting.executeScript({ target: { tabId: tabId, allFrames: true }, files: ["cell_index_utils.js", "kernel_state_listener.js"] })
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
            chrome.scripting.executeScript({ target: { tabId: tabId, allFrames: true }, files: ["cell_index_utils.js", "kernel_state_listener.js"] })
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
  const offLabelEl = document.querySelector(
    "#site-content > div.sc-tkEuq.fTdalQ > div > div.sc-kUrMZp.iZSzNa > div > div.sc-UobXP.igDrFI > button.sc-bSDwOd.sc-cFxKLN.LolHj.fzHoiL > div > div.sc-fQpbHj.QscAk"
  );
  const statusEl = document.querySelector(
    "#site-content > div.sc-cvANaB.lntgBg > div > div.sc-hpEunQ.efgyYB > div > div.sc-NOWJl.jHHMmT"
  );
  const activeEl = document.querySelector(
    "#site-content > div.sc-cvANaB.lntgBg > div > div.sc-hpEunQ.efgyYB > div > div.sc-NOWJl.jHHMmT > button.sc-NoPZx.sc-anfIT.cgepwS.fqcZPa > div:nth-child(2)"
  );

  const offLabelText = (offLabelEl?.innerText || offLabelEl?.textContent || "").trim();
  let statusText = (statusEl?.innerText || statusEl?.textContent || "").trim();
  let activeText = (activeEl?.innerText || activeEl?.textContent || "").trim();

  if (statusText.length === 0 && activeText.length === 0) {
    const bodyText = document.body.innerText || document.body.textContent || "";
    statusText = bodyText;
    activeText = bodyText;
  }

  const hasEditorLoading = /Editor\s+loading/i.test(statusText) || /Editor\s+loading/i.test(activeText);
  const hasOff =
    /off\s*\(run a cell to start\)/i.test(offLabelText) ||
    /off\s*\(run a cell to start\)/i.test(statusText) ||
    /off\s*\(run a cell to start\)/i.test(activeText) ||
    /\bDraft Session Off\b/i.test(statusText);

  console.log('[getKernelStatus] offLabel:', offLabelText.substring(0, 80), 'editorLoading:', hasEditorLoading, 'off:', hasOff);

  return {
    status: hasOff ? "off" : (hasEditorLoading ? null : "running"),
    editorLoading: hasEditorLoading,
    off: hasOff,
    hdd: !hasOff && !hasEditorLoading,
    statusText: (offLabelText || statusText).substring(0, 200),
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

function isNotebookEditUrl(rawUrl) {
  const normalized = normalizeNotebookUrl(rawUrl);
  if (!normalized) return false;
  return /\/edit$/i.test(normalized) || /jupyter-proxy\.kaggle\.net/i.test(normalized);
}

function notebookSlugFromUrl(rawUrl) {
  const normalized = normalizeNotebookUrl(rawUrl);
  const match = normalized.match(/\/code\/([^/]+)\/([^/]+)/);
  return match ? `${match[1]}/${match[2]}`.toLowerCase() : null;
}

function notebookUrlVariants(rawUrl) {
  const normalized = normalizeNotebookUrl(rawUrl);
  const variants = new Set([normalized]);
  try {
    const parsed = new URL(normalized);
    if (parsed.host === "www.kaggle.com") {
      variants.add(`${parsed.protocol}//kaggle.com${parsed.pathname}`);
    } else if (parsed.host === "kaggle.com") {
      variants.add(`${parsed.protocol}//www.kaggle.com${parsed.pathname}`);
    }
  } catch (_) {}
  return [...variants];
}

function urlsMatchTarget(tabUrl, targetUrl) {
  const tabNorm = normalizeNotebookUrl(tabUrl);
  const targetNorm = normalizeNotebookUrl(targetUrl);
  if (!tabNorm || !targetNorm) return false;
  if (tabNorm === targetNorm) return true;
  const tabVariants = new Set(notebookUrlVariants(tabUrl));
  return notebookUrlVariants(targetUrl).some((variant) => tabVariants.has(variant));
}

function findUniqueSlugTab(targetUrl, editTabs) {
  const reqSlug = notebookSlugFromUrl(targetUrl);
  if (!reqSlug) return null;
  const hits = (editTabs || []).filter(
    (tab) => isNotebookEditUrl(tab.url) && notebookSlugFromUrl(tab.url) === reqSlug,
  );
  if (hits.length === 1) return hits[0];
  const exact = hits.find((tab) => urlsMatchTarget(tab.url, targetUrl));
  return exact || null;
}

function queryNotebookEditTabs(callback) {
  // Scan every window so background /edit tabs are found (not only the active tab).
  chrome.tabs.query({}, (tabs) => {
    if (chrome.runtime.lastError) {
      callback([]);
      return;
    }
    callback((tabs || []).filter((tab) => isNotebookEditUrl(tab.url)));
  });
}

const notebookIdentityByTab = {};
const lastNotebookUrlByTab = {};
const tabIdByNotebookUrl = {};

function cacheTabIdForUrl(rawUrl, tabId) {
  const normalized = normalizeNotebookUrl(rawUrl);
  if (!normalized || typeof tabId !== "number") return;
  tabIdByNotebookUrl[normalized] = tabId;
}

function resolveTabIdForUrl(rawUrl, sender, callback, options = {}) {
  const finish = (tabId, resolvedUrl) => {
    if (typeof tabId === "number" && resolvedUrl) {
      for (const variant of notebookUrlVariants(resolvedUrl)) {
        tabIdByNotebookUrl[variant] = tabId;
      }
    }
    callback(
      typeof tabId === "number" ? tabId : null,
      resolvedUrl ? normalizeNotebookUrl(resolvedUrl) : normalizeNotebookUrl(rawUrl),
    );
  };

  const failNoMatch = () => {
    queryNotebookEditTabs((editTabs) => {
      const openCount = editTabs.length;
      console.warn(
        "[resolveTabIdForUrl] no tab matches target URL:",
        normalizeNotebookUrl(rawUrl),
        `(open notebook /edit tabs: ${openCount})`,
      );
      finish(null, null);
    });
  };

  const resolveFromOpenTabs = () => {
    queryNotebookEditTabs((editTabs) => {
      const exactHits = editTabs.filter((tab) => urlsMatchTarget(tab.url, rawUrl));
      if (exactHits.length >= 1) {
        const pick = exactHits.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
        finish(pick.id, pick.url);
        return;
      }

      const slugHit = findUniqueSlugTab(rawUrl, editTabs);
      if (slugHit) {
        finish(slugHit.id, slugHit.url);
        return;
      }

      failNoMatch();
    });
  };

  if (typeof sender?.tab?.id === "number") {
    const senderUrl = sender.tab.url || rawUrl;
    if (urlsMatchTarget(senderUrl, rawUrl)) {
      finish(sender.tab.id, senderUrl);
    } else {
      failNoMatch();
    }
    return;
  }

  const normalized = normalizeNotebookUrl(rawUrl);
  if (!normalized) {
    finish(null, null);
    return;
  }

  const preferredTabId = options?.preferredTabId;
  if (typeof preferredTabId === "number" && preferredTabId > 0) {
    chrome.tabs.get(preferredTabId, (tab) => {
      if (!chrome.runtime.lastError && tab && urlsMatchTarget(tab.url, rawUrl)) {
        finish(preferredTabId, tab.url);
        return;
      }
      resolveFromOpenTabs();
    });
    return;
  }

  const cached = tabIdByNotebookUrl[normalized];
  if (typeof cached === "number") {
    chrome.tabs.get(cached, (tab) => {
      if (!chrome.runtime.lastError && tab && urlsMatchTarget(tab.url, rawUrl)) {
        finish(cached, tab.url);
        return;
      }
      delete tabIdByNotebookUrl[normalized];
      for (const variant of notebookUrlVariants(rawUrl)) {
        if (variant !== normalized) delete tabIdByNotebookUrl[variant];
      }
      resolveFromOpenTabs();
    });
    return;
  }

  resolveFromOpenTabs();
}

function uncacheTabId(tabId) {
  const rawUrl = lastNotebookUrlByTab[tabId];
  if (!rawUrl) return;
  const normalized = normalizeNotebookUrl(rawUrl);
  if (normalized && tabIdByNotebookUrl[normalized] === tabId) {
    delete tabIdByNotebookUrl[normalized];
  }
}

function fallbackNotebookIdentity(tabUrl) {
  const url = normalizeNotebookUrl(tabUrl);
  return { url, notebookId: null, notebookKey: url, userName: null, kernelSlug: null };
}

function identityMatchesTabUrl(identity, tabUrl) {
  if (!identity?.url || !tabUrl) return false;
  return normalizeNotebookUrl(identity.url) === normalizeNotebookUrl(tabUrl);
}

function attachNotebookIdentity(msg, tabId, tabUrl) {
  const currentUrl = normalizeNotebookUrl(tabUrl || msg.url || "");
  if (currentUrl) msg.url = currentUrl;

  const identity = typeof tabId === "number" ? notebookIdentityByTab[tabId] : null;
  if (!identity || !identityMatchesTabUrl(identity, currentUrl)) return msg;
  if (identity.notebookKey) msg.notebookKey = identity.notebookKey;
  if (identity.notebookId != null) msg.notebookId = identity.notebookId;
  return msg;
}

function resolveNotebookIdentityFromHost(tabUrl, tabId, callback) {
  const port = getPort();
  if (!port) {
    callback(fallbackNotebookIdentity(tabUrl));
    return;
  }
  let settled = false;
  const finish = (identity) => {
    if (settled) return;
    settled = true;
    try { port.onMessage.removeListener(onHostMessage); } catch (_) {}
    callback(identity);
  };
  const onHostMessage = (msg) => {
    if (msg?.type !== "NOTEBOOK_IDENTITY") return;
    if (typeof tabId === "number" && msg.tabId != null && msg.tabId !== tabId) return;
    const url = normalizeNotebookUrl(msg.url || tabUrl);
    finish({
      url,
      notebookId: msg.notebookId ?? null,
      notebookKey: String(msg.notebookKey || url).trim() || url,
      userName: null,
      kernelSlug: null,
    });
  };
  port.onMessage.addListener(onHostMessage);
  port.postMessage({
    type: "RESOLVE_NOTEBOOK_IDENTITY",
    url: normalizeNotebookUrl(tabUrl),
    tabId,
  });
  setTimeout(() => finish(fallbackNotebookIdentity(tabUrl)), 8000);
}

function resolveNotebookIdentity(tab, callback) {
  const tabId = tab.id;
  const tabUrl = normalizeNotebookUrl(tab.url);
  if (!tabUrl) {
    callback(fallbackNotebookIdentity(tab.url));
    return;
  }
  // Always resolve from host using the live /edit URL slug (authoritative).
  resolveNotebookIdentityFromHost(tabUrl, tabId, callback);
}

function rememberNotebookIdentity(tabId, identity) {
  const prev = notebookIdentityByTab[tabId];
  const prevUrl = lastNotebookUrlByTab[tabId];
  const newUrl = identity?.url || "";
  if (prevUrl && newUrl && prevUrl !== newUrl) {
    const port = getPort();
    if (port) {
      port.postMessage({
        type: "NOTEBOOK_URL_CHANGED",
        oldUrl: prevUrl,
        newUrl,
        notebookId: identity?.notebookId ?? null,
        tabId,
      });
    }
  }
  if (newUrl) lastNotebookUrlByTab[tabId] = newUrl;
  cacheTabIdForUrl(newUrl, tabId);
  notebookIdentityByTab[tabId] = identity;

  const keyChanged = String(prev?.notebookKey || "") !== String(identity?.notebookKey || "");
  const urlChanged = String(prevUrl || "") !== String(newUrl || "");
  if (keyChanged || urlChanged) {
    broadcastToTabFrames(tabId, {
      type: "NOTEBOOK_IDENTITY_UPDATED",
      url: identity?.url || "",
      notebookId: identity?.notebookId ?? null,
      notebookKey: identity?.notebookKey || identity?.url || "",
    });
  }
}

const NOTEBOOK_SCOPED_MSG_TYPES = new Set([
  "CHAT_REQUEST",
  "STOP_CHAT",
  "GET_HISTORY",
  "CLEAR_HISTORY",
  "GET_GRAPH",
  "GET_AGENTIC_SETTINGS",
  "SET_AGENTIC_SETTINGS",
]);

/** UI uses sendMessage callbacks; ack immediately and stream/reply via tab broadcasts. */
const ASYNC_NATIVE_MSG_TYPES = new Set([
  "CHAT_REQUEST",
  "STOP_CHAT",
  "GET_HISTORY",
  "CLEAR_HISTORY",
  "GET_GRAPH",
  "GET_AGENTIC_SETTINGS",
  "SET_AGENTIC_SETTINGS",
]);

function ackRuntimeMessage(sendResponse, payload = { ok: true, accepted: true }) {
  if (typeof sendResponse !== "function") return;
  try {
    sendResponse(payload);
  } catch (_) {}
}

const TAB_BROADCAST_TYPES = new Set([
  "CHAT_STREAM",
  "CHAT_STREAM_END",
  "CHAT_RESPONSE",
  "HISTORY_DATA",
  "HISTORY_CLEARED",
  "NOTEBOOK_IDENTITY_UPDATED",
  "AGENTIC_SETTINGS",
]);

function broadcastToTabFrames(tabId, payload) {
  chrome.webNavigation.getAllFrames({ tabId }, (frames) => {
    if (chrome.runtime.lastError || !Array.isArray(frames) || frames.length === 0) {
      chrome.tabs.sendMessage(tabId, payload).catch(() => {});
      return;
    }
    for (const frame of frames) {
      chrome.tabs.sendMessage(tabId, payload, { frameId: frame.frameId }).catch(() => {});
    }
  });
}

/** Map host command types to the result/error types Python's stdin reader expects. */
function botResultMessageType(commandType, ok) {
  const suffix = ok ? '_RESULT' : '_ERROR';
  const aliases = {
    CLICK_CELL_BY_INDEX: 'CLICK_CELL',
    SELECT_CELL_BY_INDEX: 'SELECT_CELL',
    RUN_CELL_BY_INDEX: 'RUN_CELL',
    CREATING_MARKDOWN_BY_INDEX: 'CREATING_MARKDOWN',
  };
  const base = aliases[commandType] || commandType;
  return `${base}${suffix}`;
}

function getPort() {

  if (port) return port;
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener((msg) => {
    console.log("Python says:", msg);

    if (typeof msg?.tab_id === "number" && msg.tab_id > 0 && typeof msg?.tabId !== "number") {
      msg.tabId = msg.tab_id;
    }

    if (msg?.type === "INSERT_CODE_CELL_RESULT") {
      const requestId = msg.requestId;
      if (requestId && __pendingInsertCode.has(requestId)) {
        const pending = __pendingInsertCode.get(requestId);
        __pendingInsertCode.delete(requestId);
        try {
          pending.sendResponse({
            ok: Boolean(msg.ok),
            result: msg.result,
            error: msg.error,
          });
        } catch (_) {}
      }
      if (typeof msg.tabId === "number") {
        chrome.tabs.sendMessage(msg.tabId, msg).catch(() => {});
      }
      return;
    }

    const dispatchToFrames = (tabId, payload, onResult) => {
      chrome.tabs.get(tabId, (tabInfo) => {
        if (chrome.runtime.lastError || !tabInfo) {
          onResult({ ok: false, error: "Target notebook tab is not available." });
          return;
        }
        if (tabInfo.discarded) {
          onResult({
            ok: false,
            error: "Target notebook tab was discarded by Chrome. Click the tab once to reload, then retry.",
          });
          return;
        }

        const isBackground = !tabInfo.active;
        const backgroundBoostMs = isBackground ? 600 : 0;
        const isEditOp = payload?.type === 'SET_CELL_CONTENT';

        const runDispatch = () => {
        chrome.webNavigation.getAllFrames({ tabId }, async (frames) => {
        if (chrome.runtime.lastError) {
          chrome.tabs.sendMessage(tabId, payload, (response) => {
            const lastError = chrome.runtime.lastError;
            if (lastError) {
              // Include diagnostics when failing early
              onResult({ ok: false, error: lastError.message || String(lastError), diagnostics: { frames: null, error: lastError.message } });
              return;
            }
            onResult(response?.result || { ok: false, error: "No response from content script.", diagnostics: { frames: null } });
          });
          return;
        }
        const orderedFrames = Array.isArray(frames) ? frames.slice().sort((a, b) => (a.frameId || 0) - (b.frameId || 0)) : [];
        let lastFailure = null;
        const frameAttempts = [];

        const isDeleteOp = payload?.type === 'DELETE_CELL';
        const isSelectOp = payload?.type === 'SELECT_CELL_BY_INDEX';
        const isInsertOp = payload?.type === 'INSERT_CELL';
        const isClickEditOp =
          payload?.type === 'CLICK_CELL_BY_INDEX' && payload?.runCell !== true;
        const isFastCellOp =
          (payload?.type === 'CLICK_CELL_BY_INDEX' && payload?.runCell === true)
          || payload?.type === 'RUN_CELL_BY_INDEX'
          || payload?.type === 'CREATING_MARKDOWN_BY_INDEX';
        const isRunOp =
          payload?.type === 'RUN_CELL_BY_INDEX'
          || (payload?.type === 'CLICK_CELL_BY_INDEX' && payload?.runCell === true);
        const frameTimeoutMs = isInsertOp
          ? Math.max(10000, Number(payload?.maxWaitMs) || 1500) + 2500 + backgroundBoostMs
          : isDeleteOp || isSelectOp || isClickEditOp
          ? Math.max(800, Number(payload?.maxWaitMs) || 400) + 400 + backgroundBoostMs
          : isFastCellOp
            ? Math.max(160, Number(payload?.maxWaitMs) || 160) + 80 + backgroundBoostMs
            : isEditOp
              ? Math.max(4500, Number(payload?.maxWaitMs) || 400) + 1200 + backgroundBoostMs
              : isRunOp
                ? Math.max(600, Number(payload?.maxWaitMs) || 240) + 500 + backgroundBoostMs
                : 12000;
        const useFrameRace = (isInsertOp || isFastCellOp || isEditOp || isRunOp || isDeleteOp || isSelectOp || isClickEditOp) && orderedFrames.length > 0;

        const raceFramesForSuccess = (frames) =>
          new Promise((resolve) => {
            if (!frames.length) {
              resolve({ ok: false, error: 'No frames in tab.' });
              return;
            }

            let settled = false;
            let remaining = frames.length;
            let lastFailure = { ok: false, error: 'No frame accepted the command.' };
            const attempts = [];

            const finishAll = () => {
              if (settled) return;
              settled = true;
              lastFailure.diagnostics = { frames: attempts, dispatch: 'race' };
              resolve(lastFailure);
            };

            for (const frame of frames) {
              sendToFrame(frame).then((response) => {
                if (settled) return;
                attempts.push({ frameId: frame.frameId, result: response });
                const verdict = evaluateFrameResponse(response);
                if (verdict.accept) {
                  settled = true;
                  verdict.success.diagnostics = { frames: attempts, dispatch: 'race' };
                  resolve(verdict.success);
                  return;
                }
                lastFailure = verdict.failure || lastFailure;
                remaining -= 1;
                if (remaining === 0) {
                  finishAll();
                }
              });
            }
          });

        const sendToFrame = (frame) =>
          new Promise((resolve) => {
            let settled = false;
            const finish = (value) => {
              if (settled) return;
              settled = true;
              clearTimeout(timer);
              resolve(value);
            };
            const timer = setTimeout(
              () => finish({ ok: false, error: 'frame response timeout', frameId: frame.frameId, frameTimeout: true }),
              frameTimeoutMs,
            );
            chrome.tabs.sendMessage(tabId, payload, { frameId: frame.frameId }, (reply) => {
              const lastError = chrome.runtime.lastError;
              if (lastError) {
                finish({ ok: false, error: lastError.message || String(lastError), frameId: frame.frameId });
                return;
              }
              const wrapped = reply && typeof reply === 'object' ? reply : {};
              const inner = wrapped.result;
              if (wrapped.ok && inner && typeof inner === 'object') {
                finish({ ...inner, ok: Boolean(inner.ok ?? true), frameId: frame.frameId });
                return;
              }
              if (wrapped.ok) {
                finish({ ok: true, ...wrapped, frameId: frame.frameId });
                return;
              }
              finish(
                inner && typeof inner === 'object'
                  ? { ...inner, frameId: frame.frameId }
                  : { ok: false, error: 'No response from content script.', frameId: frame.frameId },
              );
            });
          });

        const evaluateFrameResponse = (response) => {
          const isKeyDispatch = payload?.type === "SEND_KEY" || payload?.type === "SEND_KEYS";
          const landedOnIframe = isKeyDispatch && String(response?.tagName || "").toUpperCase() === "IFRAME";
          if (landedOnIframe) {
            return {
              accept: false,
              failure: {
                ok: false,
                error: "Key event landed on iframe element; trying next frame.",
                frameId: response?.frameId,
                tagName: response?.tagName,
                key: response?.key,
              },
            };
          }
          if (response?.frameSkip || response?.frameTimeout) {
            return { accept: false, failure: response };
          }
          if (response?.ok) {
            return { accept: true, success: response };
          }
          return { accept: false, failure: response };
        };

        if (useFrameRace) {
          const success = await raceFramesForSuccess(orderedFrames);
          onResult(success);
          return;
        }

        for (const frame of orderedFrames) {
          const response = await sendToFrame(frame);

          // record attempt
          frameAttempts.push({ frameId: frame.frameId, result: response });

          const verdict = evaluateFrameResponse(response);
          if (verdict.accept) {
            verdict.success.diagnostics = { frames: frameAttempts };
            onResult(verdict.success);
            return;
          }
          lastFailure = verdict.failure;
        }

        if (lastFailure) lastFailure.diagnostics = { frames: frameAttempts };
        onResult(lastFailure || { ok: false, error: "No frame accepted the command.", diagnostics: { frames: frameAttempts } });
      });
        };

        if (isBackground && isEditOp) {
          chrome.tabs.update(tabId, { active: true }, () => {
            setTimeout(runDispatch, 400);
          });
        } else {
          runDispatch();
        }
      });
    };

    const TAB_RESOLUTION_ERROR =
      "No open browser tab matches this notebook URL. "
      + "Open the exact /edit page for the url you pass (multiple notebook tabs require an exact match).";
    const TAB_ID_INVALID_ERROR =
      "Browser tab is not available. Reload the notebook /edit page and try again.";

    function postHostTabResolutionError(msg, url, errorMessage) {
      try {
        getPort().postMessage({
          type: botResultMessageType(msg?.type, false),
          tabId: null,
          url: url || msg?.url,
          requestId: msg?.requestId,
          tunnel: msg?.type,
          result: { ok: false, error: errorMessage || TAB_RESOLUTION_ERROR },
        });
      } catch (_) {}
    }

    function withResolvedHostTab(msg, onReady) {
      const url = msg?.url || msg?.tabUrl || null;
      const preferredTabId =
        typeof msg?.tabId === "number" && msg.tabId > 0
          ? msg.tabId
          : typeof msg?.tab_id === "number" && msg.tab_id > 0
            ? msg.tab_id
            : null;

      const finish = (tabId, effectiveUrl) => {
        if (typeof tabId !== "number") {
          postHostTabResolutionError(msg, url);
          return;
        }
        onReady(tabId, effectiveUrl || url);
      };

      if (preferredTabId) {
        chrome.tabs.get(preferredTabId, (tab) => {
          if (chrome.runtime.lastError || !tab?.id) {
            postHostTabResolutionError(msg, url, TAB_ID_INVALID_ERROR);
            return;
          }
          const tabUrl = normalizeNotebookUrl(tab.url);
          if (!isNotebookEditUrl(tab.url)) {
            postHostTabResolutionError(
              msg,
              url,
              `Tab ${preferredTabId} is not an open notebook /edit page.`,
            );
            return;
          }
          finish(preferredTabId, tabUrl);
        });
        return;
      }

      if (url) {
        resolveTabIdForUrl(url, null, finish, { preferredTabId: null });
        return;
      }

      finish(null, null);
    }

    if (msg?.type === "SELECT_CELL_BY_INDEX" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "SELECT_CELL_BY_INDEX",
          cellIndex: msg.cellIndex,
          scrollIntoView: msg.scrollIntoView,
          maxWaitMs: msg.maxWaitMs,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          getPort().postMessage({
            type: botResultMessageType(payload.type, Boolean(result?.ok)),
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            cellIndex: msg.cellIndex,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "RUN_CELL_BY_INDEX" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "RUN_CELL_BY_INDEX",
          cellIndex: msg.cellIndex,
          scrollIntoView: msg.scrollIntoView,
          maxWaitMs: msg.maxWaitMs,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          maybeScheduleScrapeAfterRun(payload, result);
          getPort().postMessage({
            type: botResultMessageType(payload.type, Boolean(result?.ok)),
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            cellIndex: msg.cellIndex,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "INSERT_CELL" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "INSERT_CELL",
          direction: msg.direction,
          cellIndex: msg.cellIndex,
          toMarkdown: msg.toMarkdown === true,
          markdownDelayMs: msg.markdownDelayMs,
          requestId: msg.requestId,
          url: effectiveUrl,
          maxWaitMs: msg.maxWaitMs || 1500,
        };
        if (msg.content !== undefined && msg.content !== null) {
          payload.content = msg.content;
        }

        dispatchToFrames(tabId, payload, (result) => {
          getPort().postMessage({
            type: botResultMessageType(payload.type, Boolean(result?.ok)),
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            direction: msg.direction,
            cellIndex: msg.cellIndex,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "CLICK_CELL_BY_INDEX" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "CLICK_CELL_BY_INDEX",
          cellIndex: msg.cellIndex,
          scrollIntoView: msg.scrollIntoView,
          runCell: msg.runCell,
          maxWaitMs: msg.maxWaitMs,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          maybeScheduleScrapeAfterRun(payload, result);
          getPort().postMessage({
            type: botResultMessageType(payload.type, Boolean(result?.ok)),
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            cellIndex: msg.cellIndex,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "CLICK_SELECTOR" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "CLICK_SELECTOR",
          selector: msg.selector,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          getPort().postMessage({
            type: result.ok ? "CLICK_SELECTOR_RESULT" : "CLICK_SELECTOR_ERROR",
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            selector: msg.selector,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "DELETE_CELL" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "DELETE_CELL",
          cellIndex: msg.cellIndex,
          scrollIntoView: msg.scrollIntoView,
          maxWaitMs: msg.maxWaitMs,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          getPort().postMessage({
            type: botResultMessageType(payload.type, Boolean(result?.ok)),
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            cellIndex: msg.cellIndex,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "SEND_KEY" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "SEND_KEY",
          key: msg.key,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          getPort().postMessage({
            type: result.ok ? "SEND_KEY_RESULT" : "SEND_KEY_ERROR",
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            key: msg.key,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "SET_CELL_CONTENT" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "SET_CELL_CONTENT",
          cellIndex: msg.cellIndex,
          content: msg.content,
          maxWaitMs: msg.maxWaitMs,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          getPort().postMessage({
            type: result.ok ? "SET_CELL_CONTENT_RESULT" : "SET_CELL_CONTENT_ERROR",
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            cellIndex: msg.cellIndex,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    if (msg?.type === "SEND_KEYS" && typeof msg?.tabId === "number") {
      withResolvedHostTab(msg, (tabId, effectiveUrl) => {
        const payload = {
          type: "SEND_KEYS",
          keys: msg.keys,
          requestId: msg.requestId,
          url: effectiveUrl,
        };

        dispatchToFrames(tabId, payload, (result) => {
          getPort().postMessage({
            type: result.ok ? "SEND_KEYS_RESULT" : "SEND_KEYS_ERROR",
            tabId,
            url: effectiveUrl,
            requestId: msg.requestId,
            keys: msg.keys,
            tunnel: payload.type,
            diagnostics: result?.diagnostics || null,
            result,
          });
        });
      });
      return;
    }

    // Only deliver tab-scoped data to the originating tab to avoid cross-tab leakage.
    if (typeof msg?.tabId === "number") {
      if (TAB_BROADCAST_TYPES.has(msg?.type)) {
        broadcastToTabFrames(msg.tabId, msg);
      } else {
        chrome.tabs.sendMessage(msg.tabId, msg).catch(() => {});
      }
      return;
    }

    // If the host message was not tab-scoped, prefer resolving the tab from URL
    // for both passive updates and actionable commands so tools can target pages
    // by `url` instead of requiring a `tabId` value.
    const notebookUrl = msg?.url || msg?.tabUrl || null;
    if (!notebookUrl) {
      // No URL to resolve; there is nothing more sensible we can do here.
      console.warn("No URL present on untargeted host message:", msg?.type);
      return;
    }

    // Types that should be dispatched to a tab/frame and may return a result
    const tabTargetTypes = new Set([
      "CLICK_CELL_BY_INDEX",
      "SELECT_CELL_BY_INDEX",
      "RUN_CELL_BY_INDEX",
      "INSERT_CELL",
      "CLICK_SELECTOR",
      "DELETE_CELL",
      "CREATING_MARKDOWN_BY_INDEX",
      "SEND_KEY",
      "SEND_KEYS",
      "SET_CELL_CONTENT",
    ]);

    resolveTabIdForUrl(notebookUrl, null, (resolvedTabId, resolvedUrl) => {
      const effectiveUrl = resolvedUrl || notebookUrl;
      if (typeof resolvedTabId !== 'number') {
        console.warn("Could not resolve tab for host message:", msg?.type, notebookUrl);
        if (tabTargetTypes.has(msg?.type)) {
          try {
            getPort().postMessage({
              type: botResultMessageType(msg?.type, false),
              tabId: null,
              url: effectiveUrl,
              requestId: msg?.requestId,
              tunnel: msg?.type,
              result: {
                ok: false,
                error:
                  "No open browser tab matches this notebook URL. "
                  + "Open the exact /edit page for the url you pass (multiple notebook tabs require an exact match).",
              },
            });
          } catch (e) {
            console.warn("Failed to post tab-resolution error to native host:", e);
          }
        }
        return;
      }

      // If this is an actionable/tab-targeted message, forward it to frames
      // (same strategy as tabId-scoped commands) and post the result back.
      if (tabTargetTypes.has(msg?.type)) {
        const payload = { ...msg, tabId: resolvedTabId, url: effectiveUrl };
        dispatchToFrames(resolvedTabId, payload, (res) => {
          maybeScheduleScrapeAfterRun(payload, res);
          try {
            getPort().postMessage({
              type: botResultMessageType(msg?.type, Boolean(res?.ok)),
              tabId: resolvedTabId,
              url: effectiveUrl,
              requestId: msg?.requestId,
              cellIndex: msg?.cellIndex,
              selector: msg?.selector,
              key: msg?.key,
              keys: msg?.keys,
              direction: msg?.direction,
              tunnel: msg?.type,
              diagnostics: res?.diagnostics || null,
              result: res,
            });
          } catch (e) {
            console.warn("Failed to post action result back to native host:", e);
          }
        });
        return;
      }

      // Otherwise treat as passive data (history/graph/chat) and just forward
      if (TAB_BROADCAST_TYPES.has(msg?.type)) {
        broadcastToTabFrames(resolvedTabId, { ...msg, url: effectiveUrl });
      } else {
        chrome.tabs.sendMessage(resolvedTabId, { ...msg, url: effectiveUrl }).catch(() => {});
      }
    }, { preferredTabId: msg?.tabId });
    return;
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

  chrome.scripting.executeScript({
    target: { tabId: tabId, allFrames: true },
    files: ["markdown-it.min.js", "cell_index_utils.js", "cell_index_badges.js", "cell_debug_chat.js"]
  }).catch(e => console.warn("Cell index badge injection failed:", e));

  // Ensure kernel_state_listener is present for already-open tabs
  chrome.scripting.executeScript({
    target: { tabId: tabId, allFrames: true },
    files: ["cell_index_utils.js", "kernel_state_listener.js"]
  }).catch(e => console.warn("kernel_state_listener injection failed:", e));
}

// ── Bridge UI messages to the Native Host ────────────────────────────────────
const __pendingInsertCode = new Map();

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "GET_TAB_NOTEBOOK_URL") {
    const tabId = sender.tab?.id;
    const tabUrl = sender.tab?.url ? normalizeNotebookUrl(sender.tab.url) : "";
    const cached = typeof tabId === "number" ? notebookIdentityByTab[tabId] : null;

    if (cached && identityMatchesTabUrl(cached, tabUrl)) {
      sendResponse(cached);
      return true;
    }

    if (sender.tab && tabUrl) {
      resolveNotebookIdentity(sender.tab, (identity) => {
        rememberNotebookIdentity(tabId, identity);
        sendResponse(identity);
      });
      return true;
    }

    sendResponse(fallbackNotebookIdentity(tabUrl));
    return true;
  }

  if (msg?.type === "INSERT_CODE_CELL") {
    const requestId = msg.requestId || `insert_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    const tabId = sender.tab?.id;
    __pendingInsertCode.set(requestId, { sendResponse, tabId });
    const p = getPort();
    if (!p) {
      __pendingInsertCode.delete(requestId);
      sendResponse({ ok: false, error: "Native host not connected." });
      return true;
    }
    p.postMessage({
      type: "INSERT_CODE_CELL",
      url: msg.url,
      index: msg.index,
      content: msg.content,
      tabId,
      requestId,
    });
    setTimeout(() => {
      const pending = __pendingInsertCode.get(requestId);
      if (!pending) return;
      __pendingInsertCode.delete(requestId);
      try {
        pending.sendResponse({ ok: false, error: "Timed out waiting for notebook insert (30s)." });
      } catch (_) {}
    }, 45000);
    return true;
  }

  // Handle prompt observer signals locally to trigger an immediate re-scan (throttled)
  if (msg?.type === 'PROMPT_SIGNAL') {
    console.log(`[BG-SIGNAL] Received: "${msg.text}" from cell ${msg.cellIndex || '?'} phase=${msg.phase || '?'}`);
    const p = getPort();
    if (p) {
      p.postMessage({
        ...msg,
        tabId: sender.tab?.id,
        tabUrl: sender.tab?.url,
      });
    }
    const tabId = sender.tab?.id;
    const tabUrl = sender.tab?.url;
    const now = Date.now();
    if (now - __lastPromptSignal > 50) {
      __lastPromptSignal = now;
      if (typeof tabId === "number" && isTargetUrl(tabUrl)) {
        chrome.tabs.get(tabId, (tab) => {
          if (!chrome.runtime.lastError && tab) {
            scrapeTargetTabFast(tab, notebookIdentityByTab[tabId]);
          }
        });
      }
      try { sendTabs(); } catch (e) { /* ignore */ }
    }
    if (typeof tabId === "number" && isTargetUrl(tabUrl)) {
      setTimeout(() => {
        chrome.tabs.get(tabId, (tab) => {
          if (!chrome.runtime.lastError && tab) {
            scrapeTargetTabFast(tab, notebookIdentityByTab[tabId]);
          }
        });
      }, 120);
      setTimeout(() => {
        chrome.tabs.get(tabId, (tab) => {
          if (!chrome.runtime.lastError && tab) {
            scrapeTargetTabFast(tab, notebookIdentityByTab[tabId]);
          }
        });
      }, 350);
    }
    return false;
  }

  const p = getPort();
  if (!p) {
    const hostError = "Native host not connected. Start host.py and reload the extension.";
    if (msg?.type === "CHAT_REQUEST" && typeof sender.tab?.id === "number") {
      const payload = {
        type: "CHAT_STREAM_END",
        error: hostError,
        stopped: false,
        tabId: sender.tab.id,
        url: msg.url,
        notebookKey: msg.notebookKey,
        sessionId: msg.sessionId,
      };
      broadcastToTabFrames(sender.tab.id, payload);
    }
    if (sendResponse) {
      sendResponse({ error: hostError });
    }
    return false;
  }
  if (p) {
    if (msg?.type === "GET_GRAPH" && typeof msg?.tabId !== "number") {
      ackRuntimeMessage(sendResponse);
      resolveTabIdForUrl(msg?.url, sender, (tabId) => {
        if (typeof tabId === "number") {
          p.postMessage({ ...msg, tabId });
        }
      });
      return false;
    }

    // Notebook data/history is keyed by the tab's /edit URL, not the Jupyter iframe URL.
    if (sender.tab?.url && NOTEBOOK_SCOPED_MSG_TYPES.has(msg?.type)) {
      const tabNotebookUrl = normalizeNotebookUrl(sender.tab.url);
      if (tabNotebookUrl) msg.url = tabNotebookUrl;
      if (typeof sender.tab?.id !== "number") {
        const hostError = "Missing tab context. Reload the notebook page and try again.";
        if (msg?.type === "CHAT_REQUEST") {
          broadcastToTabFrames(sender.tab?.id, {
            type: "CHAT_STREAM_END",
            error: hostError,
            stopped: false,
            tabId: sender.tab?.id,
            url: msg.url,
            notebookKey: msg.notebookKey,
            sessionId: msg.sessionId,
          });
        }
        if (sendResponse) {
          sendResponse({ error: hostError });
        }
        return false;
      }
      msg.tabId = sender.tab.id;
      attachNotebookIdentity(msg, sender.tab.id, sender.tab.url);
    } else if (typeof sender.tab?.id === "number") {
      msg.tabId = sender.tab.id;
    } else if (typeof msg?.tab_id === "number" && msg.tab_id > 0) {
      msg.tabId = msg.tab_id;
    }
    if (ASYNC_NATIVE_MSG_TYPES.has(msg?.type)) {
      ackRuntimeMessage(sendResponse);
    }
    p.postMessage(msg);
  }
  return false;
});

// ── Main scan logic (includes UI check) ──────────────────────────────────────
const SCRAPE_MS_ACTIVE = 300;
const SCRAPE_MS_IDLE = 5000;
const SCRAPE_MS_AFTER_SIGNAL = 80;
let __scrapeTimer = null;
let __activeTabId = null;
let __scrapeInFlight = false;

function hasActiveTargetTab(targets) {
  if (typeof __activeTabId !== "number") return false;
  return (targets || []).some((t) => t.id === __activeTabId);
}

function scheduleNotebookScrape(delayMs = 2000) {
  const wait = Math.max(SCRAPE_MS_AFTER_SIGNAL, Number(delayMs) || SCRAPE_MS_AFTER_SIGNAL);
  setTimeout(() => {
    try {
      sendTabs();
    } catch (_) {}
  }, wait);
}

function scheduleNextScrapeCycle(activeTargetVisible) {
  if (__scrapeTimer) {
    clearTimeout(__scrapeTimer);
    __scrapeTimer = null;
  }
  const delay = activeTargetVisible ? SCRAPE_MS_ACTIVE : SCRAPE_MS_IDLE;
  __scrapeTimer = setTimeout(() => {
    try {
      sendTabs();
    } catch (_) {}
  }, delay);
}

function maybeScheduleScrapeAfterRun(payload, result) {
  if (
    result?.ok
    && (payload?.type === "RUN_CELL_BY_INDEX" || payload?.runCell === true)
  ) {
    scheduleNotebookScrape(SCRAPE_MS_AFTER_SIGNAL);
    setTimeout(() => { try { sendTabs(); } catch (_) {} }, 250);
    setTimeout(() => { try { sendTabs(); } catch (_) {} }, 600);
  }
}

function scrapeTargetTabFast(tab, cachedIdentity) {
  if (!tab || typeof tab.id !== "number") return;
  const identity = cachedIdentity || notebookIdentityByTab[tab.id] || fallbackNotebookIdentity(tab.url);

  chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: true },
    func: scrapeNotebook,
  }, (scrapeResults) => {
    if (chrome.runtime.lastError) {
      console.log(`[fastScrape] Tab ${tab.id} error:`, chrome.runtime.lastError);
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
            String(cell?.execution_status ?? ""),
          ].join("||");
          if (seenCells.has(key)) continue;
          seenCells.add(key);
          allCells.push(cell);
        }
        if (r.result.title) notebookTitle = r.result.title;
      }
    }
    const kernelEntry = kernelStateByTab[tab.id] || {};
    const scenario = kernelEntry.scenario || KERNEL_SCENARIO_ON;
    getPort().postMessage({
      type: "NOTEBOOK_DATA",
      tabUrl: tab.url,
      tabId: tab.id,
      notebookId: identity.notebookId,
      notebookKey: identity.notebookKey,
      title: notebookTitle,
      cellCount: allCells.length,
      cells: allCells,
      kernelStatus: kernelEntry.lastStatus || "running",
      kernelScenario: scenario,
      kernelState: { fastScrape: true },
    });
  });
}

function sendTabs() {
  if (__scrapeInFlight) {
    scheduleNextScrapeCycle(false);
    return;
  }
  __scrapeInFlight = true;

  chrome.tabs.query({}, (tabs) => {
    const targets = tabs.filter(t => {
      return isTargetUrl(t.url);
    });

    console.log(`[sendTabs] Found ${targets.length} target tabs to process`);
    const activeTargetVisible = hasActiveTargetTab(targets);

    if (targets.length === 0) {
      __scrapeInFlight = false;
      scheduleNextScrapeCycle(false);
      return;
    }

    let pending = targets.length;

    const finishOne = () => {
      pending -= 1;
      if (pending <= 0) {
        __scrapeInFlight = false;
        scheduleNextScrapeCycle(activeTargetVisible);
      }
    };

    for (const tab of targets) {
      console.log(`[sendTabs] Processing tab ${tab.id}: ${tab.url}`);

      const tabUrl = normalizeNotebookUrl(tab.url);
      for (const variant of notebookUrlVariants(tab.url)) {
        tabIdByNotebookUrl[variant] = tab.id;
      }
      const cachedIdentity = notebookIdentityByTab[tab.id];
      if (cachedIdentity && tabUrl && !identityMatchesTabUrl(cachedIdentity, tabUrl)) {
        delete notebookIdentityByTab[tab.id];
      }

      injectUI(tab.id);

      resolveNotebookIdentity(tab, (identity) => {
        rememberNotebookIdentity(tab.id, identity);

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
            finishOne();
            return;
          }
          const iframes = results?.[0]?.result || [];
          chrome.scripting.executeScript({
              target: { tabId: tab.id, allFrames: true },
              func: scrapeNotebook
          }, (scrapeResults) => {
              if (chrome.runtime.lastError) {
                console.log(`[sendTabs] Tab ${tab.id} - scrape error:`, chrome.runtime.lastError);
                finishOne();
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
                      String(cell?.execution_status ?? ""),
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
                  notebookId: identity.notebookId,
                  notebookKey: identity.notebookKey,
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
                  finishOne();
                })();
              });
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

chrome.tabs.onActivated.addListener(({ tabId }) => {
  __activeTabId = tabId;
});

chrome.windows.onFocusChanged.addListener(() => {
  chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
    if (tabs && tabs[0]) __activeTabId = tabs[0].id;
  });
});

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
  uncacheTabId(id);
  delete kernelStateByTab[id];
  delete notebookIdentityByTab[id];
  delete lastNotebookUrlByTab[id];
  console.log(`[Tab ${id}] Removed from state tracking`);
});

console.log("[Background] Service worker loaded, calling initial sendTabs()");
sendTabs();
