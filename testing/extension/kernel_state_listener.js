// Content script to relay kernel state updates from background to page scripts
(function initKernelStateListener() {
  const LISTENER_VERSION = '2026-06-13-bot-result-type-fix';
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
        const domIdx = Number(msg.cellIndex);
        const maxWaitMs = Number(msg.maxWaitMs) || 160;

        if (frameOwnsNotebookCells()) {
          const immediate = findCellByDomIndex(document, domIdx);
          if (immediate) {
            const clickResult = dispatchCellClick(immediate, {
              scrollIntoView: msg.scrollIntoView,
              runCell: false,
            });
            if (clickResult?.ok) {
              const attr = immediate.getAttribute('data-windowed-list-index');
              const resolvedDom = attr !== null && attr !== '' ? Number(attr) : domIdx;
              sendResponse({
                ok: true,
                result: {
                  ok: true,
                  domIndex: resolvedDom,
                  appIndex: resolvedDom + 1,
                  cellIndex: resolvedDom + 1,
                  dataWindowedListIndex: String(resolvedDom),
                  ...clickResult,
                },
              });
              return;
            }
          }
          if (selectCellViaNotebookApi(domIdx)) {
            sendResponse({
              ok: true,
              result: {
                ok: true,
                domIndex: domIdx,
                appIndex: domIdx + 1,
                cellIndex: domIdx + 1,
                dataWindowedListIndex: String(domIdx),
                strategy: 'notebook-api-active-index',
              },
            });
            return;
          }
        }

        clickCellByIndexAsync(msg.cellIndex, {
          scrollIntoView: msg.scrollIntoView,
          runCell: false,
          maxWaitMs,
        })
          .then((result) => sendResponse({ ok: Boolean(result?.ok), result }))
          .catch((error) => sendResponse({ ok: false, error: error?.message || String(error) }));
        return true;
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
        const domIdx = Number(msg.cellIndex);
        const runCell = msg.runCell === true;
        const maxWaitMs = Number(msg.maxWaitMs) || (runCell ? 240 : 160);

        if (runCell && frameOwnsNotebookCells()) {
          const viaCmd = runCellViaNotebookCommands(domIdx);
          if (viaCmd?.ok) {
            sendResponse({ ok: true, result: viaCmd });
            return;
          }
        }

        clickCellByIndexAsync(msg.cellIndex, {
          scrollIntoView: msg.scrollIntoView,
          runCell,
          maxWaitMs,
        })
          .then((result) => sendResponse({ ok: Boolean(result?.ok), result }))
          .catch((error) => sendResponse({ ok: false, error: error?.message || String(error) }));
        return true;
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

  async function setCellEditorContent(domIndex, text, options = {}) {
    const maxWaitMs = Number.isFinite(Number(options.maxWaitMs)) ? Number(options.maxWaitMs) : 160;
    const work = setCellEditorContentImpl(domIndex, text, options);
    const watchdog = sleep(maxWaitMs + 80).then(() => ({
      ok: false,
      error: 'set_cell_content timed out in content script.',
      domIndex: Number(domIndex),
      timedOut: true,
    }));
    return Promise.race([work, watchdog]);
  }

  function buildContentSetSuccess(domIdx, payload, strategy) {
    return {
      ok: true,
      domIndex: domIdx,
      appIndex: domIdx + 1,
      cellIndex: domIdx + 1,
      dataWindowedListIndex: String(domIdx),
      chars: payload.length,
      phase: 'content_set',
      strategy,
    };
  }

  function trySyncFillAtDomIndex(domIdx, payload) {
    const wrapper = findCellByDomIndex(document, domIdx);
    if (!wrapper) return null;
    const editor = findCellEditorSurface(wrapper);
    if (!editor) return null;
    const inserted = fillEditorContent(editor, payload);
    if (!inserted.ok) return null;
    return buildContentSetSuccess(domIdx, payload, inserted.strategy);
  }

  async function setCellEditorContentImpl(domIndex, text, options = {}) {
    if (!frameOwnsNotebookCells()) {
      return {
        ok: false,
        frameSkip: true,
        error: 'No notebook cells in this frame, skipping.',
        domIndex: Number(domIndex),
      };
    }

    const domIdx = Number(domIndex);
    const payload = String(text ?? '');
    if (!Number.isInteger(domIdx) || domIdx < 0) {
      return { ok: false, error: 'Invalid DOM cell index (must be >= 0, matches data-windowed-list-index).' };
    }

    selectCellViaNotebookApi(domIdx);

    let filled = trySyncFillAtDomIndex(domIdx, payload);
    if (filled) return filled;

    const wrapper = findCellByDomIndex(document, domIdx);
    if (wrapper) {
      const host =
        wrapper.querySelector('.jp-InputArea-editor, .cm-editor, .jp-Cell-editor, .jp-InputArea') || wrapper;
      if (typeof host.click === 'function') {
        host.click();
      }
      filled = trySyncFillAtDomIndex(domIdx, payload);
      if (filled) return filled;
    }

    const pollMs = Math.min(40, Math.max(10, Number(options.maxWaitMs) || 120));
    const deadline = Date.now() + pollMs;
    while (Date.now() < deadline) {
      await sleep(8);
      filled = trySyncFillAtDomIndex(domIdx, payload);
      if (filled) return filled;
    }

    const viaCmd = setContentViaNotebookCommands(domIdx, payload);
    if (viaCmd?.ok) {
      return viaCmd;
    }

    const click = await clickCellByIndexAsync(domIdx, {
      scrollIntoView: true,
      runCell: false,
      maxWaitMs: Math.min(160, Number(options.maxWaitMs) || 160),
    });
    if (!click?.ok) {
      return { ok: false, error: click?.error || 'Failed to select cell.', domIndex: domIdx };
    }

    filled = trySyncFillAtDomIndex(domIdx, payload);
    if (filled) return filled;

    const retryWrapper = findCellByDomIndex(document, domIdx);
    if (!retryWrapper) {
      return { ok: false, error: `Cell wrapper not found for DOM index ${domIdx}.`, domIndex: domIdx, appIndex: domIdx + 1 };
    }

    await enterCellEditMode(retryWrapper);
    const editor = findCellEditorSurface(retryWrapper);
    if (!editor) {
      return { ok: false, error: 'Code editor surface not found.', domIndex: domIdx, appIndex: domIdx + 1 };
    }

    const inserted = fillEditorContent(editor, payload);
    if (!inserted.ok) {
      return { ok: false, error: inserted.error || 'Failed to write editor content.', domIndex: domIdx, appIndex: domIdx + 1 };
    }

    return buildContentSetSuccess(domIdx, payload, inserted.strategy);
  }

  function findCellEditorSurface(wrapper) {
    if (!wrapper) return null;
    return (
      wrapper.querySelector('.jp-InputArea-editor .cm-content') ||
      wrapper.querySelector('.cm-editor .cm-content') ||
      wrapper.querySelector('.cm-content') ||
      wrapper.querySelector('[contenteditable="true"]')
    );
  }

  async function enterCellEditMode(wrapper) {
    if (!wrapper) return;
    const isMarkdown = wrapper.classList.contains('jp-MarkdownCell');
    if (isMarkdown) {
      wrapper.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, detail: 2 }));
      await sleep(15);
      return;
    }
    const editorHost =
      wrapper.querySelector('.jp-InputArea-editor, .cm-editor, .jp-Cell-editor, .jp-InputArea') || wrapper;
    if (typeof editorHost.click === 'function') {
      editorHost.click();
    }
  }

  function resolveCodeMirrorView(node) {
    let el = node;
    while (el) {
      if (el.cmView?.view) return el.cmView.view;
      if (el.__cm_view) return el.__cm_view;
      el = el.parentElement;
    }
    return null;
  }

  function fillEditorContent(editor, payload) {
    editor.focus({ preventScroll: true });

    const cmView = resolveCodeMirrorView(editor);
    if (cmView && typeof cmView.dispatch === 'function') {
      try {
        const len = cmView.state.doc.length;
        cmView.dispatch({
          changes: { from: 0, to: len, insert: payload },
        });
        return { ok: true, strategy: 'codemirror6-dispatch' };
      } catch (error) {
        console.warn('[fillEditorContent] CM6 dispatch failed:', error?.message || error);
      }
    }

    try {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(editor);
      selection?.removeAllRanges();
      selection?.addRange(range);
      document.execCommand('selectAll', false, null);
      if (document.execCommand('insertText', false, payload)) {
        return { ok: true, strategy: 'execCommand-insertText' };
      }
    } catch (error) {
      console.warn('[fillEditorContent] execCommand failed:', error?.message || error);
    }

    try {
      editor.textContent = payload;
      editor.dispatchEvent(new InputEvent('input', { bubbles: true, data: payload, inputType: 'insertText' }));
      return { ok: true, strategy: 'textContent-fallback' };
    } catch (error) {
      return { ok: false, error: error?.message || String(error) };
    }
  }

  function runCellViaNotebookCommands(domIndex) {
    const { commands } = getNotebookApp();
    if (!commands || typeof commands.execute !== 'function') {
      return { ok: false };
    }

    const domIdx = Number(domIndex);
    if (!Number.isInteger(domIdx) || domIdx < 0) {
      return { ok: false, error: 'Invalid DOM cell index.' };
    }

    try {
      selectCellViaNotebookApi(domIdx);
      const commandIds = [
        'notebook:run-cell',
        'notebook:run-in-place',
        'cell:run-cell',
        'runmenu:run',
        'notebook:execute',
      ];
      for (const commandId of commandIds) {
        try {
          if (typeof commands.hasCommand === 'function' && !commands.hasCommand(commandId)) {
            continue;
          }
          if (typeof commands.isEnabled === 'function' && !commands.isEnabled(commandId)) {
            continue;
          }
          const pending = commands.execute(commandId);
          if (pending && typeof pending.catch === 'function') {
            pending.catch(() => null);
          }
          return {
            ok: true,
            domIndex: domIdx,
            appIndex: domIdx + 1,
            cellIndex: domIdx + 1,
            dataWindowedListIndex: String(domIdx),
            phase: 'run_triggered',
            strategy: 'jupyterlab-run-command-async',
            commandId,
          };
        } catch (executeError) {
          console.warn('[runCellViaNotebookCommands] execute failed:', commandId, executeError?.message || executeError);
        }
      }
    } catch (error) {
      console.warn('[runCellViaNotebookCommands] failed:', error?.message || error);
      return { ok: false };
    }

    return { ok: false };
  }

  function setContentViaNotebookCommands(domIndex, payload) {
    const { commands } = getNotebookApp();
    if (!commands || typeof commands.execute !== 'function') {
      return { ok: false };
    }
    try {
      selectCellViaNotebookApi(domIndex);
      try {
        const pending = commands.execute('notebook:replace-selection', { text: payload });
        if (pending && typeof pending.catch === 'function') {
          pending.catch(() => null);
        }
      } catch (executeError) {
        console.warn('[setContentViaNotebookCommands] execute failed:', executeError?.message || executeError);
        return { ok: false };
      }
      return {
        ok: true,
        domIndex,
        appIndex: domIndex + 1,
        cellIndex: domIndex + 1,
        chars: payload.length,
        phase: 'content_set',
        strategy: 'jupyter-replace-selection-async',
      };
    } catch (error) {
      console.warn('[setContentViaNotebookCommands] failed:', error?.message || error);
      return { ok: false };
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
        sendResponse({ ok: Boolean(result?.ok), result });
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

      if (msg.type === 'SET_CELL_CONTENT') {
        const domIdx = Number(msg.cellIndex);
        const payload = String(msg.content ?? '');
        const maxWaitMs = Number(msg.maxWaitMs) || 160;

        if (frameOwnsNotebookCells()) {
          selectCellViaNotebookApi(domIdx);
          let filled = trySyncFillAtDomIndex(domIdx, payload);
          if (!filled) {
            const wrapper = findCellByDomIndex(document, domIdx);
            if (wrapper) {
              const host =
                wrapper.querySelector('.jp-InputArea-editor, .cm-editor, .jp-Cell-editor, .jp-InputArea')
                || wrapper;
              if (typeof host.click === 'function') {
                host.click();
              }
              filled = trySyncFillAtDomIndex(domIdx, payload);
            }
          }
          if (filled) {
            sendResponse({ ok: true, result: filled });
            return;
          }

          const viaCmd = setContentViaNotebookCommands(domIdx, payload);
          if (viaCmd?.ok) {
            sendResponse({ ok: true, result: viaCmd });
            return;
          }
        }

        setCellEditorContent(msg.cellIndex, msg.content, { maxWaitMs })
          .then((result) => sendResponse({ ok: Boolean(result?.ok), result }))
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

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function forceVisibleSelection(cellEl) {
    if (!cellEl) return;
    try {
      cellEl.classList.add('jp-mod-selected', 'jp-mod-active');
      cellEl.setAttribute('aria-selected', 'true');
    } catch (error) {
      console.warn('[forceVisibleSelection] failed:', error?.message || error);
    }
  }

  function collectVisibleDomIndices(rootDocument = document, seen = new Set()) {
    if (!rootDocument || seen.has(rootDocument)) {
      return [];
    }
    seen.add(rootDocument);

    const indices = [];
    rootDocument.querySelectorAll('[data-windowed-list-index]').forEach((el) => {
      const raw = el.getAttribute('data-windowed-list-index');
      const n = Number(raw);
      if (Number.isInteger(n) && n >= 0) {
        indices.push(n);
      }
    });

    const frames = rootDocument.querySelectorAll('iframe');
    for (const frame of frames) {
      try {
        if (frame.contentDocument) {
          indices.push(...collectVisibleDomIndices(frame.contentDocument, seen));
        }
      } catch (_) {
        /* cross-origin */
      }
    }

    return Array.from(new Set(indices)).sort((a, b) => a - b);
  }

  function findCellByDomIndex(rootDocument, domIdx, seen = new Set()) {
    if (!rootDocument || seen.has(rootDocument)) {
      return null;
    }
    seen.add(rootDocument);

    const direct = rootDocument.querySelector('[data-windowed-list-index="' + domIdx + '"]');
    if (direct) {
      return direct;
    }

    const frames = rootDocument.querySelectorAll('iframe');
    for (const frame of frames) {
      try {
        if (frame.contentDocument) {
          const nested = findCellByDomIndex(frame.contentDocument, domIdx, seen);
          if (nested) {
            return nested;
          }
        }
      } catch (error) {
        console.warn('[findCellByDomIndex] Unable to inspect iframe:', error?.message || error);
      }
    }

    return null;
  }

  function scrollTowardDomIndex(targetDomIdx) {
    const visible = collectVisibleDomIndices(document);
    if (!visible.length) {
      return false;
    }
    const closest = visible.reduce((best, cur) => (
      Math.abs(cur - targetDomIdx) < Math.abs(best - targetDomIdx) ? cur : best
    ), visible[0]);
    const el = findCellByDomIndex(document, closest);
    if (!el) {
      return false;
    }
    el.scrollIntoView({ block: 'center', inline: 'nearest' });
    return true;
  }

  function frameOwnsNotebookCells() {
    return Boolean(document.querySelector('[data-windowed-list-index]'));
  }

  function frameHasNotebookCells() {
    return frameOwnsNotebookCells();
  }

  function selectCellViaNotebookApi(domIndex) {
    const { notebook } = getNotebookApp();
    if (!notebook) {
      return false;
    }
    try {
      if (typeof notebook.activeCellIndex === 'number' || notebook.activeCellIndex === undefined) {
        notebook.activeCellIndex = domIndex;
      }
      if (typeof notebook.scrollToCell === 'function') {
        const maybe = notebook.scrollToCell({ index: domIndex });
        if (maybe && typeof maybe.catch === 'function') {
          maybe.catch(() => null);
        }
      }
      return Number(notebook.activeCellIndex) === domIndex;
    } catch (error) {
      console.warn('[selectCellViaNotebookApi] failed:', error?.message || error);
      return false;
    }
  }

  function isElementMostlyVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    return rect.top >= -8 && rect.bottom <= vh + 8;
  }

  function finishClickResult(domIdx, idx, cell, clickResult) {
    const attr = cell.getAttribute('data-windowed-list-index');
    const resolvedDom = attr !== null && attr !== '' ? Number(attr) : domIdx;
    if (!clickResult.ok) {
      return { ...clickResult, domIndex: resolvedDom };
    }
    return {
      ok: true,
      domIndex: resolvedDom,
      appIndex: resolvedDom + 1,
      cellIndex: resolvedDom + 1,
      dataWindowedListIndex: String(resolvedDom),
      ...clickResult,
    };
  }

  async function activateCellViaNotebookApi(domIndex) {
    return selectCellViaNotebookApi(domIndex);
  }

  async function resolveCellElement(domIndex, options = {}) {
    const quick = findCellByDomIndex(document, domIndex);
    if (quick) {
      return { cell: quick, domIndex };
    }

    const maxWaitMs = Number.isFinite(Number(options.maxWaitMs)) ? Number(options.maxWaitMs) : 400;
    const deadline = Date.now() + Math.max(200, maxWaitMs);

    await activateCellViaNotebookApi(domIndex);

    while (Date.now() < deadline) {
      const cell = findCellByDomIndex(document, domIndex);
      if (cell) {
        return { cell, domIndex };
      }
      scrollTowardDomIndex(domIndex);
      await sleep(80);
    }

    const visible = collectVisibleDomIndices(document);
    return {
      cell: null,
      domIndex,
      visibleDomIndices: visible,
    };
  }

  function dispatchCellClick(cell, options = {}) {
    const runCell = options.runCell === true;
    if (!runCell) {
      if (options.scrollIntoView !== false && !isElementMostlyVisible(cell)) {
        cell.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
      const clickTarget =
        cell.querySelector('.jp-InputArea-prompt, .jp-Cell-prompt, .jp-Cell-inputWrapper') || cell;
      if (typeof clickTarget.click === 'function') {
        clickTarget.click();
      } else if (typeof cell.click === 'function') {
        cell.click();
      }
      forceVisibleSelection(cell);
      return {
        ok: true,
        clicked: clickTarget.className || clickTarget.tagName,
        strategy: 'dom-fast-click',
      };
    }

    if (options.scrollIntoView !== false && !isElementMostlyVisible(cell)) {
      cell.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
    const targets = [
      cell,
      runCell ? cell.querySelector('.jp-InputArea-editor, .jp-Cell-editor') : null,
      cell.querySelector('.jp-InputArea-prompt, .jp-Cell-prompt'),
      cell.querySelector('.cm-editor, .cm-content, [contenteditable="true"]'),
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

        target.dispatchEvent(new PointerEvent('pointerdown', eventInit));
        target.dispatchEvent(new MouseEvent('mousedown', eventInit));
        target.dispatchEvent(new PointerEvent('pointerup', eventInit));
        target.dispatchEvent(new MouseEvent('mouseup', eventInit));
        target.dispatchEvent(new MouseEvent('click', eventInit));
        if (typeof target.click === 'function') {
          target.click();
        }

        forceVisibleSelection(cell);

        if (!runCell) {
          return {
            ok: true,
            clicked: target.className || target.tagName,
            strategy: 'dom-click-only',
          };
        }

        const runSelectors = [
          'button[aria-label*="Run"]',
          '.cell-execute-button',
          'button[title*="Run"]',
          'button[data-test-id="run-cell"]',
        ];
        for (const rs of runSelectors) {
          const btn = cell.querySelector(rs);
          if (btn) {
            btn.scrollIntoView?.({ block: 'nearest' });
            btn.focus?.({ preventScroll: true });
            btn.click();
            return {
              ok: true,
              clicked: target.className || target.tagName,
              strategy: 'run-button-click',
              selector: rs,
            };
          }
        }

        const kbInit = {
          key: 'Enter',
          code: 'Enter',
          keyCode: 13,
          which: 13,
          shiftKey: true,
          bubbles: true,
          cancelable: true,
          composed: true,
        };
        target.dispatchEvent(new KeyboardEvent('keydown', kbInit));
        target.dispatchEvent(new KeyboardEvent('keypress', kbInit));
        target.dispatchEvent(new KeyboardEvent('keyup', kbInit));
        return {
          ok: true,
          clicked: target.className || target.tagName,
          strategy: 'dom-click-plus-shift-enter',
        };
      } catch (error) {
        console.warn('[dispatchCellClick] target failed:', error?.message || error);
      }
    }

    return { ok: false, error: 'No clickable target succeeded.' };
  }

  async function clickCellByIndexAsync(domIndex, options = {}) {
    const idx = Number(domIndex);
    if (!Number.isInteger(idx) || idx < 0) {
      return {
        ok: false,
        error: 'Invalid DOM cell index (must be >= 0, matches data-windowed-list-index).',
      };
    }

    const maxWaitMs = Number.isFinite(Number(options.maxWaitMs)) ? Number(options.maxWaitMs) : 160;
    const work = clickCellByIndexAsyncImpl(idx, { ...options, maxWaitMs });
    const watchdog = sleep(maxWaitMs + 40).then(() => ({
      ok: false,
      error: 'click_cell timed out in content script.',
      domIndex: idx,
      timedOut: true,
    }));
    return Promise.race([work, watchdog]);
  }

  async function clickCellByIndexAsyncImpl(domIndex, options = {}) {
    const idx = Number(domIndex);
    if (!Number.isInteger(idx) || idx < 0) {
      return {
        ok: false,
        error: 'Invalid DOM cell index (must be >= 0, matches data-windowed-list-index).',
      };
    }

    if (!frameOwnsNotebookCells()) {
      return {
        ok: false,
        frameSkip: true,
        error: 'No notebook cells in this frame, skipping.',
        domIndex: idx,
      };
    }

    const immediate = findCellByDomIndex(document, idx);
    if (immediate) {
      return finishClickResult(idx, idx, immediate, dispatchCellClick(immediate, options));
    }

    if (selectCellViaNotebookApi(idx)) {
      const cellAfterApi = findCellByDomIndex(document, idx);
      if (cellAfterApi) {
        return finishClickResult(idx, idx, cellAfterApi, dispatchCellClick(cellAfterApi, options));
      }
      return {
        ok: true,
        domIndex: idx,
        appIndex: idx + 1,
        cellIndex: idx + 1,
        dataWindowedListIndex: String(idx),
        strategy: 'notebook-api-active-index',
      };
    }

    const pollMs = Math.min(160, Math.max(40, Number(options.maxWaitMs) || 160));
    const deadline = Date.now() + pollMs;
    while (Date.now() < deadline) {
      const cell = findCellByDomIndex(document, idx);
      if (cell) {
        return finishClickResult(idx, idx, cell, dispatchCellClick(cell, options));
      }
      await sleep(10);
    }

    const visible = collectVisibleDomIndices(document);
    return {
      ok: false,
      error: 'Cell not found in this frame tree (scroll/wait exhausted).',
      domIndex: idx,
      dataWindowedListIndex: String(idx),
      visibleDomIndices: visible,
      hint: visible.length
        ? `Visible data-windowed-list-index values: ${visible.join(', ')}`
        : 'No notebook cells visible in this frame — wait for editor load.',
    };
  }

  function clickCellByIndex(domIndex, options = {}) {
    const idx = Number(domIndex);
    if (!Number.isInteger(idx) || idx < 0) {
      return { ok: false, error: 'Invalid DOM cell index (must be >= 0).' };
    }
    const cell = findCellByDomIndex(document, idx);
    if (!cell) {
      return {
        ok: false,
        error: 'Cell not found in this frame tree.',
        domIndex: idx,
        visibleDomIndices: collectVisibleDomIndices(document),
      };
    }
    const clickResult = dispatchCellClick(cell, options);
    const attr = cell.getAttribute('data-windowed-list-index');
    const resolvedDom = attr !== null && attr !== '' ? Number(attr) : idx;
    return clickResult.ok
      ? {
          ok: true,
          domIndex: resolvedDom,
          appIndex: resolvedDom + 1,
          cellIndex: resolvedDom,
          dataWindowedListIndex: String(resolvedDom),
          ...clickResult,
        }
      : { ...clickResult, domIndex: resolvedDom };
  }

  function selectCellByIndex(domIndex, options = {}) {
    return clickCellByIndex(domIndex, {
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
    const work = insertCellByDirectionImpl(direction);
    const watchdog = sleep(500).then(() => ({
      ok: false,
      error: 'insert_cell timed out in content script.',
      direction: String(direction || '').trim().toLowerCase(),
      timedOut: true,
    }));
    return Promise.race([work, watchdog]);
  }

  async function insertCellByDirectionImpl(direction) {
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
          const pending = commands.execute(commandId);
          if (pending && typeof pending.catch === 'function') {
            pending.catch(() => null);
          }
          return { ok: true, direction: normalizedDirection, strategy: 'jupyterlab-command-async', commandId };
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
            maybeResult.catch(() => null);
          }
          return { ok: true, direction: normalizedDirection, strategy: 'notebook-method-async', methodName };
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
