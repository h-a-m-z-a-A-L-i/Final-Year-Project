(function observePromptTitle(){
  if (window.__nc_prompt_observer_installed) return;
  window.__nc_prompt_observer_installed = true;
  
  const RE = /Cell execution queued|Cell started execution|Cell executed/i;
  let lastSeen = {};
  let debugBox = null;
  let pollInterval = null;

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
      cell = cell.parentElement;
      if (!cell) break;
      if (cell.classList && cell.classList.contains('jp-Cell')){
        const allCells = Array.from(document.querySelectorAll('.jp-Cell'));
        return allCells.indexOf(cell) + 1;
      }
    }
    return 0;
  }

  function sendSignal(text, cellIndex){
    try { 
      chrome.runtime.sendMessage({ 
        type: 'PROMPT_SIGNAL', 
        text: String(text||'').slice(0,200),
        cellIndex: cellIndex,
        ts: Date.now()
      }); 
      console.log(`[NC-OBSERVER] Flag detected: "${text}" at cell ${cellIndex}`);
      updateDebugBox(`NC detector\nFound: ${text}\nCell: ${cellIndex || '?'}`);
    } catch(e){}
  }

  function checkPromptTitle(promptEl){
    if (!promptEl) return;
    const title = (promptEl.getAttribute('title') || '').trim();
    if (title && RE.test(title) && !lastSeen[title]){
      lastSeen[title] = true;
      const cellIdx = getCellIndex(promptEl);
      sendSignal(title, cellIdx);
    }
  }

  function aggressivePoll(){
    try {
      const prompts = document.querySelectorAll('.jp-InputPrompt');
      for (const p of prompts){
        checkPromptTitle(p);
      }
    } catch(e){}
  }

  updateDebugBox('NC detector\nWatching Kaggle cells...');

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations){
      if (mutation.type === 'attributes' && mutation.attributeName === 'title'){
        checkPromptTitle(mutation.target);
      }
    }
  });

  function startObserver(){
    const root = document.documentElement;
    if (!root) return;

    observer.observe(root, {
      subtree: true,
      attributes: true,
      attributeFilter: ['title'],
      attributeOldValue: false,
    });

    aggressivePoll();
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(aggressivePoll, 2);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startObserver, { once: true });
  } else {
    startObserver();
  }

  document.addEventListener('readystatechange', () => {
    if (document.readyState === 'interactive' && !pollInterval){
      startObserver();
    }
  }, { once: true });

  try { startObserver(); } catch(e){}
})();
