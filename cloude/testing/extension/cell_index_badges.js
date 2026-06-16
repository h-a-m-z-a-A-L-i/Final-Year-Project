(function initCellIndexBadges() {
  const VERSION = "2026-06-11-v4";
  if (window.__ncCellIndexBadgesVersion === VERSION) return;
  window.__ncCellIndexBadgesVersion = VERSION;

  const BADGE_CLASS = "nc-cell-index-badge";
  const COLUMN_CLASS = "nc-prompt-index-col";
  const PROMPT_SELECTORS =
    ".jp-InputPrompt.jp-InputArea-prompt, .jp-InputPrompt, .jp-InputArea-prompt, .jp-Cell-prompt, .input_prompt";
  const observedRoots = new WeakSet();
  let scanTimer = null;

  function ensureStyles(doc) {
    const rootDoc = doc || document;
    if (!rootDoc || rootDoc.getElementById("nc-cell-index-badge-style")) return;

    const style = rootDoc.createElement("style");
    style.id = "nc-cell-index-badge-style";
    style.textContent = `
      .${COLUMN_CLASS} {
        display: flex;
        flex-direction: column;
        align-items: center;
        flex-shrink: 0;
        min-width: 1.5rem;
      }
      .${BADGE_CLASS} {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-top: 2px;
        padding: 0 3px;
        min-width: 20px;
        height: 20px;
        border: 1px solid #1e40af;
        border-radius: 3px;
        background: #2563eb;
        color: #fff;
        font: 700 12px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        letter-spacing: -0.02em;
        text-align: center;
        cursor: pointer;
        user-select: none;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.28);
      }
      .${BADGE_CLASS}:hover {
        background: #1d4ed8;
        border-color: #1e3a8a;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
      }
      .${BADGE_CLASS}.nc-copied {
        background: #16a34a;
        border-color: #15803d;
        color: #fff;
      }
    `;
    (rootDoc.head || rootDoc.documentElement).appendChild(style);
  }

  function toAppIndex(domIndex) {
    const n = Number(domIndex);
    return Number.isFinite(n) ? n + 1 : null;
  }

  function resolveCellIndex(cell) {
    let node = cell;
    for (let i = 0; i < 8 && node; i++) {
      const attr = node.getAttribute && node.getAttribute("data-windowed-list-index");
      if (attr !== null && attr !== "") {
        return toAppIndex(attr);
      }
      node = node.parentElement;
    }

    const notebookRoot = cell.closest(".jp-Notebook") || cell.ownerDocument || document;
    const allCells = Array.from(
      notebookRoot.querySelectorAll("[data-windowed-list-index], .jp-Cell")
    ).filter((el) => !el.closest("[data-windowed-list-index] .jp-Cell") || el.hasAttribute("data-windowed-list-index"));

    const directIdx = allCells.indexOf(cell);
    if (directIdx >= 0) return directIdx + 1;

    const wrapper = cell.closest("[data-windowed-list-index]");
    if (wrapper) {
      return toAppIndex(wrapper.getAttribute("data-windowed-list-index"));
    }

    const jpIdx = Array.from(notebookRoot.querySelectorAll(".jp-Cell")).indexOf(
      cell.classList && cell.classList.contains("jp-Cell") ? cell : cell.querySelector(".jp-Cell")
    );
    return jpIdx >= 0 ? jpIdx + 1 : null;
  }

  function ensurePromptColumn(promptEl) {
    const parent = promptEl.parentElement;
    if (!parent) return null;

    let column = promptEl.closest(`.${COLUMN_CLASS}`);
    if (column) return column;

    column = document.createElement("div");
    column.className = COLUMN_CLASS;
    parent.insertBefore(column, promptEl);
    column.appendChild(promptEl);
    return column;
  }

  function upsertBadge(cellContainer) {
    const index = resolveCellIndex(cellContainer);
    if (index === null) return;

    const host =
      (cellContainer.matches && cellContainer.matches(".jp-Cell")
        ? cellContainer
        : cellContainer.querySelector(".jp-Cell")) || cellContainer;

    ensureStyles(host.ownerDocument || document);

    let badge = host.querySelector(`.${BADGE_CLASS}`);
    if (badge) {
      const label = String(index);
      if (badge.textContent !== label) {
        badge.textContent = label;
        badge.title = `Cell ${index}`;
        badge.setAttribute("data-cell-index", label);
        badge.setAttribute("aria-label", `Cell ${index}`);
      }
      return;
    }

    const promptEl = host.querySelector(PROMPT_SELECTORS);
    let mount = null;

    if (promptEl) {
      mount = ensurePromptColumn(promptEl);
    } else {
      const inputArea = host.querySelector(".jp-InputArea, .input_area");
      if (inputArea) {
        mount = document.createElement("div");
        mount.className = COLUMN_CLASS;
        inputArea.insertBefore(mount, inputArea.firstChild);
      } else {
        const inputWrapper = host.querySelector(".jp-Cell-inputWrapper, .jp-Cell-head");
        if (inputWrapper) {
          mount = document.createElement("div");
          mount.className = COLUMN_CLASS;
          const collapser = inputWrapper.querySelector(".jp-Cell-inputCollapser, .jp-Collapser");
          if (collapser) {
            collapser.insertAdjacentElement("afterend", mount);
          } else {
            inputWrapper.insertBefore(mount, inputWrapper.firstChild);
          }
        }
      }
    }

    if (!mount) return;

    badge = document.createElement("button");
    badge.type = "button";
    badge.className = BADGE_CLASS;
    badge.textContent = String(index);
    badge.title = `Cell ${index}`;
    badge.setAttribute("data-cell-index", String(index));
    badge.setAttribute("aria-label", `Cell ${index}`);

    badge.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      try {
        navigator.clipboard.writeText(String(index));
        badge.classList.add("nc-copied");
        setTimeout(() => badge.classList.remove("nc-copied"), 900);
      } catch (error) {
        console.warn("[nc-cell-index-badge] clipboard copy failed:", error?.message || error);
      }
    });

    mount.appendChild(badge);
  }

  function collectCellContainers(root) {
    const containers = new Set();
    if (!root || typeof root.querySelectorAll !== "function") return containers;

    root.querySelectorAll("[data-windowed-list-index]").forEach((el) => containers.add(el));

    root.querySelectorAll(".jp-Cell").forEach((el) => {
      if (!el.closest("[data-windowed-list-index]")) {
        containers.add(el);
      }
    });

    return containers;
  }

  function scanRoot(root) {
    ensureStyles(root.ownerDocument || document);
    for (const cell of collectCellContainers(root)) {
      try {
        upsertBadge(cell);
      } catch (error) {
        console.warn("[nc-cell-index-badge] upsert failed:", error?.message || error);
      }
    }
  }

  function scheduleScan(root) {
    const target = root || document;
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => scanRoot(target), 80);
  }

  function walkTree(root) {
    if (!root) return;
    scheduleScan(root);

    const doc = root.ownerDocument || document;
    const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
      const el = walker.currentNode;
      if (el.shadowRoot) walkTree(el.shadowRoot);
      if (el.tagName === "IFRAME") {
        try {
          if (el.contentDocument) walkTree(el.contentDocument);
        } catch (error) {
          // Cross-origin iframe; ignore.
        }
      }
    }
  }

  function observeRoot(root) {
    if (!root || observedRoots.has(root)) return;
    observedRoots.add(root);

    const observer = new MutationObserver(() => scheduleScan(root));
    observer.observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-windowed-list-index", "class"],
    });

    scheduleScan(root);
  }

  function start() {
    observeRoot(document);
    if (document.documentElement) observeRoot(document.documentElement);
    if (document.body) observeRoot(document.body);
    walkTree(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  }
  start();

  setInterval(() => walkTree(document), 2500);
})();
