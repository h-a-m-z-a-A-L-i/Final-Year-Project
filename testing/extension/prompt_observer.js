(function observePromptTitle(){
  if (window.__nc_prompt_observer_installed) return;
  window.__nc_prompt_observer_installed = true;

  const RE = /Cell execution(?: is)? queued|Cell started execution|Cell executed(?: in| at)?/i;
  const PROMPT_SELECTOR = '.jp-InputPrompt, .jp-InputPrompt.jp-InputArea-prompt';
  const observedRoots = new WeakSet();
  const lastPromptTitle = new WeakMap();
  const seenTitles = new Set();
  let debugBox = null;

  if (!document.querySelector(PROMPT_SELECTOR)) {
    return;
  }

  function ensureDebugBox(){
    if (debugBox && document.contains(debugBox)) return debugBox;
    debugBox = document.getElementById('nc-prompt-debug-box');
    if (debugBox) return debugBox;

    debugBox = document.createElement('div');
    debugBox.id = 'nc-prompt-debug-box';
    debugBox.style.cssText = [
      'position:fixed',
      'right:12px',
      'bottom:12px',
      'z-index:2147483647',
      'max-width:320px',
      'padding:10px 12px',
      'border-radius:10px',
      'border:1px solid rgba(0,0,0,.18)',
      'background:rgba(18,24,38,.96)',
      'color:#fff',
      'font:12px/1.45 monospace',
      'box-shadow:0 10px 30px rgba(0,0,0,.25)',
      'white-space:pre-wrap',
      'pointer-events:none'
    ].join(';');
    debugBox.textContent = 'NC detector waiting...';
    (document.body || document.documentElement).appendChild(debugBox);
    return debugBox;
  }

  function updateDebugBox(text){
    try {
      const box = ensureDebugBox();
      if (box) box.textContent = text;
    } catch(e){}
  }

  function getCellIndex(promptEl){
    let cell = promptEl;
    for (let i = 0; i < 10; i++){
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

  function sendSignal(text, cellIndex){
    try {
      chrome.runtime.sendMessage({
        type: 'PROMPT_SIGNAL',
        text: String(text || '').slice(0, 200),
        cellIndex: cellIndex,
        ts: Date.now()
      });
      console.log(`[NC-OBSERVER] Flag detected: "${text}" at cell ${cellIndex}`);
      updateDebugBox(`NC detector\nFound: ${text}\nCell: ${cellIndex || '?'}`);
    } catch(e){}
  }

  function checkPromptTitle(promptEl){
    const title = (promptEl.getAttribute('title') || '').trim();
    if (lastPromptTitle.get(promptEl) === title) return;
    lastPromptTitle.set(promptEl, title);

    if (title) {
      updateDebugBox(`NC detector\nSeen: ${title}`);
    }

    if (title && RE.test(title) && !seenTitles.has(title)){
      seenTitles.add(title);
      sendSignal(title, getCellIndex(promptEl));
    }
  }

  function scanRoot(root){
    if (!root) return;
    const prompts = root.querySelectorAll ? root.querySelectorAll(PROMPT_SELECTOR) : [];
    for (const prompt of prompts) checkPromptTitle(prompt);

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
        if (mutation.type === 'attributes' && mutation.attributeName === 'title'){
          checkPromptTitle(mutation.target);
        }
        if (mutation.type === 'childList'){
          for (const node of mutation.addedNodes){
            if (node && node.nodeType === 1){
              if (node.shadowRoot) observeRoot(node.shadowRoot);
              if (node.matches && node.matches(PROMPT_SELECTOR)) checkPromptTitle(node);
              scanRoot(node);
            }
          }
        }
      }
    });

    observer.observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['title']
    });

    scanRoot(root);
  }

  function start(){
    updateDebugBox('NC detector\nWatching Kaggle cells...');
    observeRoot(document);
    if (document.documentElement) observeRoot(document.documentElement);
    if (document.body) observeRoot(document.body);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  }
  start();
})();
