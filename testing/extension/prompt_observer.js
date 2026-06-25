(function observePromptTitle(){
  if (window.__nc_prompt_observer_installed) return;
  window.__nc_prompt_observer_installed = true;

  const RE = /Cell execution(?: is)? queued|Cell started execution|Cell executed(?: in| at)?|Cell is being executed/i;
  const PROMPT_SELECTOR = '.jp-InputPrompt, .jp-InputPrompt.jp-InputArea-prompt, .input_prompt';
  const NC_PROMPT_COL_SELECTOR = '.nc-prompt-index-col';
  const PROMPT_TARGET_SELECTOR = `${PROMPT_SELECTOR}, ${NC_PROMPT_COL_SELECTOR}`;
  const RUN_BUTTON_SELECTOR =
    '.cell-execution-button, [title*="Cell executed"], [title*="Cell started execution"], ' +
    '[title*="Cell execution queued"], [aria-label*="Cell executed"], [aria-label*="Cell started execution"], ' +
    '[aria-label*="Cell execution queued"]';
  const observedRoots = new WeakSet();
  const lastPromptTitle = new WeakMap();
  const lastPromptText = new WeakMap();
  const lastButtonSignal = new WeakMap();
  const lastCellPhase = new Map();

  function classifyPhase(text) {
    const t = String(text || '').trim().toLowerCase();
    if (!t) return '';
    if (/queued|pending/.test(t)) return 'queued';
    if (/started execution|being executed/.test(t)) return 'running';
    if (/executed/.test(t)) return 'executed';
    return '';
  }

  function getCellIndex(promptEl){
    let cell = promptEl;
    for (let i = 0; i < 12; i++){
      cell = cell && (cell.parentElement || cell.parentNode || cell.host || null);
      if (!cell) break;
      if (cell.classList && cell.classList.contains('jp-Cell')){
        const root = cell.getRootNode ? cell.getRootNode() : document;
        const allCells = root && root.querySelectorAll ? Array.from(root.querySelectorAll('.jp-Cell')) : Array.from(document.querySelectorAll('.jp-Cell'));
        const index = allCells.indexOf(cell);
        if (index >= 0) return index + 1;

        const fallbackIndex = cell.getAttribute && cell.getAttribute('data-windowed-list-index');
        if (fallbackIndex != null && fallbackIndex !== '') return Number(fallbackIndex) + 1;

        return 0;
      }
    }
    return 0;
  }

  function getExecutionOrder(promptEl) {
    const text = (promptEl.innerText || promptEl.textContent || "").trim();
    const match = text.match(/\d+/);
    return match ? Number(match[0]) : null;
  }

  function sendSignal(text, cellIndex, execOrder, phase){
    const idx = Number(cellIndex) || 0;
    const p = phase || classifyPhase(text);
    if (!p) return;
    const prev = lastCellPhase.get(idx);
    if (prev === p) return;
    lastCellPhase.set(idx, p);

    try {
      chrome.runtime.sendMessage({
        type: 'PROMPT_SIGNAL',
        text: String(text || '').slice(0, 200),
        cellIndex: cellIndex,
        execOrder: execOrder,
        phase: p,
        ts: Date.now()
      });
      console.log(`[NC-OBSERVER] ${p} cell=${cellIndex} order=${execOrder} "${String(text || '').slice(0, 80)}"`);
    } catch(e){}
  }

  function checkPromptTitle(promptEl){
    const title = (promptEl.getAttribute('title') || promptEl.getAttribute('aria-label') || '').trim();
    const inner = (promptEl.innerText || promptEl.textContent || '').trim();
    const combined = title || inner;
    const titleChanged = lastPromptTitle.get(promptEl) !== title;
    const textChanged = lastPromptText.get(promptEl) !== inner;
    if (!titleChanged && !textChanged) return;
    lastPromptTitle.set(promptEl, title);
    lastPromptText.set(promptEl, inner);

    if (combined && RE.test(combined)){
      sendSignal(combined, getCellIndex(promptEl), getExecutionOrder(promptEl));
    }
  }

  function checkRunButton(buttonEl) {
    const signal = String(
      buttonEl.getAttribute('title') ||
      buttonEl.getAttribute('aria-label') ||
      buttonEl.title ||
      buttonEl.innerText ||
      buttonEl.textContent ||
      ''
    ).trim();
    if (!signal || lastButtonSignal.get(buttonEl) === signal) return;
    lastButtonSignal.set(buttonEl, signal);
    if (!RE.test(signal)) return;
    const cell = buttonEl.closest('.jp-Cell, .cell, .code_cell');
    const cellIndex = cell ? getCellIndex(cell.querySelector(PROMPT_SELECTOR) || cell) : 0;
    sendSignal(signal, cellIndex, null);
  }

  function scanRoot(root){
    if (!root) return;
    const prompts = root.querySelectorAll ? root.querySelectorAll(PROMPT_TARGET_SELECTOR) : [];
    for (const prompt of prompts) checkPromptTitle(prompt);
    const buttons = root.querySelectorAll ? root.querySelectorAll(RUN_BUTTON_SELECTOR) : [];
    for (const btn of buttons) checkRunButton(btn);

    const treeWalker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    while (treeWalker.nextNode()){
      const el = treeWalker.currentNode;
      if (el.shadowRoot) observeRoot(el.shadowRoot);
      if (el.tagName === 'IFRAME'){
        try {
          if (el.contentDocument) observeRoot(el.contentDocument);
        } catch(e){}
      }
    }
  }

  function observeRoot(root){
    if (!root || observedRoots.has(root)) return;
    observedRoots.add(root);

    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations){
        if (mutation.type === 'attributes' && (mutation.attributeName === 'title' || mutation.attributeName === 'aria-label')){
          const target = mutation.target;
          if (target && target.matches && target.matches(PROMPT_TARGET_SELECTOR)) {
            checkPromptTitle(target);
          } else if (target && target.matches && target.matches(RUN_BUTTON_SELECTOR)) {
            checkRunButton(target);
          }
        }
        if (mutation.type === 'characterData' || mutation.type === 'childList'){
          const node = mutation.target;
          if (node && node.nodeType === 3) {
            const prompt = node.parentElement && node.parentElement.closest && node.parentElement.closest(PROMPT_TARGET_SELECTOR);
            if (prompt) checkPromptTitle(prompt);
          }
          if (mutation.type === 'childList'){
            for (const added of mutation.addedNodes){
              if (added && added.nodeType === 1){
                if (added.shadowRoot) observeRoot(added.shadowRoot);
                if (added.matches && added.matches(PROMPT_TARGET_SELECTOR)) checkPromptTitle(added);
                if (added.matches && added.matches(RUN_BUTTON_SELECTOR)) checkRunButton(added);
                scanRoot(added);
              }
            }
          }
        }
      }
    });

    observer.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['title', 'aria-label', 'class']
    });

    scanRoot(root);
  }

  function start(){
    observeRoot(document);
    if (document.documentElement) observeRoot(document.documentElement);
    if (document.body) observeRoot(document.body);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  }
  start();

  // Prompts may appear after initial load (virtualized notebook).
  let retries = 0;
  const retryTimer = setInterval(() => {
    retries += 1;
    if (document.querySelector(PROMPT_TARGET_SELECTOR)) {
      start();
      if (retries >= 3) clearInterval(retryTimer);
    }
    if (retries >= 40) clearInterval(retryTimer);
  }, 500);
})();
