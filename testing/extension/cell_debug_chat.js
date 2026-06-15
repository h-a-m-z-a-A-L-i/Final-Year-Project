(function initCellDebugChat() {
  const VERSION = "2026-06-13-v13";
  if (window.__ncCellDebugChatVersion === VERSION) return;
  window.__ncCellDebugChatVersion = VERSION;

  const BADGE_CLASS = "nc-cell-debug-badge";
  const COLUMN_CLASS = "nc-prompt-index-col";
  const PANEL_CLASS = "nc-cell-debug-panel";
  const PANEL_ROOT_ID = "nc-cell-debug-panel-root";

  let activePanel = null;
  let activeCellIndex = null;
  let activeSessionId = null;
  let activeStreamChannel = null;
  let notebookUrl = "";
  let notebookKey = "";
  let notebookId = null;
  let streamBuffer = "";
  let isStreaming = false;
  let repositionHandler = null;

  function hasChromeRuntime() {
    try {
      return typeof chrome !== "undefined" && !!chrome.runtime && !!chrome.runtime.sendMessage;
    } catch {
      return false;
    }
  }

  function normalizeNotebookUrl(raw) {
    try {
      const u = new URL(String(raw || window.location.href));
      const path = (u.pathname || "/").replace(/\/+$/, "") || "/";
      return `${u.protocol}//${u.host}${path}`.toLowerCase();
    } catch {
      return String(raw || "").split("#", 1)[0].split("?", 1)[0].replace(/\/+$/, "").toLowerCase();
    }
  }

  function streamChannelFor(cellIndex) {
    return `cell-${cellIndex}`;
  }

  /** Stable per-cell session id (notebook key is the other key in SQLite). */
  function getCellDebugSessionId(cellIndex) {
    const n = Number(cellIndex);
    if (!Number.isInteger(n) || n < 1) return "cell-debug-cell-0";
    return `cell-debug-cell-${n}`;
  }

  function currentNotebookKey() {
    return notebookKey || notebookUrl || normalizeNotebookUrl(window.location.href);
  }

  function notebookPayload() {
    return {
      url: notebookUrl || normalizeNotebookUrl(window.location.href),
      notebookId,
      notebookKey: currentNotebookKey(),
    };
  }

  function resolveNotebookUrl(callback) {
    const liveUrl = normalizeNotebookUrl(window.location.href);
    if (notebookUrl && notebookKey && notebookUrl === liveUrl) {
      callback(notebookUrl);
      return;
    }
    if (!hasChromeRuntime()) {
      notebookUrl = liveUrl;
      notebookKey = liveUrl;
      callback(notebookUrl);
      return;
    }
    chrome.runtime.sendMessage({ type: "GET_TAB_NOTEBOOK_URL" }, (response) => {
      notebookUrl = normalizeNotebookUrl(response?.url || liveUrl);
      notebookKey = String(response?.notebookKey || notebookUrl).trim() || notebookUrl;
      notebookId = response?.notebookId ?? null;
      callback(notebookUrl);
    });
  }

  function ensureStyles(doc) {
    const rootDoc = doc || document;
    if (!rootDoc || rootDoc.getElementById("nc-cell-debug-style")) return;
    const style = rootDoc.createElement("style");
    style.id = "nc-cell-debug-style";
    style.textContent = `
      .${BADGE_CLASS} {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-top: 3px;
        width: 20px;
        height: 20px;
        padding: 0;
        border: 1px solid #6b21a8;
        border-radius: 3px;
        background: #7c3aed;
        color: #fff;
        cursor: pointer;
        user-select: none;
        box-shadow: 0 1px 3px rgba(0,0,0,0.22);
      }
      .${BADGE_CLASS}:hover { background: #6d28d9; }
      .${BADGE_CLASS}.nc-active {
        background: #5b21b6;
        box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.35);
      }
      .${BADGE_CLASS} svg {
        width: 12px;
        height: 12px;
        pointer-events: none;
      }
      .${PANEL_CLASS} {
        position: fixed;
        z-index: 2147483646;
        width: min(360px, calc(100vw - 24px));
        max-height: min(420px, calc(100vh - 24px));
        display: flex;
        flex-direction: column;
        border: 1px solid #3f3f46;
        border-radius: 10px;
        background: #18181b;
        color: #f4f4f5;
        box-shadow: 0 12px 40px rgba(0,0,0,0.45);
        font: 12px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        overflow: hidden;
      }
      .${PANEL_CLASS} .nc-cd-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 8px 10px;
        border-bottom: 1px solid #3f3f46;
        background: #27272a;
      }
      .${PANEL_CLASS} .nc-cd-title {
        font-weight: 600;
        font-size: 12px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .${PANEL_CLASS} .nc-cd-actions { display: flex; gap: 6px; align-items: center; }
      .${PANEL_CLASS} .nc-cd-mode {
        font-size: 11px;
        padding: 2px 6px;
        border-radius: 6px;
        border: 1px solid #52525b;
        background: #3f3f46;
        color: inherit;
      }
      .${PANEL_CLASS} .nc-cd-clear {
        font-size: 10px;
        padding: 2px 8px;
        border-radius: 6px;
        border: 1px solid #52525b;
        background: transparent;
        color: #a1a1aa;
        cursor: pointer;
      }
      .${PANEL_CLASS} .nc-cd-clear:hover {
        color: #fecaca;
        border-color: #7f1d1d;
        background: rgba(127, 29, 29, 0.22);
      }
      .${PANEL_CLASS} .nc-cd-close {
        border: none;
        background: transparent;
        color: #a1a1aa;
        cursor: pointer;
        font-size: 16px;
        line-height: 1;
        padding: 2px 4px;
      }
      .${PANEL_CLASS} .nc-cd-close:hover { color: #fff; }
      .${PANEL_CLASS} .nc-cd-messages {
        flex: 1;
        overflow-y: auto;
        padding: 8px 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
        min-height: 120px;
        max-height: 280px;
      }
      .${PANEL_CLASS} .nc-cd-msg {
        padding: 6px 8px;
        border-radius: 8px;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .${PANEL_CLASS} .nc-cd-msg.user {
        align-self: flex-end;
        background: #2563eb;
        color: #fff;
        max-width: 92%;
      }
      .${PANEL_CLASS} .nc-cd-msg.assistant {
        align-self: stretch;
        width: 100%;
        max-width: 100%;
        background: transparent;
        padding: 0;
        white-space: normal;
      }
      .${PANEL_CLASS} .nc-cd-msg.assistant.nc-cd-streaming .nc-cd-code-stack {
        display: none;
      }
      .${PANEL_CLASS} .nc-cd-prose-bubble {
        display: inline-block;
        align-self: flex-start;
        background: #3f3f46;
        padding: 6px 8px;
        border-radius: 8px;
        max-width: 96%;
        margin-bottom: 6px;
        white-space: normal;
      }
      .${PANEL_CLASS} .nc-cd-msg.system {
        align-self: center;
        background: transparent;
        color: #a1a1aa;
        font-size: 11px;
        padding: 0;
      }
      .${PANEL_CLASS} .nc-cd-footer {
        border-top: 1px solid #3f3f46;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .${PANEL_CLASS} .nc-cd-input {
        width: 100%;
        min-height: 52px;
        max-height: 120px;
        resize: vertical;
        border: 1px solid #52525b;
        border-radius: 8px;
        background: #09090b;
        color: #f4f4f5;
        padding: 8px;
        font: inherit;
        box-sizing: border-box;
      }
      .${PANEL_CLASS} .nc-cd-input:focus {
        outline: none;
        border-color: #7c3aed;
      }
      .${PANEL_CLASS} .nc-cd-send-row {
        display: flex;
        justify-content: flex-end;
        gap: 6px;
      }
      .${PANEL_CLASS} .nc-cd-btn {
        border: none;
        border-radius: 6px;
        padding: 5px 10px;
        font-size: 11px;
        cursor: pointer;
      }
      .${PANEL_CLASS} .nc-cd-btn.send {
        background: #7c3aed;
        color: #fff;
      }
      .${PANEL_CLASS} .nc-cd-btn.send:disabled { opacity: 0.5; cursor: not-allowed; }
      .${PANEL_CLASS} .nc-cd-btn.stop {
        background: #52525b;
        color: #fff;
      }
      .${PANEL_CLASS} .nc-cd-prose {
        margin-bottom: 0;
        line-height: 1.55;
      }
      .${PANEL_CLASS} .nc-cd-prose pre { display: none; }
      .${PANEL_CLASS} .nc-cd-prose p { margin: 6px 0; }
      .${PANEL_CLASS} .nc-cd-prose h1,
      .${PANEL_CLASS} .nc-cd-prose h2,
      .${PANEL_CLASS} .nc-cd-prose h3,
      .${PANEL_CLASS} .nc-cd-prose h4 {
        color: #c4b5fd;
        margin: 10px 0 4px 0;
        font-weight: 700;
        line-height: 1.35;
      }
      .${PANEL_CLASS} .nc-cd-prose h1 { font-size: 1.15em; }
      .${PANEL_CLASS} .nc-cd-prose h2 { font-size: 1.08em; }
      .${PANEL_CLASS} .nc-cd-prose h3 { font-size: 1.02em; }
      .${PANEL_CLASS} .nc-cd-prose h4 { font-size: 1em; }
      .${PANEL_CLASS} .nc-cd-prose ul,
      .${PANEL_CLASS} .nc-cd-prose ol {
        margin: 6px 0;
        padding-left: 18px;
      }
      .${PANEL_CLASS} .nc-cd-prose ul li { list-style: disc; }
      .${PANEL_CLASS} .nc-cd-prose ol li { list-style: decimal; }
      .${PANEL_CLASS} .nc-cd-prose strong { font-weight: 700; color: #f4f4f5; }
      .${PANEL_CLASS} .nc-cd-prose p > strong:only-child {
        display: block;
        margin-top: 8px;
        font-size: 1.02em;
        color: #c4b5fd;
      }
      .${PANEL_CLASS} .nc-cd-prose em { font-style: italic; }
      .${PANEL_CLASS} .nc-cd-prose p code,
      .${PANEL_CLASS} .nc-cd-prose li code {
        background: rgba(124, 58, 237, 0.2);
        color: #ddd6fe;
        padding: 1px 5px;
        border-radius: 4px;
        font-size: 0.9em;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }
      .${PANEL_CLASS} .nc-cd-code-stack {
        display: flex;
        flex-direction: column;
        gap: 8px;
        width: 100%;
      }
      .${PANEL_CLASS} .nc-cd-code-card {
        border: 1px solid #3f3f46;
        border-radius: 8px;
        overflow: hidden;
        background: #0c0c0e;
      }
      .${PANEL_CLASS} .nc-cd-code-header {
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 6px 8px;
        background: #27272a;
        border-bottom: 1px solid #3f3f46;
      }
      .${PANEL_CLASS} .nc-cd-code-header-top {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 8px;
      }
      .${PANEL_CLASS} .nc-cd-code-lang {
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        color: #a78bfa;
        letter-spacing: 0.04em;
      }
      .${PANEL_CLASS} .nc-cd-copy-btn {
        border: 1px solid rgba(255,255,255,0.2);
        background: rgba(255,255,255,0.08);
        color: #fff;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 10px;
        cursor: pointer;
      }
      .${PANEL_CLASS} .nc-cd-copy-btn:hover { background: rgba(255,255,255,0.16); }
      .${PANEL_CLASS} .nc-cd-copy-btn.copied {
        background: #16a34a;
        border-color: #16a34a;
      }
      .${PANEL_CLASS} .nc-cd-code-pre {
        margin: 0;
        padding: 8px 10px;
        overflow-x: auto;
        font: 11px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        color: #d4d4d8;
        white-space: pre;
        tab-size: 4;
      }
      .${PANEL_CLASS} .nc-cd-stream-plain {
        white-space: pre-wrap;
        word-break: break-word;
      }
    `;
    (rootDoc.head || rootDoc.documentElement).appendChild(style);
  }

  const DEBUG_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
    + '<path d="M12 3v3M8 6l-2-2M16 6l2-2M5 11l-2 1M19 11l2 1M7 18l-1 3M17 18l1 3"/>'
    + '<rect x="7" y="9" width="10" height="8" rx="2"/>'
    + "</svg>";

  function upsertDebugBadge(column) {
    if (!column || column.querySelector(`.${BADGE_CLASS}`)) return;
    const indexBadge = column.querySelector(".nc-cell-index-badge");
    if (!indexBadge) return;

    const cellIndex = parseInt(indexBadge.getAttribute("data-cell-index") || indexBadge.textContent, 10);
    if (!Number.isFinite(cellIndex) || cellIndex < 1) return;

    ensureStyles(column.ownerDocument || document);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = BADGE_CLASS;
    btn.innerHTML = DEBUG_ICON;
    btn.title = `Debug / generate for cell ${cellIndex}`;
    btn.setAttribute("aria-label", `Open cell ${cellIndex} debug chat`);
    btn.setAttribute("data-cell-index", String(cellIndex));

    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      togglePanel(cellIndex, btn, column);
    });

    column.appendChild(btn);
  }

  function scanForBadges(root) {
    const scope = root || document;
    if (!scope.querySelectorAll) return;
    scope.querySelectorAll(`.${COLUMN_CLASS}`).forEach((col) => {
      try {
        upsertDebugBadge(col);
      } catch (e) {
        console.warn("[nc-cell-debug] badge upsert failed:", e?.message || e);
      }
    });
  }

  function getPanelRoot() {
    let root = document.getElementById(PANEL_ROOT_ID);
    if (!root) {
      root = document.createElement("div");
      root.id = PANEL_ROOT_ID;
      document.body.appendChild(root);
    }
    return root;
  }

  function positionPanel(panel, anchor) {
    if (!panel || !anchor) return;
    const rect = anchor.getBoundingClientRect();
    const margin = 8;
    let top = rect.bottom + margin;
    let left = rect.left;
    const pw = panel.offsetWidth || 320;
    const ph = panel.offsetHeight || 280;
    if (left + pw > window.innerWidth - margin) {
      left = Math.max(margin, window.innerWidth - pw - margin);
    }
    if (top + ph > window.innerHeight - margin) {
      top = Math.max(margin, rect.top - ph - margin);
    }
    panel.style.top = `${Math.round(top)}px`;
    panel.style.left = `${Math.round(left)}px`;
  }

  function closePanel() {
    if (repositionHandler) {
      window.removeEventListener("scroll", repositionHandler, true);
      window.removeEventListener("resize", repositionHandler);
      repositionHandler = null;
    }
    if (activePanel) {
      activePanel.remove();
      activePanel = null;
    }
    document.querySelectorAll(`.${BADGE_CLASS}.nc-active`).forEach((el) => el.classList.remove("nc-active"));
    if (isStreaming && activeSessionId && hasChromeRuntime()) {
      chrome.runtime.sendMessage({
        type: "STOP_CHAT",
        ...notebookPayload(),
        sessionId: activeSessionId,
        streamChannel: activeStreamChannel || undefined,
      });
    }
    activeCellIndex = null;
    activeSessionId = null;
    activeStreamChannel = null;
    streamBuffer = "";
    isStreaming = false;
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function getMarkdownRenderer() {
    try {
      if (typeof window.markdownit === "function") {
        return window.markdownit({ html: true, linkify: true, typographer: true });
      }
    } catch {}
    return null;
  }

  let markdownRenderer = null;
  let markdownLoadRequested = false;

  function ensureMarkdownIt(callback) {
    if (typeof window.markdownit === "function") {
      callback();
      return;
    }
    if (!hasChromeRuntime() || markdownLoadRequested) {
      callback();
      return;
    }
    markdownLoadRequested = true;
    try {
      const src = chrome.runtime.getURL("markdown-it.min.js");
      const script = document.createElement("script");
      script.src = src;
      script.onload = () => callback();
      script.onerror = () => callback();
      (document.head || document.documentElement).appendChild(script);
    } catch {
      callback();
    }
  }

  /** Fix common LLM markdown glitches before render. */
  function normalizeAssistantMarkdown(text) {
    let t = String(text || "").replace(/\r\n/g, "\n");
    const dash = "[—\\-]";
    t = t.replace(
      new RegExp(`^\\*Cell\\s*\\[(\\d+)\\]\\s*${dash}\\*\\*\\s*\\*(.+?)\\*`, "gim"),
      "**Cell [$1] —** $2"
    );
    t = t.replace(
      new RegExp(`^\\*Cell\\s*\\[(\\d+)\\]\\s*${dash}\\*\\*\\s*(.+)$`, "gim"),
      "**Cell [$1] —** $2"
    );
    t = t.replace(
      new RegExp(`^\\*\\*Cell\\s*\\[(\\d+)\\]\\s*${dash}\\*\\*\\s*(.+)$`, "gim"),
      "### Cell [$1] — $2"
    );
    // Standalone **Section** lines → subsection headings (e.g. **What it does**)
    t = t.replace(/^\*\*([^*\n]{2,96})\*\*\s*$/gm, "#### $1");
    return t;
  }

  function renderProseHtml(text) {
    const t = normalizeAssistantMarkdown(text).trim();
    if (!t) return "";
    const md = markdownRenderer || getMarkdownRenderer();
    if (md) {
      markdownRenderer = md;
      return md.render(t);
    }
    return escapeHtml(t).replace(/\n/g, "<br>");
  }

  function refreshAssistantMarkdown(panel) {
    if (!panel) return;
    panel.querySelectorAll(".nc-cd-msg.assistant[data-nc-raw]").forEach((el) => {
      const raw = el.getAttribute("data-nc-raw") || "";
      const cellIdx = Number.parseInt(el.getAttribute("data-nc-cell") || "", 10);
      mountAssistantContent(el, raw, Number.isFinite(cellIdx) ? cellIdx : activeCellIndex);
    });
  }

  function parseFencedCodeBlocks(raw) {
    const segments = [];
    const s = String(raw || "");
    const re = /```([\w.-]*)\r?\n?([\s\S]*?)```/g;
    let last = 0;
    let m;
    while ((m = re.exec(s))) {
      if (m.index > last) {
        const chunk = s.slice(last, m.index);
        if (chunk.trim()) segments.push({ type: "text", content: chunk });
      }
      segments.push({
        type: "code",
        lang: (m[1] || "code").trim() || "code",
        content: m[2].replace(/\s+$/, ""),
      });
      last = m.index + m[0].length;
    }
    if (last < s.length) {
      const tail = s.slice(last);
      if (tail.trim()) segments.push({ type: "text", content: tail });
    }
    if (!segments.length && s.trim()) segments.push({ type: "text", content: s });
    return segments;
  }

  function looksLikePythonCell(text) {
    const t = String(text || "").trim();
    if (!t || t.length < 12) return false;
    const lines = t.split(/\r?\n/).filter((ln) => ln.trim());
    if (lines.length < 2) return false;
    return /^(import |from |def |class |#|@|for |while |if |try:|with )/m.test(t);
  }

  /** Fenced blocks first; fall back to **Code** sections or obvious python bodies. */
  function segmentAssistantContent(raw) {
    const fenced = parseFencedCodeBlocks(raw);
    if (fenced.some((seg) => seg.type === "code")) return fenced;

    const s = String(raw || "");
    const sectionRe =
      /(?:^|\n)(?:#{1,3}\s*|\*\*)Code(?:\*\*)?\s*:?\s*\n+([\s\S]+?)(?=\n(?:#{1,3}\s|\*\*)(?:Plan|Placement|Notes|Run)\b|$)/i;
    const match = sectionRe.exec(s);
    if (match && match[1].trim()) {
      const code = match[1].trim().replace(/^```[\w.-]*\r?\n?/, "").replace(/\n?```$/, "");
      const before = s.slice(0, match.index).trim();
      const segments = [];
      if (before) segments.push({ type: "text", content: before });
      segments.push({ type: "code", lang: "python", content: code });
      return segments;
    }

    const hasProseSections = /\*\*(?:Plan|Placement|Notes|Run)\*\*/i.test(s) || /^#{1,3}\s/m.test(s);
    if (!hasProseSections && looksLikePythonCell(s)) {
      return [{ type: "code", lang: "python", content: s.trim() }];
    }

    return fenced;
  }

  function createCodeSnippetCard(lang, code) {
    const card = document.createElement("div");
    card.className = "nc-cd-code-card";

    const header = document.createElement("div");
    header.className = "nc-cd-code-header";

    const headerTop = document.createElement("div");
    headerTop.className = "nc-cd-code-header-top";

    const label = document.createElement("span");
    label.className = "nc-cd-code-lang";
    label.textContent = lang || "code";

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "nc-cd-copy-btn";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await navigator.clipboard.writeText(code);
        copyBtn.textContent = "Copied";
        copyBtn.classList.add("copied");
        setTimeout(() => {
          copyBtn.textContent = "Copy";
          copyBtn.classList.remove("copied");
        }, 2000);
      } catch {
        copyBtn.textContent = "Failed";
      }
    });

    headerTop.appendChild(label);
    headerTop.appendChild(copyBtn);
    header.appendChild(headerTop);

    const pre = document.createElement("pre");
    pre.className = "nc-cd-code-pre";
    pre.textContent = code;

    card.appendChild(header);
    card.appendChild(pre);
    return card;
  }

  function mountAssistantContent(container, raw, defaultCellIndex) {
    const segments = segmentAssistantContent(raw);
    container.innerHTML = "";
    container.className = "nc-cd-msg assistant";
    container.setAttribute("data-nc-raw", String(raw || ""));
    if (defaultCellIndex != null) {
      container.setAttribute("data-nc-cell", String(defaultCellIndex));
    }

    const textParts = [];
    const codeParts = [];
    for (const seg of segments) {
      if (seg.type === "code") codeParts.push(seg);
      else textParts.push(seg.content);
    }

    const proseJoined = textParts.join("\n\n").trim();
    if (proseJoined) {
      const prose = document.createElement("div");
      prose.className = "nc-cd-prose-bubble";
      const inner = document.createElement("div");
      inner.className = "nc-cd-prose";
      inner.innerHTML = renderProseHtml(proseJoined);
      prose.appendChild(inner);
      container.appendChild(prose);
    }

    if (codeParts.length) {
      const stack = document.createElement("div");
      stack.className = "nc-cd-code-stack";
      for (const seg of codeParts) {
        stack.appendChild(createCodeSnippetCard(seg.lang, seg.content));
      }
      container.appendChild(stack);
    }

    if (!proseJoined && !codeParts.length) {
      container.textContent = String(raw || "").trim() || "No response.";
    }
  }

  const PANEL_SYSTEM_HINT =
    "Focused on this cell only. Ask to debug; Code to generate or fix. History is saved per cell until you click Clear.";

  function resetPanelMessages(messagesEl) {
    if (!messagesEl) return;
    messagesEl.innerHTML = "";
    appendPanelMessage(messagesEl, "system", PANEL_SYSTEM_HINT);
  }

  function appendPanelMessage(container, role, text, cellIndex) {
    const el = document.createElement("div");
    el.className = `nc-cd-msg ${role}`;
    if (role === "assistant") {
      mountAssistantContent(el, text, cellIndex);
    } else {
      el.textContent = String(text || "");
    }
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
    return el;
  }

  function ensureStreamMessage(messages, cellIndex) {
    const el = document.createElement("div");
    el.className = "nc-cd-msg assistant nc-cd-streaming";
    const plain = document.createElement("span");
    plain.className = "nc-cd-stream-plain";
    plain.textContent = "…";
    el.appendChild(plain);
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
    el.__ncStreamPlain = plain;
    return el;
  }

  function buildPanel(cellIndex, anchor, url) {
    const root = getPanelRoot();
    closePanel();

    activeCellIndex = cellIndex;
    activeSessionId = getCellDebugSessionId(cellIndex);
    activeStreamChannel = streamChannelFor(cellIndex);
    notebookUrl = url || notebookUrl;
    streamBuffer = "";
    isStreaming = false;

    const panel = document.createElement("div");
    panel.className = PANEL_CLASS;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", `Cell ${cellIndex} debug chat`);

    const header = document.createElement("div");
    header.className = "nc-cd-header";
    header.innerHTML = `<span class="nc-cd-title">Cell ${cellIndex} — debug</span>`;

    const actions = document.createElement("div");
    actions.className = "nc-cd-actions";

    const modeSelect = document.createElement("select");
    modeSelect.className = "nc-cd-mode";
    modeSelect.innerHTML = '<option value="ask">Ask</option><option value="code">Code</option>';
    modeSelect.title = "Ask = explain/debug; Code = generate cell code";

    const messages = document.createElement("div");
    messages.className = "nc-cd-messages";
    appendPanelMessage(messages, "system", PANEL_SYSTEM_HINT);

    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "nc-cd-clear";
    clearBtn.textContent = "Clear";
    clearBtn.title = "Delete this cell's saved chat history";
    clearBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!confirm(`Clear all saved chat history for cell ${cellIndex}?`)) return;
      if (isStreaming && hasChromeRuntime()) {
        chrome.runtime.sendMessage({
          type: "STOP_CHAT",
          ...notebookPayload(),
          sessionId: activeSessionId,
          streamChannel: activeStreamChannel || undefined,
        });
      }
      streamBuffer = "";
      streamEl = null;
      isStreaming = false;
      resetPanelMessages(messages);
      if (!hasChromeRuntime() || !notebookUrl || !activeSessionId) return;
      chrome.runtime.sendMessage({
        type: "CLEAR_HISTORY",
        ...notebookPayload(),
        sessionId: activeSessionId,
      });
    });

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "nc-cd-close";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      closePanel();
    });

    actions.appendChild(modeSelect);
    actions.appendChild(clearBtn);
    actions.appendChild(closeBtn);
    header.appendChild(actions);

    const footer = document.createElement("div");
    footer.className = "nc-cd-footer";

    const input = document.createElement("textarea");
    input.className = "nc-cd-input";
    input.placeholder = "Explain error, improve code, or describe what to generate…";
    input.rows = 2;

    const sendRow = document.createElement("div");
    sendRow.className = "nc-cd-send-row";

    const stopBtn = document.createElement("button");
    stopBtn.type = "button";
    stopBtn.className = "nc-cd-btn stop";
    stopBtn.textContent = "Stop";
    stopBtn.style.display = "none";

    const sendBtn = document.createElement("button");
    sendBtn.type = "button";
    sendBtn.className = "nc-cd-btn send";
    sendBtn.textContent = "Send";

    sendRow.appendChild(stopBtn);
    sendRow.appendChild(sendBtn);
    footer.appendChild(input);
    footer.appendChild(sendRow);

    panel.appendChild(header);
    panel.appendChild(messages);
    panel.appendChild(footer);
    root.appendChild(panel);
    activePanel = panel;

    anchor.classList.add("nc-active");
    positionPanel(panel, anchor);

    repositionHandler = () => positionPanel(panel, anchor);
    window.addEventListener("scroll", repositionHandler, true);
    window.addEventListener("resize", repositionHandler);

    panel.addEventListener("click", (e) => e.stopPropagation());
    panel.addEventListener("mousedown", (e) => e.stopPropagation());

    let streamEl = null;

    function setStreaming(active) {
      isStreaming = !!active;
      sendBtn.disabled = active;
      sendBtn.style.display = active ? "none" : "";
      stopBtn.style.display = active ? "" : "none";
      input.disabled = active;
    }

    function finalizeStream(opts) {
      const text = (opts && opts.text) || streamBuffer;
      if (streamEl) {
        if (text) {
          mountAssistantContent(streamEl, text, cellIndex);
        } else if (opts && opts.stopped) {
          streamEl.textContent = "Stopped.";
        } else {
          streamEl.textContent = "No response.";
        }
        streamEl.classList.remove("nc-cd-streaming");
      } else if (text) {
        appendPanelMessage(messages, "assistant", text, cellIndex);
      }
      streamEl = null;
      streamBuffer = "";
      setStreaming(false);
      if (opts && opts.error) {
        appendPanelMessage(messages, "system", `Error: ${opts.error}`);
      }
      messages.scrollTop = messages.scrollHeight;
    }

    function sendPrompt() {
      if (!hasChromeRuntime()) {
        appendPanelMessage(messages, "system", "Extension runtime unavailable.");
        return;
      }
      const text = input.value.trim();
      if (!text || isStreaming) return;

      const mode = String(modeSelect.value || "ask");
      const scopedPrompt = `cell ${cellIndex}: ${text}`;

      appendPanelMessage(messages, "user", text);
      input.value = "";
      streamBuffer = "";
      streamEl = ensureStreamMessage(messages, cellIndex);
      setStreaming(true);

      chrome.runtime.sendMessage(
        {
          type: "CHAT_REQUEST",
          ...notebookPayload(),
          sessionId: activeSessionId,
          cellIndex: cellIndex,
          streamChannel: activeStreamChannel,
          prompt: scopedPrompt,
          mode: mode,
          source: "cell_debug",
        },
        (response) => {
          const lastError = chrome.runtime.lastError?.message || "";
          if (
            lastError &&
            !/message port closed before a response was received/i.test(lastError)
          ) {
            finalizeStream({ error: lastError });
            return;
          }
          if (response && response.error) {
            finalizeStream({ error: response.error });
          }
        }
      );
    }

    sendBtn.addEventListener("click", (e) => {
      e.preventDefault();
      sendPrompt();
    });

    stopBtn.addEventListener("click", (e) => {
      e.preventDefault();
      if (!hasChromeRuntime() || !activeSessionId) return;
      chrome.runtime.sendMessage({
        type: "STOP_CHAT",
        ...notebookPayload(),
        sessionId: activeSessionId,
        streamChannel: activeStreamChannel || undefined,
      });
      finalizeStream({ text: streamBuffer, stopped: true });
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendPrompt();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closePanel();
      }
    });

    panel.__ncFinalizeStream = finalizeStream;
    panel.__ncAfterClear = () => {
      streamEl = null;
      setStreaming(false);
    };
    panel.__ncStreamEl = () => streamEl;
    panel.__ncSetStreamEl = (el) => {
      streamEl = el;
    };
    panel.__ncAppendDelta = (delta) => {
      streamBuffer += String(delta || "");
      if (!streamEl) {
        streamEl = ensureStreamMessage(messages, cellIndex);
      }
      const plain = streamEl.__ncStreamPlain || streamEl.querySelector(".nc-cd-stream-plain");
      if (plain) {
        plain.textContent = streamBuffer;
      } else {
        streamEl.textContent = streamBuffer;
      }
      messages.scrollTop = messages.scrollHeight;
    };

    if (hasChromeRuntime() && activeSessionId && notebookUrl) {
      chrome.runtime.sendMessage({
        type: "GET_HISTORY",
        ...notebookPayload(),
        sessionId: activeSessionId,
      });
    }

    ensureMarkdownIt(() => {
      markdownRenderer = getMarkdownRenderer();
      refreshAssistantMarkdown(panel);
    });

    setTimeout(() => input.focus(), 50);
  }

  function togglePanel(cellIndex, anchor, column) {
    const anchorEl = anchor || column;
    if (activePanel && activeCellIndex === cellIndex) {
      closePanel();
      return;
    }
    resolveNotebookUrl((url) => buildPanel(cellIndex, anchorEl, url));
  }

  function onRuntimeMessage(msg) {
    if (msg?.type === "NOTEBOOK_IDENTITY_UPDATED") {
      notebookUrl = normalizeNotebookUrl(msg?.url || window.location.href);
      notebookKey = String(msg?.notebookKey || notebookUrl).trim() || notebookUrl;
      notebookId = msg?.notebookId ?? null;
      return;
    }

    if (!activePanel || !activeSessionId) return;

    const msgKey = String(msg?.notebookKey || normalizeNotebookUrl(msg?.url || "")).trim();
    const expectedKey = currentNotebookKey();
    if (msgKey && expectedKey && msgKey !== expectedKey) return;

    if (msg.type === "HISTORY_DATA") {
      const histSid = String(msg.activeSessionId || msg.sessionId || "");
      if (histSid && histSid !== activeSessionId) return;
      const history = Array.isArray(msg.history) ? msg.history : [];
      const messagesEl = activePanel.querySelector(".nc-cd-messages");
      if (!messagesEl) return;
      resetPanelMessages(messagesEl);
      history.forEach((entry) => {
        const role = entry?.role === "user" ? "user" : "assistant";
        appendPanelMessage(messagesEl, role, entry?.content || "", activeCellIndex);
      });
      messagesEl.scrollTop = messagesEl.scrollHeight;
      ensureMarkdownIt(() => {
        markdownRenderer = getMarkdownRenderer();
        refreshAssistantMarkdown(activePanel);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      });
      return;
    }

    if (msg.type === "HISTORY_CLEARED") {
      const clearedSid = String(msg.sessionId || "");
      if (clearedSid !== activeSessionId) return;
      const messagesEl = activePanel.querySelector(".nc-cd-messages");
      resetPanelMessages(messagesEl);
      streamBuffer = "";
      isStreaming = false;
      if (typeof activePanel.__ncFinalizeStream === "function") {
        activePanel.__ncAfterClear?.();
      }
      return;
    }

    const sid = String(msg?.sessionId || "");
    if (sid !== activeSessionId) return;

    if (msg.type === "CHAT_STREAM") {
      if (!isStreaming) return;
      if (typeof activePanel.__ncAppendDelta === "function") {
        activePanel.__ncAppendDelta(msg.delta || "");
      }
      return;
    }

    if (msg.type === "CHAT_STREAM_END" || msg.type === "CHAT_RESPONSE") {
      if (!isStreaming) return;
      if (typeof activePanel.__ncFinalizeStream === "function") {
        activePanel.__ncFinalizeStream({
          text: msg.response || streamBuffer,
          error: msg.error,
          stopped: msg.stopped,
        });
      }
      return;
    }
  }

  if (hasChromeRuntime()) {
    chrome.runtime.onMessage.addListener(onRuntimeMessage);
  }

  document.addEventListener(
    "click",
    (e) => {
      if (!activePanel) return;
      if (activePanel.contains(e.target)) return;
      if (e.target.closest && e.target.closest(`.${BADGE_CLASS}`)) return;
      closePanel();
    },
    true
  );

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && activePanel) closePanel();
  });

  let scanTimer = null;
  function scheduleScan(root) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => scanForBadges(root), 100);
  }

  function observeRoot(root) {
    if (!root || root.__ncDebugObserved) return;
    root.__ncDebugObserved = true;
    const observer = new MutationObserver(() => scheduleScan(root));
    observer.observe(root, { subtree: true, childList: true });
    scheduleScan(root);
  }

  function start() {
    ensureStyles(document);
    resolveNotebookUrl(() => {});
    ensureMarkdownIt(() => {
      markdownRenderer = getMarkdownRenderer();
      refreshAssistantMarkdown(activePanel);
    });
    observeRoot(document.body || document.documentElement);
    scheduleScan(document);
    setInterval(() => scanForBadges(document), 2500);
    setInterval(() => resolveNotebookUrl(() => {}), 2000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
