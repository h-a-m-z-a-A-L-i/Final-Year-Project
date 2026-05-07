(function() {
    if (document.getElementById('injected-copilot-panel-wrapper')) return;

    // 1. Inject Styles
    const style = document.createElement('style');
    style.id = 'copilot-styles';
    style.textContent = `:root {
            --cp-bg: #1e1f23;
            --cp-text: #f0f0f0;
            --cp-accent: #47a1ff;
            --cp-accent-hover: #6cb6ff;
            --cp-bubble-user: #47a1ff;
            --cp-bubble-user-text: #ffffff;
            --cp-bubble-bot: #2b2c31;
            --cp-bubble-bot-border: #3f4046;
            --cp-header-bg: #1e1f23;
            --cp-border: #33343a;
            --cp-shadow: rgba(0, 0, 0, 0.3);
        }

        #injected-copilot-panel-wrapper {
            position: fixed;
            top: 0;
            right: 0;
            width: min(395px, 100vw);
            height: 100vh;
            background: var(--cp-bg);
            color: var(--cp-text);
            box-shadow: -4px 0 15px var(--cp-shadow);
            z-index: 9999999;
            display: none;
            overflow: hidden;
            flex-direction: column;
            border-left: 1px solid var(--cp-border);
            transition: transform 0.3s ease;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            overflow-y: hidden;
        }

        #injected-copilot-panel-wrapper.active { display: flex; animation: cpSlideIn 0.3s ease; }
        @keyframes cpSlideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }

        .copilot-container {
            display: flex;
            flex-direction: column;
            height: 100%;
            position: relative;
        }

        .copilot-header {
            height: 40px;
            padding: 0 11px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            position: sticky;
            top: 0;
            z-index: 10;
        }
        .header-left { display: flex; align-items: center; gap: 10px; }
        .header-title { font-weight: 600; font-size: 14px; }
        .header-actions { display: flex; align-items: center; gap: 6px; }
        .header-mini-btn {
            border: 1px solid var(--cp-border);
            background: transparent;
            color: var(--cp-text);
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s;
            opacity: 0.9;
        }
        .header-mini-btn:hover { border-color: var(--cp-accent); opacity: 1; }
        .header-mini-btn.active {
            background: rgba(71,161,255,0.15);
            border-color: var(--cp-accent);
            color: var(--cp-accent);
        }
        .header-mini-btn.primary {
            background: var(--cp-accent);
            border-color: var(--cp-accent);
            color: #fff;
        }
        .header-mini-btn.primary:hover {
            background: var(--cp-accent-hover);
            border-color: var(--cp-accent-hover);
            color: #fff;
        }

        .copilot-tabs {
            display: flex;
            padding: 0 11px;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            gap: 16px;
            flex-shrink: 0;
        }
        .tab-item {
            padding: 10px 4px;
            font-size: 13px;
            color: var(--cp-text);
            opacity: 0.6;
            cursor: pointer;
            border: none;
            background: none;
            border-bottom: 2px solid transparent;
            font-family: inherit;
            transition: all 0.2s;
        }
        .tab-item.active {
            opacity: 1;
            font-weight: 600;
            border-bottom-color: var(--cp-accent);
        }

        .copilot-main {
            flex: 1;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            position: relative;
            background: var(--cp-bg);
        }

        .chat-scroll-area {
            flex: 1;
            overflow-y: auto;
            padding: 11px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            scroll-behavior: smooth;
            overscroll-behavior: contain;
        }
        .chat-scroll-area::-webkit-scrollbar { width: 5px; }
        .chat-scroll-area::-webkit-scrollbar-thumb { background: var(--cp-border); border-radius: 10px; }

        .message {
            display: flex;
            flex-direction: column;
            max-width: 95%;
            width: fit-content;
            margin-bottom: 0px;
        }
        .message.user { align-self: flex-end; align-items: flex-end; margin-left: auto; }
        .message.assistant { align-self: flex-start; align-items: flex-start; margin-right: auto; }
        
        .bubble {
            padding: 5px 9px;
            border-radius: 16px;
            font-size: 13.5px;
            line-height: 1.6;
            word-wrap: break-word;
            white-space: pre-wrap;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            max-width: 100%;
        }
        .user .bubble { background: var(--cp-bubble-user); color: var(--cp-bubble-user-text); border-bottom-right-radius: 4px; border: none; }
        .assistant .bubble { background: var(--cp-bubble-bot); border: 1px solid var(--cp-bubble-bot-border); border-bottom-left-radius: 4px; }

        .code-block-wrapper {
            position: relative;
            margin: 12px 0;
            border-radius: 8px;
            overflow: hidden;
            background: #282c34;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .code-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: #20232a;
            font-size: 11px;
            color: #abb2bf;
            font-family: sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .copy-btn {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: #ffffff;
            padding: 4px 12px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 11px;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            gap: 6px;
            line-height: 1;
        }
        .copy-icon {
            width: 14px;
            height: 14px;
            display: block;
            flex-shrink: 0;
            stroke: #ffffff;
        }
        .copy-btn:hover {
            background: rgba(255, 255, 255, 0.2);
            border-color: rgba(255, 255, 255, 0.4);
            transform: translateY(-1px);
        }
        .copy-btn.copied {
            background: #28a745;
            border-color: #28a745;
            color: white;
        }
        .code-block {
            background: #282c34;
            color: #abb2bf;
            padding: 16px;
            font-family: "Fira Code", "Consolas", monospace;
            font-size: 13px;
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
            line-height: 1.5;
        }

        /* Catch ALL pre/code produced by markdown-it inside bubbles */
        .bubble pre {
            background: #282c34 !important;
            color: #abb2bf !important;
            font-family: "Fira Code", "Consolas", "Courier New", monospace !important;
            font-size: 13px !important;
            padding: 16px !important;
            margin: 0 !important;
            white-space: pre-wrap !important;
            word-wrap: break-word !important;
            line-height: 1.5 !important;
            overflow-x: auto;
        }
        .bubble code {
            background: #282c34 !important;
            color: #abb2bf !important;
            font-family: "Fira Code", "Consolas", "Courier New", monospace !important;
            font-size: 0.9em;
            border-radius: 4px;
            padding: 2px 5px;
        }
        /* Inline code (not inside pre) gets lighter bg */
        .bubble p code, .bubble li code {
            background: rgba(40,44,52,0.15) !important;
            color: #d63384 !important;
            padding: 1px 5px;
            border-radius: 3px;
        }

        /* Markdown-it rendered content: headings, lists, paragraphs */
        .bubble h1, .bubble h2, .bubble h3, .bubble h4 {
            color: var(--cp-accent);
            margin: 14px 0 6px 0;
            font-weight: 700;
            line-height: 1.3;
        }
        .bubble h1 { font-size: 1.4em; border-bottom: 2px solid var(--cp-border); padding-bottom: 4px; }
        .bubble h2 { font-size: 1.25em; border-bottom: 1px solid var(--cp-border); padding-bottom: 3px; }
        .bubble h3 { font-size: 1.1em; }
        .bubble h4 { font-size: 1.0em; }
        .bubble p  { margin: 6px 0; line-height: 1.6; }
        .bubble ul, .bubble ol {
            padding-left: 20px;
            margin: 6px 0;
            line-height: 1.7;
        }
        .bubble ul li { list-style: disc; }
        .bubble ol li { list-style: decimal; }
        .bubble strong { font-weight: 700; }
        .bubble em { font-style: italic; }

        .tab-content {
            display: none;
            flex: 1;
            flex-direction: column;
            overflow: hidden;
            height: 100%;
        }
        .tab-content.active {
            display: flex;
        }

        .copilot-footer {
            padding: 5px 11px;
            background: var(--cp-header-bg);
            border-top: 1px solid var(--cp-border);
            flex-shrink: 0;
        }
        .input-wrapper {
            background: var(--cp-bg);
            border: 1px solid var(--cp-border);
            border-radius: 8px;
            padding: 5px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            transition: border-color 0.2s;
            max-height: 200px;
        }
        .input-wrapper:focus-within { border-color: var(--cp-accent); }
        
        #chat-input {
            border: none;
            background: transparent;
            color: var(--cp-text);
            resize: none;
            font-family: inherit;
            font-size: 14px;
            outline: none;
            height: 15px;
            max-height: 160px;
            padding: 2px;
            overflow-y: auto;
        }
        .input-actions { display: flex; justify-content: flex-end; align-items: center; gap: 8px; }

        .icon-btn {
            width: 32px;
            height: 32px;
            border-radius: 6px;
            border: none;
            background: transparent;
            color: var(--cp-text);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s;
            font-size: 16px;
        }
        .icon-btn:hover { background: var(--cp-border); }
        
        .send-btn {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            border: none;
            background: var(--cp-accent);
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s, background 0.2s;
        }
        .send-btn:hover { background: var(--cp-accent-hover); transform: scale(1.05); }
        .send-btn:active { transform: scale(0.95); }

        /* Stop Button */
        .stop-btn {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            border: none;
            background: #e53935;
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            transition: transform 0.2s, background 0.2s;
            margin-left: 6px;
        }
        .stop-btn:hover { background: #c62828; transform: scale(1.05); }
        .stop-btn:active { transform: scale(0.95); }

        .footer-note { font-size: 10px; opacity: 0.5; text-align: center; margin-top: 8px; }

        /* Floating Toggle Button */
        #injected-copilot-toggle-btn {
            position: fixed;
            width: 50px;
            height: 50px;
            border-radius: 50%;
            border: none;
            background: #1e1e1e;
            color: white;
            font-size: 24px;
            cursor: grab;
            z-index: 10000000;
            box-shadow: 0 4px 12px var(--cp-shadow);
            transition: all 0.2s ease;
            align-items: center;
            justify-content: center;
        }
        #injected-copilot-toggle-btn:hover { transform: scale(1.1); box-shadow: 0 6px 16px var(--cp-shadow); }


        /* Typing Cursor Effect */
        .typing-cursor::after {
            content: "●";
            display: inline-block;
            margin-left: 4px;
            color: var(--cp-accent);
            animation: cpBlink 0.8s infinite;
            font-size: 12px;
            vertical-align: middle;
        }
        @keyframes cpBlink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

        /* ---- Debug Tab Styles ---- */
        .debug-toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 11px;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            flex-shrink: 0;
        }
        .debug-label { font-size: 12px; font-weight: 600; opacity: 0.7; }
        .debug-refresh-btn {
            background: var(--cp-accent);
            border: none;
            color: white;
            padding: 4px 10px;
            border-radius: 5px;
            font-size: 12px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .debug-refresh-btn:hover { background: var(--cp-accent-hover); }
        .debug-scroll-area {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .debug-scroll-area::-webkit-scrollbar { width: 5px; }
        .debug-scroll-area::-webkit-scrollbar-thumb { background: var(--cp-border); border-radius: 10px; }
        .debug-placeholder { font-size: 12px; opacity: 0.5; text-align: center; margin-top: 40px; }
        .dep-card {
            background: var(--cp-bubble-bot);
            border: 1px solid var(--cp-bubble-bot-border);
            border-radius: 8px;
            padding: 8px 10px;
            font-size: 12px;
            line-height: 1.6;
        }
        .dep-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 4px;
        }
        .dep-title-wrap {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .dep-index-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 20px;
            height: 20px;
            padding: 0 6px;
            border-radius: 999px;
            background: rgba(71,161,255,0.18);
            border: 1px solid rgba(71,161,255,0.38);
            color: #9fd0ff;
            font-size: 11px;
            font-weight: 700;
            line-height: 1;
        }
        .dep-cell-num {
            font-weight: 700;
            font-size: 13px;
            color: var(--cp-accent);
        }
        .dep-badges { display: flex; gap: 4px; flex-wrap: wrap; }
        .dep-badge {
            background: rgba(71,161,255,0.15);
            border: 1px solid rgba(71,161,255,0.3);
            color: var(--cp-accent);
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 12px;
        }
        .dep-badge.rev {
            background: rgba(255,150,71,0.15);
            border-color: rgba(255,150,71,0.3);
            color: #ff9647;
        }
        .dep-badge.muted {
            background: rgba(200,200,200,0.12);
            border-color: rgba(200,200,200,0.22);
            color: #c9c9c9;
        }
        .dep-preview {
            font-family: 'Fira Code', monospace;
            font-size: 11px;
            color: #abb2bf;
            white-space: pre-wrap;
            word-break: break-word;
            margin-top: 4px;
            opacity: 0.8;
        }
        .dep-no-deps { font-size: 11px; opacity: 0.4; font-style: italic; }

        /* ---- Header History Dropdown ---- */
        .history-dropdown {
            position: absolute;
            top: 41px;
            left: 11px;
            right: 11px;
            max-height: min(360px, 55vh);
            background: var(--cp-bg);
            border: 1px solid var(--cp-border);
            border-radius: 10px;
            display: none;
            flex-direction: column;
            overflow: hidden;
            z-index: 25;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.35);
        }
        .history-dropdown.active { display: flex; }
        .history-dropdown-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 11px;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            flex-shrink: 0;
        }
        .history-label { font-size: 12px; font-weight: 600; opacity: 0.8; }
        .history-scroll-area {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .history-scroll-area::-webkit-scrollbar { width: 5px; }
        .history-scroll-area::-webkit-scrollbar-thumb { background: var(--cp-border); border-radius: 10px; }
        .history-placeholder { font-size: 12px; opacity: 0.5; text-align: center; margin-top: 40px; }
        .history-item {
            background: var(--cp-bubble-bot);
            border: 1px solid var(--cp-bubble-bot-border);
            border-radius: 8px;
            padding: 8px 10px;
            cursor: pointer;
            text-align: left;
            color: var(--cp-text);
            transition: border-color 0.2s, background 0.2s;
        }
        .history-item:hover { border-color: var(--cp-accent); }
        .history-item.active {
            border-color: var(--cp-accent);
            background: rgba(71,161,255,0.12);
        }
        .history-title {
            font-size: 12px;
            line-height: 1.5;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .history-meta {
            margin-top: 2px;
            font-size: 10px;
            opacity: 0.65;
        }`;
    document.head.appendChild(style);

    // 2. HTML Structure
    const wrapper = document.createElement('div');
    wrapper.id = 'injected-copilot-panel-wrapper';
    wrapper.innerHTML = `<div class="copilot-container">
        <!-- Header -->
        <header class="copilot-header">
            <div class="header-left">
                <span class="header-title">My Copilot</span>
                <button id="history-toggle-btn" class="header-mini-btn" title="Open conversation history">History</button>
            </div>
            <div class="header-actions">
                <button id="history-new" class="header-mini-btn primary" title="Start new conversation">+ New</button>
            </div>
        </header>

        <div id="history-dropdown" class="history-dropdown">
            <div class="history-dropdown-header">
                <span class="history-label">Conversations</span>
            </div>
            <div id="history-list" class="history-scroll-area">
                <p class="history-placeholder">No saved conversations yet.</p>
            </div>
        </div>

        <!-- Tab Bar -->
        <nav class="copilot-tabs">
            <button class="tab-item active" data-tab="chat-tab">💬 Chat</button>
            <button class="tab-item" data-tab="debug-tab">🔗 Dependencies</button>
        </nav>

        <!-- Main Content Area -->
        <main class="copilot-main">
            <div id="chat-tab" class="tab-content active">
                <div id="chat-history" class="chat-scroll-area">
                    <div class="message assistant">
                        <div class="bubble">
                            Hello! I am your AI assistant. How can I help you today?
                        </div>
                    </div>
                </div>
            </div>

            <div id="debug-tab" class="tab-content">
                <div class="debug-toolbar">
                    <span class="debug-label">Dependency Graph</span>
                    <button id="debug-refresh" class="debug-refresh-btn" title="Refresh">↻ Refresh</button>
                </div>
                <div id="debug-content" class="debug-scroll-area">
                    <p class="debug-placeholder">Click ↻ Refresh to load the dependency graph for this notebook.</p>
                </div>
            </div>
        </main>

        <!-- Footer / Input -->
        <footer class="copilot-footer">
            <div class="input-wrapper">
                <textarea id="chat-input" rows="1" placeholder="Ask me anything..." autocomplete="off"></textarea>
                <div class="input-actions">
                    <button id="chat-send" class="send-btn" title="Send message">➔</button>
                    <button id="chat-stop" class="stop-btn" title="Stop generation" style="display:none;">⏹</button>
                </div>
            </div>
            <div class="footer-note"></div>
        </footer>
    </div>`;

    const toggleBtn = document.createElement('button');
    toggleBtn.id = 'injected-copilot-toggle-btn';
    toggleBtn.innerHTML = '🤖';
    toggleBtn.style.right = '16px';
    toggleBtn.style.bottom = '16px';

    // Create kernel state indicator
    const kernelStateIndicator = document.createElement('div');
    kernelStateIndicator.id = 'kernel-state-indicator';
    kernelStateIndicator.style.cssText = `
        position: fixed;
        top: 10px;
        left: 10px;
        padding: 8px 12px;
        background: #2b2c31;
        color: #f0f0f0;
        border: 1px solid #47a1ff;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 600;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        z-index: 10000000;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
    `;
    kernelStateIndicator.textContent = 'Kernel: ...';

    document.body.appendChild(wrapper);
    document.body.appendChild(toggleBtn);
    document.body.appendChild(kernelStateIndicator);

    console.log('[ui_injector] Kernel state indicator created and appended to page');
    console.log('[ui_injector] Indicator element:', kernelStateIndicator);

    // Listen for kernel state updates via window.postMessage from content script
    window.addEventListener('message', (event) => {
        if (event.source !== window) {
            return;
        }
        
        const msg = event.data;
        if (!msg || msg.type !== 'KERNEL_STATE_UPDATE') {
            return;
        }

        console.log('[ui_injector] KERNEL_STATE_UPDATE received:', msg.kernelScenario, 'timestamp:', msg.timestamp);
        
        if (msg.kernelScenario) {
            let displayText = 'Kernel: ...';
            let borderColor = '#888888';
            
            if (msg.kernelScenario === 'scenario_1_new_notebook_off') {
                displayText = 'Kernel: off';
                borderColor = '#FF6B6B';
            } else if (msg.kernelScenario === 'scenario_2_fresh_kernel_started') {
                displayText = 'Kernel: fresh running';
                borderColor = '#4ECDC4';
            } else if (msg.kernelScenario === 'scenario_3_reload_running_kernel') {
                displayText = 'Kernel: reloaded with already running kernel';
                borderColor = '#45B7D1';
            } else if (msg.kernelScenario === 'editor_loading') {
                displayText = 'Kernel: loading...';
                borderColor = '#FFA07A';
            }
            
            if (kernelStateIndicator) {
                kernelStateIndicator.textContent = displayText;
                kernelStateIndicator.style.borderColor = borderColor;
                console.log('[ui_injector] Indicator updated:', displayText);
            } else {
                console.error('[ui_injector] Indicator element not found!');
            }
        }
    }, false);
    
    console.log('[ui_injector] Message listener registered on window');

    // Also listen directly for runtime messages from background (more reliable)
    try {
        if (chrome && chrome.runtime && chrome.runtime.onMessage) {
            chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
                try {
                    if (!msg) return;
                    if (msg.type === 'NOTEBOOK_DATA' || msg.type === 'KERNEL_STATE_UPDATE') {
                        const scenarioMsg = msg.kernelScenario || msg.kernelScenario;
                        console.log('[ui_injector] runtime NOTEBOOK_DATA received:', scenarioMsg);

                        let displayText = 'Kernel: ...';
                        let borderColor = '#888888';
                        if (scenarioMsg === 'scenario_1_new_notebook_off') {
                            displayText = 'Kernel: off';
                            borderColor = '#FF6B6B';
                        } else if (scenarioMsg === 'scenario_2_fresh_kernel_started') {
                            displayText = 'Kernel: fresh running';
                            borderColor = '#4ECDC4';
                        } else if (scenarioMsg === 'scenario_3_reload_running_kernel') {
                            displayText = 'Kernel: reloaded with already running kernel';
                            borderColor = '#45B7D1';
                        } else if (scenarioMsg === 'editor_loading') {
                            displayText = 'Kernel: loading...';
                            borderColor = '#FFA07A';
                        }

                        if (kernelStateIndicator) {
                            kernelStateIndicator.textContent = displayText;
                            kernelStateIndicator.style.borderColor = borderColor;
                            console.log('[ui_injector] Indicator updated via runtime message:', displayText);
                        }
                        try { sendResponse && sendResponse({ ok: true }); } catch (e) {}
                    }
                } catch (e) {
                    console.error('[ui_injector] Error handling runtime message:', e?.message);
                }
            });
            console.log('[ui_injector] chrome.runtime.onMessage listener registered');
        }
    } catch (e) {
        console.warn('[ui_injector] chrome.runtime not available or error registering runtime listener', e?.message);
    }

    // 3. Logic
    let activeTab = 'chat-tab';
    const tabItems = wrapper.querySelectorAll('.tab-item');
    const panes = { 'chat-tab': wrapper.querySelector('#chat-tab'), 'debug-tab': wrapper.querySelector('#debug-tab') };

    tabItems.forEach(tab => {
        tab.addEventListener('click', () => {
            tabItems.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            Object.values(panes).forEach(p => {
                p.classList.remove('active');
            });
            activeTab = tab.dataset.tab;
            if (panes[activeTab]) {
                panes[activeTab].classList.add('active');
            }
            if (activeTab === 'debug-tab') {
                chrome.runtime.sendMessage({ type: 'GET_GRAPH', url: window.location.href });
            }
        });
    });

    const TOGGLE_POS_KEY = 'injected_copilot_toggle_pos_v1';
    let isDraggingToggle = false;
    let dragMoved = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;
    const DRAG_START_THRESHOLD = 8;
    const DOUBLE_CLICK_GRAB_DETAIL = 2;
    const CLICK_TOGGLE_DELAY_MS = 220;
    let suppressNextClick = false;
    let clickToggleTimer = null;
    let lastTouchStartAt = 0;

    function clampTogglePosition(left, top) {
        const maxLeft = Math.max(0, window.innerWidth - toggleBtn.offsetWidth);
        const maxTop = Math.max(0, window.innerHeight - toggleBtn.offsetHeight);
        return {
            left: Math.min(Math.max(0, left), maxLeft),
            top: Math.min(Math.max(0, top), maxTop)
        };
    }

    function applyTogglePosition(left, top) {
        const pos = clampTogglePosition(left, top);
        toggleBtn.style.left = `${pos.left}px`;
        toggleBtn.style.top = `${pos.top}px`;
        toggleBtn.style.right = 'auto';
        toggleBtn.style.bottom = 'auto';
    }

    function saveTogglePosition(left, top) {
        try {
            localStorage.setItem(TOGGLE_POS_KEY, JSON.stringify({ left, top }));
        } catch (_) {}
    }

    function restoreTogglePosition() {
        try {
            const raw = localStorage.getItem(TOGGLE_POS_KEY);
            if (!raw) return;
            const pos = JSON.parse(raw);
            if (typeof pos.left === 'number' && typeof pos.top === 'number') {
                applyTogglePosition(pos.left, pos.top);
            }
        } catch (_) {}
    }

    function startToggleDrag(clientX, clientY) {
        const rect = toggleBtn.getBoundingClientRect();
        isDraggingToggle = true;
        dragMoved = false;
        startX = clientX;
        startY = clientY;
        startLeft = rect.left;
        startTop = rect.top;
        
        // Prevent iframes from stealing mouse events during drag
        document.querySelectorAll('iframe').forEach(iframe => {
            iframe.dataset.cpOldPe = iframe.style.pointerEvents;
            iframe.style.pointerEvents = 'none';
        });

        applyTogglePosition(startLeft, startTop);
        toggleBtn.style.cursor = 'grabbing';
    }

    function moveToggleDrag(clientX, clientY) {
        if (!isDraggingToggle) return;
        const dx = clientX - startX;
        const dy = clientY - startY;
        if (!dragMoved && (Math.abs(dx) > DRAG_START_THRESHOLD || Math.abs(dy) > DRAG_START_THRESHOLD)) {
            dragMoved = true;
        }

        const next = clampTogglePosition(startLeft + dx, startTop + dy);
        toggleBtn.style.left = `${next.left}px`;
        toggleBtn.style.top = `${next.top}px`;
    }

    function endToggleDrag() {
        if (!isDraggingToggle) return;
        isDraggingToggle = false;
        toggleBtn.style.cursor = 'grab';

        // Restore iframe pointer events
        document.querySelectorAll('iframe').forEach(iframe => {
            iframe.style.pointerEvents = iframe.dataset.cpOldPe || '';
            delete iframe.dataset.cpOldPe;
        });

        if (dragMoved) {
            const rect = toggleBtn.getBoundingClientRect();
            const pos = clampTogglePosition(rect.left, rect.top);
            applyTogglePosition(pos.left, pos.top);
            saveTogglePosition(pos.left, pos.top);
        }
    }

    restoreTogglePosition();

    window.addEventListener('resize', () => {
        if (!toggleBtn.style.left || !toggleBtn.style.top) return;
        const rect = toggleBtn.getBoundingClientRect();
        const pos = clampTogglePosition(rect.left, rect.top);
        applyTogglePosition(pos.left, pos.top);
    });

    toggleBtn.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        if (e.detail < DOUBLE_CLICK_GRAB_DETAIL) return;
        e.preventDefault();
        suppressNextClick = true;
        startToggleDrag(e.clientX, e.clientY);
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDraggingToggle) return;
        moveToggleDrag(e.clientX, e.clientY);
    });

    document.addEventListener('mouseup', () => {
        endToggleDrag();
    });

    toggleBtn.addEventListener('touchstart', (e) => {
        if (!e.touches || e.touches.length === 0) return;
        const now = Date.now();
        if (now - lastTouchStartAt > 320) {
            lastTouchStartAt = now;
            return;
        }
        lastTouchStartAt = 0;
        const t = e.touches[0];
        suppressNextClick = true;
        // Pointer events handle the cross-iframe tracking via setPointerCapture, 
        // but we still support touchstart for the double-tap logic in some environments.
        startToggleDrag(t.clientX, t.clientY);
    }, { passive: true });

    document.addEventListener('touchmove', (e) => {
        if (!isDraggingToggle) return;
        if (!e.touches || e.touches.length === 0) return;
        const t = e.touches[0];
        moveToggleDrag(t.clientX, t.clientY);
    }, { passive: true });

    document.addEventListener('touchend', () => {
        endToggleDrag();
    });

    document.addEventListener('touchcancel', () => {
        endToggleDrag();
    });

    window.addEventListener('blur', () => {
        endToggleDrag();
    });

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            endToggleDrag();
        }
    });

    toggleBtn.addEventListener('click', (e) => {
        if (dragMoved) {
            dragMoved = false;
            return;
        }
        if (suppressNextClick || e.detail >= DOUBLE_CLICK_GRAB_DETAIL) {
            suppressNextClick = false;
            if (clickToggleTimer) {
                clearTimeout(clickToggleTimer);
                clickToggleTimer = null;
            }
            return;
        }
        if (clickToggleTimer) {
            clearTimeout(clickToggleTimer);
            clickToggleTimer = null;
        }
        clickToggleTimer = window.setTimeout(() => {
            wrapper.classList.toggle('active');
            clickToggleTimer = null;
        }, CLICK_TOGGLE_DELAY_MS);
    });

    toggleBtn.addEventListener('dblclick', (e) => {
        e.preventDefault();
        if (clickToggleTimer) {
            clearTimeout(clickToggleTimer);
            clickToggleTimer = null;
        }
    });

    const input = wrapper.querySelector('#chat-input');
    const sendBtn = wrapper.querySelector('#chat-send');
    const stopBtn = wrapper.querySelector('#chat-stop');
    const chatHistory = wrapper.querySelector('#chat-history');
    const INPUT_MAX_HEIGHT_PX = 160;
    const md = (window.markdownit) ? window.markdownit({ html: true, linkify: true, typographer: true }) : null;
    let isStreaming = false;
    let streamBuffer = '';
    let streamBubble = null;
    const cancelledSessions = new Set();

    function renderAssistant(text) {
        const raw = String(text || '');
        if (md) return md.render(raw);
        return raw.replace(/\n/g, '<br>');
    }

    function setStreamingState(active) {
        isStreaming = !!active;
        sendBtn.style.display = isStreaming ? 'none' : '';
        stopBtn.style.display = isStreaming ? '' : 'none';
    }

    function ensureStreamBubble() {
        if (streamBubble) return streamBubble;
        const div = document.createElement('div');
        div.className = 'message assistant';
        div.innerHTML = '<div class="bubble typing-cursor"></div>';
        chatHistory.appendChild(div);
        streamBubble = div.querySelector('.bubble');
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return streamBubble;
    }

    function appendStreamDelta(delta) {
        streamBuffer += String(delta || '');
        const bubble = ensureStreamBubble();
        bubble.textContent = streamBuffer;
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function finalizeStream(opts = {}) {
        if (streamBubble) {
            streamBubble.classList.remove('typing-cursor');
            const finalText = (typeof opts.text === 'string' && opts.text.length > 0)
                ? opts.text
                : streamBuffer;
            if (finalText) {
                streamBubble.innerHTML = renderAssistant(finalText);
            } else if (opts.stopped) {
                streamBubble.innerHTML = '<em>Stopped.</em>';
            } else {
                streamBubble.innerHTML = '<em>No response.</em>';
            }
        } else if (opts.error) {
            appendMessage('assistant', `Error: ${opts.error}`);
        }

        setStreamingState(false);
        streamBubble = null;
        streamBuffer = '';
    }

    function normalizeNotebookUrl(raw) {
        try {
            const u = new URL(String(raw || ''));
            const path = (u.pathname || '/').replace(/\/+$/, '') || '/';
            return `${u.protocol}//${u.host}${path}`.toLowerCase();
        } catch {
            return String(raw || '').split('#', 1)[0].split('?', 1)[0].replace(/\/+$/, '').toLowerCase();
        }
    }

    function currentNotebookUrl() {
        return normalizeNotebookUrl(window.location.href);
    }

    function sessionStorageKey() {
        return `copilot_session_${currentNotebookUrl()}`;
    }

    function createSessionId() {
        try {
            if (window.crypto?.randomUUID) return window.crypto.randomUUID();
        } catch {}
        return `session_${Date.now()}_${Math.floor(Math.random() * 100000)}`;
    }

    let volatileSessionId = createSessionId();

    function getCurrentSessionId() {
        try {
            const existing = localStorage.getItem(sessionStorageKey());
            if (existing && existing.trim()) return existing;
            if (volatileSessionId && volatileSessionId.trim()) return volatileSessionId;
        } catch {}
        if (volatileSessionId && volatileSessionId.trim()) return volatileSessionId;
        volatileSessionId = createSessionId();
        try { localStorage.setItem(sessionStorageKey(), volatileSessionId); } catch {}
        return volatileSessionId;
    }

    function setCurrentSessionId(sessionId) {
        const sid = String(sessionId || '').trim() || createSessionId();
        volatileSessionId = sid;
        try { localStorage.setItem(sessionStorageKey(), sid); } catch {}
        return sid;
    }

    function requestHistory(sessionId) {
        chrome.runtime.sendMessage({
            type: 'GET_HISTORY',
            url: currentNotebookUrl(),
            sessionId: sessionId || getCurrentSessionId()
        });
    }

    function appendMessage(role, text) {
        const div = document.createElement('div');
        div.className = `message ${role}`;
        if (role === 'assistant') {
            div.innerHTML = `<div class="bubble">${renderAssistant(text)}</div>`;
        } else {
            div.innerHTML = `<div class="bubble">${String(text || '').replace(/\n/g, '<br>')}</div>`;
        }
        chatHistory.appendChild(div);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function resetChatToDefault() {
        chatHistory.innerHTML = '<div class="message assistant"><div class="bubble">Hello! I am your AI assistant. How can I help you today?</div></div>';
    }

    function autosizeInput() {
        input.style.height = 'auto';
        const target = Math.min(input.scrollHeight, INPUT_MAX_HEIGHT_PX);
        input.style.height = `${Math.max(15, target)}px`;
        input.style.overflowY = input.scrollHeight > INPUT_MAX_HEIGHT_PX ? 'auto' : 'hidden';
    }

    // -- BUTTON LOGIC --

    // 1. History Toggle
    const historyToggle = wrapper.querySelector('#history-toggle-btn');
    const historyDropdown = wrapper.querySelector('#history-dropdown');
    historyToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        historyDropdown.classList.toggle('active');
        historyToggle.classList.toggle('active');
        if (historyDropdown.classList.contains('active')) {
            requestHistory(getCurrentSessionId());
        }
    });

    document.addEventListener('click', (e) => {
        if (!historyDropdown.contains(e.target) && e.target !== historyToggle) {
            historyDropdown.classList.remove('active');
            historyToggle.classList.remove('active');
        }
    });

    // 2. New Conversation
    const newBtn = wrapper.querySelector('#history-new');
    newBtn.onclick = () => {
        if (!confirm('Start a new chat view? Saved history for this notebook will be kept.')) return;
        setCurrentSessionId(createSessionId());
        resetChatToDefault();
        historyDropdown.classList.remove('active');
        historyToggle.classList.remove('active');
    };

    // 3. Load Initial History and Graph
    requestHistory(getCurrentSessionId());
    chrome.runtime.sendMessage({ type: 'GET_GRAPH', url: currentNotebookUrl() });

    sendBtn.onclick = () => {
        if (isStreaming) return;
        const text = input.value.trim();
        if (!text) return;
        appendMessage('user', text);
        input.value = '';
        autosizeInput();

        const sid = getCurrentSessionId();
        cancelledSessions.delete(sid);
        streamBuffer = '';
        streamBubble = null;
        setStreamingState(true);
        ensureStreamBubble();
        
        chrome.runtime.sendMessage({
            type: 'CHAT_REQUEST',
            url: currentNotebookUrl(),
            sessionId: sid,
            prompt: text
        }, (response) => {
            if (response?.error) {
                finalizeStream({ error: response.error });
            }
        });
    };

    stopBtn.onclick = () => {
        if (!isStreaming) return;
        const sid = getCurrentSessionId();
        cancelledSessions.add(sid);
        chrome.runtime.sendMessage({
            type: 'STOP_CHAT',
            url: currentNotebookUrl(),
            sessionId: sid
        });
    };

    input.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        if (e.shiftKey) return;
        e.preventDefault();
        sendBtn.click();
    });

    input.addEventListener('input', autosizeInput);
    autosizeInput();

    // Listen for AI responses from background script
    chrome.runtime.onMessage.addListener((msg) => {
        if (msg?.url && normalizeNotebookUrl(msg.url) !== currentNotebookUrl()) {
            return;
        }
        const msgSessionId = String(msg?.sessionId || '');
        if (msgSessionId && msgSessionId !== getCurrentSessionId()) {
            return;
        }

        if (msg.type === 'CHAT_STREAM') {
            if (msgSessionId && cancelledSessions.has(msgSessionId)) {
                return;
            }
            if (!isStreaming) {
                setStreamingState(true);
                ensureStreamBubble();
            }
            appendStreamDelta(msg.delta || '');
            return;
        }

        if (msg.type === 'CHAT_STREAM_END') {
            const wasCancelled = msgSessionId && cancelledSessions.has(msgSessionId);
            if (wasCancelled) {
                cancelledSessions.delete(msgSessionId);
            }
            finalizeStream({ text: msg.response || '', stopped: wasCancelled || !!msg.stopped, error: msg.error });
            return;
        }

        if (msg.type === 'CHAT_RESPONSE') {
            if (isStreaming) {
                finalizeStream({ text: msg.response || '', error: msg.error });
            } else {
                appendMessage('assistant', msg.response || msg.error || 'No response.');
            }
        }
        if (msg.type === 'HISTORY_DATA') {
            const activeSessionId = String(msg.activeSessionId || getCurrentSessionId());
            const history = Array.isArray(msg.history) ? msg.history : [];
            const sessions = Array.isArray(msg.sessions) ? msg.sessions : [];
            const list = wrapper.querySelector('#history-list');
            list.innerHTML = '';

            if (sessions.length > 0) {
                sessions.forEach((s, i) => {
                    const sid = String(s.sessionId || '');
                    if (!sid) return;
                    const item = document.createElement('div');
                    item.className = `history-item${sid === activeSessionId ? ' active' : ''}`;
                    item.innerHTML = `<div class="history-title">Conversation ${i + 1}</div><div class="history-meta">${Number(s.messageCount || 0)} messages</div>`;
                    item.addEventListener('click', () => {
                        setCurrentSessionId(sid);
                        requestHistory(sid);
                        historyDropdown.classList.remove('active');
                        historyToggle.classList.remove('active');
                    });
                    list.appendChild(item);
                });
            } else {
                list.innerHTML = '<p class="history-placeholder">No saved conversations yet.</p>';
            }

            if (activeSessionId !== getCurrentSessionId()) {
                return;
            }

            if (history.length > 0) {
                chatHistory.innerHTML = '';
                history.forEach(m => appendMessage(m.role, m.content));
            } else {
                resetChatToDefault();
            }
        }
        if (msg.type === 'GRAPH_DATA') {
            const debugContainer = wrapper.querySelector('#debug-content');
            if (!debugContainer) return;
            if (msg.error) {
                debugContainer.innerHTML = `<p class="debug-placeholder">⚠️ ${msg.error}</p>`;
                return;
            }
            if (!msg.graph || msg.graph.length === 0) {
                debugContainer.innerHTML = '<p class="debug-placeholder">No cells found yet. Cells are captured automatically while monitoring.</p>';
                return;
            }
            debugContainer.innerHTML = '';
            msg.graph.forEach((node, idx) => {
                const card = document.createElement('div');
                card.className = 'dep-card';
                const rawCellNumber = Number(node?.cell_number ?? node?.index);
                const cellNumber = Number.isFinite(rawCellNumber) && rawCellNumber > 0 ? rawCellNumber : (idx + 1);
                const deps = Array.isArray(node?.dependencies) ? node.dependencies : [];
                const revs = Array.isArray(node?.reverse_dependencies) ? node.reverse_dependencies : [];
                const depBadges = deps.map(d => `<span class="dep-badge">← Cell ${d}</span>`).join('');
                const revBadges = revs.map(r => `<span class="dep-badge rev">→ Cell ${r}</span>`).join('');
                const statusBadge = (deps.length === 0 && revs.length === 0)
                    ? '<span class="dep-badge muted">No links</span>'
                    : `<span class="dep-badge muted">${deps.length + revs.length} links</span>`;
                const noDeps = deps.length === 0 && revs.length === 0;
                const rawPreview = String(node?.input_preview || '');
                const preview = rawPreview ? rawPreview.replace(/</g,'&lt;').replace(/>/g,'&gt;') : '';
                card.innerHTML = `
                    <div class="dep-card-header">
                        <div class="dep-title-wrap">
                            <span class="dep-index-badge">${cellNumber}</span>
                            <span class="dep-cell-num">Cell ${cellNumber}</span>
                        </div>
                        <div class="dep-badges">${statusBadge}${depBadges}${revBadges}</div>
                    </div>
                    ${noDeps ? '<span class="dep-no-deps" style="font-size:10px; opacity:0.4; font-style:italic;">No dependencies</span>' : ''}
                    ${preview ? `<div class="dep-preview" style="font-size:11px; margin-top:5px; opacity:0.8; font-family: monospace; background: #202124; padding: 4px; border-radius: 4px; white-space: pre-wrap;">${preview}</div>` : ''}
                `;
                debugContainer.appendChild(card);
            });
        }
    });

    wrapper.querySelector('#debug-refresh').onclick = () => {
        chrome.runtime.sendMessage({ type: 'GET_GRAPH', url: currentNotebookUrl() });
    };
})();
