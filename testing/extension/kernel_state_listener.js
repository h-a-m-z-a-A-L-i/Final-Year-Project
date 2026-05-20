// Content script to relay kernel state updates from background to page scripts
(function initKernelStateListener() {
  const LISTENER_VERSION = '2026-05-12-key-dispatch-fix-v2';
  if (window.__kernelStateListenerVersion === LISTENER_VERSION) {
    return;
  }

  // Support hot reinjection: remove previous handler so new logic takes effect.
  if (window.__kernelStateListenerHandler) {
    try {
      chrome.runtime.onMessage.removeListener(window.__kernelStateListenerHandler);
    } catch (error) {
      console.warn('[kernel_state_listener] Failed to remove previous listener:', error?.message || error);
    }
  }
  console.log('[kernel_state_listener] Content script initializing at:', new Date().toISOString());
  console.log('[kernel_state_listener] Current URL:', window.location.href);

  // Flag to track if listener is active
  window.__kernelStateListenerReady = true;
  window.__kernelStateListenerVersion = LISTENER_VERSION;

  // Message listener for kernel state updates
  const messageHandler = (msg, sender, sendResponse) => {
    try {
      if (msg.type === 'SELECT_CELL_BY_INDEX') {
        const result = clickCellByIndex(msg.cellIndex, {
          scrollIntoView: msg.scrollIntoView,
          runCell: false,
        });
        sendResponse({ ok: true, result });
        return;
      }

      if (msg.type === 'INSERT_CELL') {
        insertCellByDirection(msg.direction)
          .then(async (result) => {
            if (!result?.ok || msg.toMarkdown !== true) {
              sendResponse({ ok: true, result });
              return;
            }

            const parsedDelay = Number(msg.markdownDelayMs);
            const delayMs = Number.isFinite(parsedDelay) && parsedDelay >= 0 ? parsedDelay : 500;
            await new Promise((resolve) => setTimeout(resolve, delayMs));

            const markdownKeyResult = sendKey('m');
            sendResponse({
              ok: true,
              result: {
                ...result,
                markdownDelayMs: delayMs,
                markdownKeyResult,
                ok: Boolean(result.ok && markdownKeyResult?.ok),
              },
            });
          })
          .catch((error) => sendResponse({ ok: false, error: error?.message || String(error) }));
        return true;
      }

      if (msg.type === 'CLICK_CELL_BY_INDEX') {
        const result = clickCellByIndex(msg.cellIndex, {
          scrollIntoView: msg.scrollIntoView,
          runCell: msg.runCell !== false,
        });
        sendResponse({ ok: true, result });
        return;
      }

  function sendKey(key) {
    const normalizedKey = String(key || '').trim();
    if (!normalizedKey) {
      console.error('[sendKey] Empty key');
      return { ok: false, error: 'Empty key.' };
    }

    // Get the active element, preferring focused editable elements
    let targetEl = document.activeElement || document.body;

    // If this frame is not a notebook frame, do not consume the key command here.
    // Returning ok:false allows background dispatchToFrames to continue into other frames.
    const hasNotebookSurface = Boolean(
      document.querySelector('.jp-Notebook, .jp-Cell, [data-windowed-list-index]')
    );
    if (!hasNotebookSurface) {
      return { ok: false, error: 'No notebook surface in this frame, skipping.', key: normalizedKey };
    }

    // Descend into iframes to find the truly active element
    while (targetEl && targetEl.tagName === 'IFRAME' && targetEl.contentDocument) {
      const nextActive = targetEl.contentDocument.activeElement;
      if (nextActive && nextActive !== targetEl.contentDocument.body) {
        targetEl = nextActive;
      } else {
        // If no specific element is active in the iframe, use the iframe body
        targetEl = targetEl.contentDocument.body || targetEl;
        break;
      }
    }

    if (!targetEl) {
      console.error('[sendKey] No target element found');
      return { ok: false, error: 'No target element found.' };
    }

    // Never treat an iframe element as a successful key target.
    // This is a common false-positive where shortcuts are not handled.
    if (targetEl.tagName === 'IFRAME' || targetEl.tagName === 'FRAME') {
      return { ok: false, error: 'Active target is iframe/frame, skipping frame.', key: normalizedKey };
    }

    // Map single character keys and named keys to their keyCode values
    const keyCodeMap = {
      'a': 65, 'b': 66, 'c': 67, 'd': 68, 'e': 69, 'f': 70, 'g': 71, 'h': 72, 'i': 73, 'j': 74,
      'k': 75, 'l': 76, 'm': 77, 'n': 78, 'o': 79, 'p': 80, 'q': 81, 'r': 82, 's': 83, 't': 84,
      'u': 85, 'v': 86, 'w': 87, 'x': 88, 'y': 89, 'z': 90,
      '0': 48, '1': 49, '2': 50, '3': 51, '4': 52, '5': 53, '6': 54, '7': 55, '8': 56, '9': 57,
      'Enter': 13, 'Escape': 27, ' ': 32,
    };

    // Support lookup by either exact name (e.g. 'Enter') or lowercase single-char (e.g. 'm')
    const keyCode = keyCodeMap[normalizedKey] || keyCodeMap[normalizedKey.toLowerCase()] || normalizedKey.charCodeAt(0);

    const eventInit = {
      key: normalizedKey,
      code: normalizedKey.length === 1 ? `Key${normalizedKey.toUpperCase()}` : normalizedKey,
      keyCode: keyCode,
      which: keyCode,
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window || targetEl.ownerDocument.defaultView,
    };

    try {
      if (typeof targetEl.focus === 'function') {
        targetEl.focus({ preventScroll: true });
      }

      // Dispatch keyboard events in sequence to the specific target
      targetEl.dispatchEvent(new KeyboardEvent('keydown', eventInit));
      targetEl.dispatchEvent(new KeyboardEvent('keypress', eventInit));
      targetEl.dispatchEvent(new KeyboardEvent('keyup', eventInit));

      // Also dispatch to document and window to catch global listeners (like notebook shortcuts)
      document.dispatchEvent(new KeyboardEvent('keydown', eventInit));
      window.dispatchEvent(new KeyboardEvent('keydown', eventInit));

      const isBody = targetEl.tagName === 'BODY' || targetEl.tagName === 'HTML';
      const hasFocus = document.hasFocus();
      
      console.log('[sendKey] Key event dispatched:', normalizedKey, 'on', targetEl.tagName || 'unknown', 'hasFocus:', hasFocus);
      
      // If this frame does not have focus, skip so dispatchToFrames can try other frames.
      if (isBody && !hasFocus) {
        return { ok: false, error: 'Frame not focused, skipping to next.', key: normalizedKey };
      }

      return { ok: true, key: normalizedKey, tagName: targetEl.tagName || 'unknown' };
    } catch (error) {
      console.error('[sendKey] Failed to send key:', error?.message || error);
      return { ok: false, error: error?.message || String(error), key: normalizedKey };
    }
  }

  async function sendKeysSequence(keysStr) {
    const keys = String(keysStr || '').split(/\s+/).filter(Boolean);
    const results = [];
    for (const key of keys) {
      const result = sendKey(key);
      results.push(result);
      // Small delay between keys in a sequence
      await new Promise(r => setTimeout(r, 50));
    }
    return { ok: true, results, keys };
  }

      if (msg.type === 'CLICK_SELECTOR') {
        const result = clickSelector(msg.selector);
        sendResponse({ ok: true, result });
        return;
      }

      if (msg.type === 'SEND_KEY') {
        const result = sendKey(msg.key);
        sendResponse({ ok: true, result });
        return;
      }

      if (msg.type === 'DELETE_CELL') {
        deleteActiveCell()
          .then((result) => sendResponse({ ok: true, result }))
          .catch((error) => sendResponse({ ok: false, error: error?.message || String(error) }));
        return true;
      }

      if (msg.type === 'SEND_KEYS') {
        sendKeysSequence(msg.keys)
          .then((result) => sendResponse({ ok: true, result }))
          .catch((error) => sendResponse({ ok: false, error: error?.message || String(error) }));
        return true;
      }

      if (msg.type === 'PING') {
        console.log('[kernel_state_listener] PING received, responding with PONG');
        sendResponse({ type: 'PONG', ready: true, timestamp: Date.now() });
        return;
      }

      if (msg.type === 'NOTEBOOK_DATA' && msg.kernelScenario) {
        console.log('[kernel_state_listener] NOTEBOOK_DATA received:', msg.kernelScenario, 'at', new Date().toISOString());
        
        // Post to the page so injected scripts can receive it
        window.postMessage({
          type: 'KERNEL_STATE_UPDATE',
          kernelScenario: msg.kernelScenario,
          kernelStatus: msg.kernelStatus,
          kernelState: msg.kernelState,
          timestamp: Date.now()
        }, '*');
        
        console.log('[kernel_state_listener] KERNEL_STATE_UPDATE posted to window');
        sendResponse({ ok: true, received: true, timestamp: Date.now() });
      }
    } catch (e) {
      console.error('[kernel_state_listener] Error in message handler:', e?.message);
      sendResponse({ error: e?.message });
    }
  };

  function clickCellByIndex(index, options = {}) {
    const targetIndex = Number(index);
    if (!Number.isInteger(targetIndex) || targetIndex < 0) {
      console.error('[clickCellByIndex] Invalid cell index:', index);
      return { ok: false, error: 'Invalid cell index.' };
    }

    // Search the current frame first, then recurse into same-origin iframes.
    // This works whether the message lands in the notebook iframe itself or in
    // the top page that owns the iframe.
    const findCell = (rootDocument, seen = new Set()) => {
      if (!rootDocument || seen.has(rootDocument)) {
        return null;
      }
      seen.add(rootDocument);

      const specific_run_selector = `[data-windowed-list-index="${targetIndex}"] button[aria-label="Run"]`;

      const direct = rootDocument.querySelector('[data-windowed-list-index="' + targetIndex + '"]');
      if (direct) {
        return direct;
      }

      const frames = rootDocument.querySelectorAll('iframe');
      for (const frame of frames) {
        try {
          if (frame.contentDocument) {
            const nested = findCell(frame.contentDocument, seen);
            if (nested) {
              return nested;
            }
          }
        } catch (error) {
          console.warn('[clickCellByIndex] Unable to inspect iframe:', error?.message || error);
        }
      }

      return null;
    };

    const cell = findCell(document);
    if (!cell) {
      console.error('[clickCellByIndex] Cell not found in this frame tree:', targetIndex);
      return { ok: false, error: 'Cell not found in this frame tree.' };
    }


    // Keep the cell near the viewport so JupyterLab can attach focus and selection state.
    if (options.scrollIntoView !== false) {
      cell.scrollIntoView({ block: 'nearest' });
    }

    // Click the cell wrapper first so JupyterLab can visibly mark the cell active.
    // If that does not trigger the expected state change, fall back to the editor and prompt.
    const targets = [
      cell,
      options.runCell !== false ? cell.querySelector('.jp-InputArea-editor, .jp-Cell-editor') : null,
      cell.querySelector('.jp-InputArea-prompt, .jp-Cell-prompt'),
    ].filter(Boolean);

    const rect = cell.getBoundingClientRect();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      clientX: Math.max(0, Math.floor(rect.left + rect.width / 2)),
      clientY: Math.max(0, Math.floor(rect.top + rect.height / 2)),
      button: 0,
      buttons: 1,
      detail: 1,
    };

    for (const target of targets) {
      try {
        if (target.focus) {
          target.focus({ preventScroll: true });
        }

        // JupyterLab/React often depends on a full pointer + mouse sequence instead of a bare click.
        target.dispatchEvent(new PointerEvent('pointerdown', eventInit));
        target.dispatchEvent(new MouseEvent('mousedown', eventInit));
        target.dispatchEvent(new PointerEvent('pointerup', eventInit));
        target.dispatchEvent(new MouseEvent('mouseup', eventInit));
        target.dispatchEvent(new MouseEvent('click', eventInit));
        if (typeof target.click === 'function') {
          target.click();
        }

        if (options.runCell !== false) {
          // Immediately simulate Shift+Enter to run the cell, bypassing cross-origin iframe limits
          const kbInit = {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            which: 13,
            shiftKey: true,
            bubbles: true,
            cancelable: true,
            composed: true
          };
          target.dispatchEvent(new KeyboardEvent('keydown', kbInit));
          target.dispatchEvent(new KeyboardEvent('keypress', kbInit));
          target.dispatchEvent(new KeyboardEvent('keyup', kbInit));

          console.log('[clickCellByIndex] Click & Shift+Enter sequence dispatched on:', target.className || target.tagName);
          return { ok: true, cellIndex: targetIndex, clicked: target.className || target.tagName, strategy: 'dom-click-plus-shift-enter' };
        }

        console.log('[clickCellByIndex] Click-only selection dispatched on:', target.className || target.tagName);
        return { ok: true, cellIndex: targetIndex, clicked: target.className || target.tagName, strategy: 'dom-click-only' };
      } catch (error) {
        console.warn('[clickCellByIndex] Target failed, trying next fallback:', error?.message || error);
      }
    }

    // JupyterLab API fallback: if the app object is exposed, use notebook APIs directly.
    const app = window.__JUPYTERLAB_APP__ || window.jupyterlab || window.jupyterLab;
    const notebook = app?.shell?.currentWidget?.content || app?.shell?.currentWidget?.model || null;
    if (notebook) {
      try {
        if (typeof notebook.activeCellIndex === 'number') {
          notebook.activeCellIndex = targetIndex;
        }
        if (typeof notebook.scrollToCell === 'function') {
          notebook.scrollToCell({ index: targetIndex });
        }
        forceVisibleSelection(cell);
        console.log('[clickCellByIndex] Used JupyterLab API fallback for cell:', targetIndex);
        return { ok: true, cellIndex: targetIndex, strategy: 'jupyterlab-api-fallback' };
      } catch (error) {
        console.error('[clickCellByIndex] JupyterLab API fallback failed:', error?.message || error);
      }
    }

    return { ok: false, error: 'No clickable target succeeded.' };
  }

  function selectCellByIndex(index, options = {}) {
    return clickCellByIndex(index, {
      scrollIntoView: options.scrollIntoView,
      runCell: false,
    });
  }

  function getNotebookApp() {
    const app = window.__JUPYTERLAB_APP__ || window.jupyterlab || window.jupyterLab || null;
    const notebook = app?.shell?.currentWidget?.content || app?.shell?.currentWidget || null;
    const commands = app?.commands || window.__JUPYTERLAB_COMMANDS__ || null;
    return { app, notebook, commands };
  }

  function findElementInFrameTree(rootDocument, predicate, seen = new Set()) {
    if (!rootDocument || seen.has(rootDocument)) {
      return null;
    }
    seen.add(rootDocument);

    const directMatch = predicate(rootDocument);
    if (directMatch) {
      return directMatch;
    }

    const frames = rootDocument.querySelectorAll('iframe');
    for (const frame of frames) {
      try {
        if (frame.contentDocument) {
          const nested = findElementInFrameTree(frame.contentDocument, predicate, seen);
          if (nested) {
            return nested;
          }
        }
      } catch (error) {
        console.warn('[insertCellByDirection] Unable to inspect iframe:', error?.message || error);
      }
    }

    return null;
  }

  function findInsertButton(direction) {
    const labels = direction === 'above'
      ? [/insert.*above/i, /add.*above/i, /above/i]
      : [/insert.*below/i, /add.*below/i, /below/i];

    return findElementInFrameTree(document, (rootDocument) => {
      const nodes = rootDocument.querySelectorAll('button, [role="button"], [aria-label], [title]');
      for (const node of nodes) {
        const label = String(
          node.getAttribute('aria-label') ||
          node.getAttribute('title') ||
          node.textContent ||
          ''
        ).trim();
        if (!label) {
          continue;
        }
        if (labels.some((pattern) => pattern.test(label))) {
          return node;
        }
      }
      return null;
    });
  }

  async function insertCellByDirection(direction) {
    const normalizedDirection = String(direction || '').trim().toLowerCase();
    if (!['above', 'below'].includes(normalizedDirection)) {
      return { ok: false, error: 'Invalid insert direction.' };
    }

    const commandIds = normalizedDirection === 'above'
      ? ['notebook:insert-cell-above', 'cell:insert-above', 'notebook:insert-above']
      : ['notebook:insert-cell-below', 'cell:insert-below', 'notebook:insert-below'];
    const methodNames = normalizedDirection === 'above'
      ? ['insertCellAbove', 'insertAbove', 'addCellAbove']
      : ['insertCellBelow', 'insertBelow', 'addCellBelow'];

    const { notebook, commands } = getNotebookApp();
    let lastError = null;

    if (commands && typeof commands.execute === 'function') {
      for (const commandId of commandIds) {
        try {
          if (typeof commands.hasCommand === 'function' && !commands.hasCommand(commandId)) {
            continue;
          }
          if (typeof commands.isEnabled === 'function' && !commands.isEnabled(commandId)) {
            continue;
          }
          await commands.execute(commandId);
          return { ok: true, direction: normalizedDirection, strategy: 'jupyterlab-command', commandId };
        } catch (error) {
          lastError = error;
        }
      }
    }

    if (notebook) {
      for (const methodName of methodNames) {
        try {
          if (typeof notebook[methodName] !== 'function') {
            continue;
          }
          const maybeResult = notebook[methodName]();
          if (maybeResult && typeof maybeResult.then === 'function') {
            await maybeResult;
          }
          return { ok: true, direction: normalizedDirection, strategy: 'notebook-method', methodName };
        } catch (error) {
          lastError = error;
        }
      }
    }

    const button = findInsertButton(normalizedDirection);
    if (button) {
      try {
        if (typeof button.scrollIntoView === 'function') {
          button.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
        if (typeof button.focus === 'function') {
          button.focus({ preventScroll: true });
        }
        button.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, composed: true, view: window }));
        button.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, composed: true, view: window }));
        button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, composed: true, view: window }));
        if (typeof button.click === 'function') {
          button.click();
        }
        return { ok: true, direction: normalizedDirection, strategy: 'dom-button-click', label: String(button.getAttribute('aria-label') || button.getAttribute('title') || button.textContent || '').trim() };
      } catch (error) {
        lastError = error;
      }
    }

    return {
      ok: false,
      error: lastError?.message || 'No notebook insert target succeeded.',
      direction: normalizedDirection,
    };
  }

  async function deleteActiveCell() {
    const { notebook, commands } = getNotebookApp();
    let lastError = null;

    // Try JupyterLab command first (most robust)
    if (commands && typeof commands.execute === 'function') {
      const commandIds = ['notebook:delete-cell', 'cell:delete', 'notebook:delete'];
      for (const commandId of commandIds) {
        try {
          if (typeof commands.hasCommand === 'function' && !commands.hasCommand(commandId)) continue;
          await commands.execute(commandId);
          return { ok: true, strategy: 'jupyterlab-command', commandId };
        } catch (error) {
          lastError = error;
        }
      }
    }

    // Fallback: try notebook method directly
    if (notebook) {
      const methodNames = ['deleteCell', 'deleteSelection', 'removeCell'];
      for (const methodName of methodNames) {
        try {
          if (typeof notebook[methodName] === 'function') {
            await notebook[methodName]();
            return { ok: true, strategy: 'notebook-method', methodName };
          }
        } catch (error) {
          lastError = error;
        }
      }
    }

    // Final Fallback: Simulated keys 'dd' if the above failed (unlikely in Jupyter)
    try {
      sendKey('d');
      sendKey('d');
      return { ok: true, strategy: 'fallback-keys-dd' };
    } catch (e) {
      return { ok: false, error: lastError?.message || e.message || 'Failed to delete cell' };
    }
  }

  function clickSelector(selector) {
    const normalizedSelector = String(selector || '').trim();
    if (!normalizedSelector) {
      console.error('[clickSelector] Empty selector');
      return { ok: false, error: 'Empty selector.' };
    }

    if (normalizedSelector === 'Shift+Enter') {
      let activeEl = document.activeElement || document.body;
      
      // Descend into iframes if the active element is an iframe
      while (activeEl && activeEl.tagName === 'IFRAME' && activeEl.contentDocument) {
        const nextActive = activeEl.contentDocument.activeElement;
        if (nextActive && nextActive !== activeEl.contentDocument.body) {
          activeEl = nextActive;
        } else {
          // If no specific element is active in the iframe, dispatch on the iframe body
          activeEl = activeEl.contentDocument.body;
          break;
        }
      }

      const eventInit = {
        key: 'Enter',
        code: 'Enter',
        keyCode: 13,
        which: 13,
        shiftKey: true,
        bubbles: true,
        cancelable: true,
        composed: true
      };
      
      activeEl.dispatchEvent(new KeyboardEvent('keydown', eventInit));
      activeEl.dispatchEvent(new KeyboardEvent('keypress', eventInit));
      activeEl.dispatchEvent(new KeyboardEvent('keyup', eventInit));
      
      console.log('[clickSelector] Dispatched Shift+Enter on', activeEl.tagName || 'unknown');
      return { ok: true, strategy: 'keyboard-shortcut-shift-enter', tagName: activeEl.tagName || 'unknown' };
    }

    const findElement = (rootDocument, selectorStr, seen = new Set()) => {
      if (!rootDocument || seen.has(rootDocument)) return null;
      seen.add(rootDocument);

      // Helper: deep-search including shadow roots
      const deepQuery = (root, sel) => {
        try {
          const direct = root.querySelector(sel);
          if (direct) return direct;
        } catch (e) {}

        // Traverse shadow roots within this root
        try {
          const all = root.querySelectorAll('*');
          for (const el of all) {
            try {
              if (el.shadowRoot) {
                try {
                  const found = el.shadowRoot.querySelector(sel);
                  if (found) return found;
                } catch (ee) {}
                const nested = deepQuery(el.shadowRoot, sel);
                if (nested) return nested;
              }
            } catch (ee) {}
          }
        } catch (e) {}

        return null;
      };

      // Try direct or shadow-root-aware query in this document
      const foundHere = deepQuery(rootDocument, selectorStr);
      if (foundHere) return foundHere;

      // Try searching inside iframes (same-origin frames only)
      const frames = rootDocument.querySelectorAll('iframe');
      for (const frame of frames) {
        try {
          if (frame.contentDocument) {
            const nested = findElement(frame.contentDocument, selectorStr, seen);
            if (nested) return nested;
          }
        } catch (e) {}
      }
      return null;
    };

    const target = findElement(document, normalizedSelector);
    if (!target) {
      console.error('[clickSelector] Target not found in any frame:', normalizedSelector);
      return { ok: false, error: 'Target not found.', selector: normalizedSelector };
    }

    const rect = target.getBoundingClientRect();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      clientX: Math.max(0, Math.floor(rect.left + rect.width / 2)),
      clientY: Math.max(0, Math.floor(rect.top + rect.height / 2)),
      button: 0,
      buttons: 1,
      detail: 1,
    };

    try {
      if (typeof target.scrollIntoView === 'function') {
        target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
      if (typeof target.focus === 'function') {
        target.focus({ preventScroll: true });
      }

      // Prefer native click for buttons/links to avoid duplicate activations.
      try {
        if (target.tagName && /^(BUTTON|A)$/i.test(target.tagName)) {
          target.click();
          console.log('[clickSelector] Native .click() dispatched on button/link:', normalizedSelector);
          return { ok: true, selector: normalizedSelector, tagName: target.tagName, strategy: 'native-click' };
        }

        // Otherwise dispatch a single synthetic click sequence compatible with React.
        target.dispatchEvent(new PointerEvent('pointerdown', eventInit));
        target.dispatchEvent(new MouseEvent('mousedown', eventInit));
        target.dispatchEvent(new PointerEvent('pointerup', eventInit));
        target.dispatchEvent(new MouseEvent('mouseup', eventInit));
        target.dispatchEvent(new MouseEvent('click', eventInit));

        console.log('[clickSelector] Synthetic click sequence dispatched on:', normalizedSelector);
        return { ok: true, selector: normalizedSelector, tagName: target.tagName, strategy: 'synthetic-click' };
      } catch (err) {
        console.error('[clickSelector] Activation failed during single-click path:', err);
        return { ok: false, error: err?.message || String(err), selector: normalizedSelector };
      }
    } catch (error) {
      console.error('[clickSelector] Failed:', error?.message || error);
      return { ok: false, error: error?.message || String(error), selector: normalizedSelector };
    }
  }

  chrome.runtime.onMessage.addListener(messageHandler);
  window.__kernelStateListenerHandler = messageHandler;
  console.log('[kernel_state_listener] Message listener registered and ready');
})();
