// Content script to relay kernel state updates from background to page scripts
(function initKernelStateListener() {
  console.log('[kernel_state_listener] Content script initializing at:', new Date().toISOString());
  console.log('[kernel_state_listener] Current URL:', window.location.href);

  // Flag to track if listener is active
  window.__kernelStateListenerReady = true;

  // Message listener for kernel state updates
  const messageHandler = (msg, sender, sendResponse) => {
    try {
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

  chrome.runtime.onMessage.addListener(messageHandler);
  console.log('[kernel_state_listener] Message listener registered and ready');
})();
