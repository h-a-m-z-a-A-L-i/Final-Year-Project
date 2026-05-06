(function pollPromptString(){
  if (window.__nc_prompt_poll_installed) return;
  window.__nc_prompt_poll_installed = true;
  const RE = /Cell execution queued|Cell started execution|Cell executed/i;
  let lastSeen = {};

  function getCellIndex(promptEl){
    let cell = promptEl;
    for (let i = 0; i < 10; i++){
      cell = cell.parentElement;
      if (!cell) break;
      if (cell.classList && cell.classList.contains('jp-Cell')){
        const uuid = cell.getAttribute('data-uuid');
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
      console.log(`[NC-POLL] Flag detected: "${text}" at cell ${cellIndex}`);
    } catch(e){}
  }

  setInterval(() => {
    try{
      const prompts = document.querySelectorAll('.jp-InputPrompt');
      for (const p of prompts){
        const text = (p.innerText || p.textContent || '').trim();
        if (text && RE.test(text) && !lastSeen[text]){
          lastSeen[text] = true;
          const cellIdx = getCellIndex(p);
          sendSignal(text, cellIdx);
        }
      }
    }catch(e){}
  }, 50);
})();
