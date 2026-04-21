(function() {
    const contentStr = `<div class="copilot-container">
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
    const injectionFunc = (function(content, targetSelector) {
    if (!document.body) return 'DOM_NOT_READY';

    // Cleanup
    try {
        const existing = document.getElementById('injected-copilot-panel-wrapper');
        if (existing) existing.remove();
        const existingBtn = document.getElementById('injected-copilot-toggle-btn');
        if (existingBtn) existingBtn.remove();
        const existingStyles = document.getElementById('copilot-injected-styles');
        if (existingStyles) existingStyles.remove();
    } catch (e) {}
       // Inject Styles
    const styleTag = document.createElement('style');
    styleTag.id = 'copilot-injected-styles';
    styleTag.textContent = `
        :root {
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
        }
    `;
    document.head.appendChild(styleTag);

    // ---- Function to dynamically load Markdown-it if missing ----
    const ensureMarkdownIt = () => {
        return new Promise((resolve) => {
            if (window.markdownit) return resolve(true);
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/markdown-it@14.0.0/dist/markdown-it.min.js';
            script.onload = () => resolve(true);
            script.onerror = () => resolve(false);
            document.head.appendChild(script);
        });
    };

    // Panel Wrapper
    const wrapper = document.createElement('div');
    wrapper.id = 'injected-copilot-panel-wrapper';
    wrapper.innerHTML = content;

    // Toggle Button
    const btn = document.createElement('button');
    btn.id = 'injected-copilot-toggle-btn';
    btn.innerHTML = '🤖';

    // ---- Restore saved position or fall back to default ----
    const savedPos = JSON.parse(localStorage.getItem('cp_btn_pos') || 'null');
    if (savedPos) {
        btn.style.left = savedPos.left;
        btn.style.top  = savedPos.top;
        btn.style.right = 'auto';
    } else {
        // Default: try to position near targetSelector, else bottom-right
        const targetElement = document.querySelector(targetSelector);
        if (targetElement) {
            const rect = targetElement.getBoundingClientRect();
            btn.style.left  = (rect.left - 75) + 'px';
            btn.style.top   = (rect.top + (rect.height / 2) - 25) + 'px';
            btn.style.right = 'auto';
        } else {
            btn.style.right = '20px';
            btn.style.top   = '20px';
        }
    }

    // ---- Draggable logic (pointer events) ----
    let dragStartX, dragStartY, startLeft, startTop;
    let dragging = false;
    const DRAG_THRESHOLD = 5; // px — less than this = click, more = drag

    btn.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return; // left-click / touch only
        e.preventDefault();
        btn.setPointerCapture(e.pointerId);
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        const cs = getComputedStyle(btn);
        startLeft = parseInt(cs.left, 10) || (window.innerWidth - 70);
        startTop  = parseInt(cs.top,  10) || 20;
        dragging  = false;
        btn.style.transition = 'none';
        btn.style.cursor = 'grabbing';
    });

    btn.addEventListener('pointermove', (e) => {
        if (!btn.hasPointerCapture(e.pointerId)) return;
        const dx = e.clientX - dragStartX;
        const dy = e.clientY - dragStartY;
        if (!dragging && Math.sqrt(dx*dx + dy*dy) > DRAG_THRESHOLD) {
            dragging = true;
        }
        if (dragging) {
            let newLeft = startLeft + dx;
            let newTop  = startTop  + dy;
            // Clamp inside viewport
            newLeft = Math.max(0, Math.min(window.innerWidth  - 54, newLeft));
            newTop  = Math.max(0, Math.min(window.innerHeight - 54, newTop));
            btn.style.left  = newLeft + 'px';
            btn.style.top   = newTop  + 'px';
            btn.style.right = 'auto';
        }
    });

    btn.addEventListener('pointerup', (e) => {
        btn.style.transition = 'box-shadow 0.2s ease, background 0.2s ease';
        btn.style.cursor = 'grab';
        if (dragging) {
            // Persist position across reloads
            localStorage.setItem('cp_btn_pos', JSON.stringify({
                left: btn.style.left,
                top:  btn.style.top
            }));
        } else {
            // Short tap / click → toggle panel
            togglePanel();
        }
        dragging = false;
    });

    // ---- positionButton kept for resize handling ----
    function positionButton(btn, targetSelector) {
        // Only reposition if user hasn't manually dragged the button
        if (localStorage.getItem('cp_btn_pos')) return;
        const targetElement = document.querySelector(targetSelector);
        if (targetElement) {
            const rect = targetElement.getBoundingClientRect();
            btn.style.right = 'auto';
            btn.style.left  = (rect.left - 75) + 'px';
            btn.style.top   = (rect.top + (rect.height / 2) - 25) + 'px';
        } else {
            btn.style.right = '20px';
            btn.style.top   = '20px';
        }
    }

    // State Management
    let isOpen = false;
    const togglePanel = () => {
        isOpen = !isOpen;
        wrapper.classList.toggle('active', isOpen);
        btn.innerHTML = isOpen ? '✕' : '🤖';
        btn.style.background = isOpen ? '#1e1e1e' : '#1e1e1e';
        if (isOpen) {
            document.getElementById('chat-input').focus();
        } else {
            const dropdown = wrapper.querySelector('#history-dropdown');
            const historyBtn = wrapper.querySelector('#history-toggle-btn');
            if (dropdown) dropdown.classList.remove('active');
            if (historyBtn) historyBtn.classList.remove('active');
        }
    };

    document.body.appendChild(wrapper);
    document.body.appendChild(btn);

    // ---- Tab switching ----
    wrapper.querySelectorAll('.tab-item').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.tab;
            wrapper.querySelectorAll('.tab-item').forEach(b => b.classList.remove('active'));
            wrapper.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            wrapper.querySelector('#' + target).classList.add('active');

            if (target === 'debug-tab') renderGraph();
        });
    });

    // ---- Debug: Render dependency graph ----
    const debugContent = wrapper.querySelector('#debug-content');
    const historyToggleBtn = wrapper.querySelector('#history-toggle-btn');
    const historyDropdown = wrapper.querySelector('#history-dropdown');
    const historyList = wrapper.querySelector('#history-list');
    const historyNewBtn = wrapper.querySelector('#history-new');

    const getNotebookUrl = () => {
        const rawHref = String(window.location.href || '').trim();
        if (!rawHref) return 'unknown_notebook';
        try {
            const u = new URL(rawHref, window.location.origin);
            const parts = (u.pathname || '/').split('/').filter(Boolean);
            if (parts.length >= 3 && parts[0].toLowerCase() === 'code') {
                return `${u.origin.toLowerCase()}/code/${parts[1]}/${parts[2]}`;
            }
            const cleanPath = (u.pathname || '/').replace(/\/+$/, '') || '/';
            return `${u.origin.toLowerCase()}${cleanPath}`;
        } catch (_e) {
            return rawHref.split('#')[0].split('?')[0];
        }
    };

    const renderGraph = async () => {
        debugContent.innerHTML = '<p class="debug-placeholder">⏳ Loading graph...</p>';
        try {
            const res = await fetch('http://localhost:8080/graph?url=' + encodeURIComponent(getNotebookUrl()));
            const data = await res.json();
            if (data.error) {
                debugContent.innerHTML = `<p class="debug-placeholder">⚠️ ${data.error}</p>`;
                return;
            }
            if (!data.cells || data.cells.length === 0) {
                debugContent.innerHTML = '<p class="debug-placeholder">No cells found yet. Cells are captured automatically while monitoring.</p>';
                return;
            }
            debugContent.innerHTML = '';
            data.cells.forEach(cell => {
                const card = document.createElement('div');
                card.className = 'dep-card';
                const depBadges = (cell.dependencies || []).map(d => `<span class="dep-badge">← Cell ${d}</span>`).join('');
                const revBadges = (cell.reverse_dependencies || []).map(r => `<span class="dep-badge rev">→ Cell ${r}</span>`).join('');
                const noDeps = !depBadges && !revBadges;
                const preview = cell.input_preview ? cell.input_preview.replace(/</g,'&lt;').replace(/>/g,'&gt;') : '';
                card.innerHTML = `
                    <div class="dep-card-header">
                        <span class="dep-cell-num">Cell ${cell.cell_number}</span>
                        <div class="dep-badges">${depBadges}${revBadges}</div>
                    </div>
                    ${noDeps ? '<span class="dep-no-deps">No dependencies</span>' : ''}
                    ${preview ? `<div class="dep-preview">${preview}</div>` : ''}
                `;
                debugContent.appendChild(card);
            });
        } catch(e) {
            debugContent.innerHTML = `<p class="debug-placeholder">⚠️ Could not reach backend: ${e.message}</p>`;
        }
    };

    wrapper.querySelector('#debug-refresh').addEventListener('click', renderGraph);

    const textarea = wrapper.querySelector('#chat-input');
    textarea.oninput = function() {
        this.style.height = 'auto';
        const newHeight = Math.min(this.scrollHeight, 160);
        this.style.height = newHeight + 'px';
        this.style.overflowY = this.scrollHeight > 160 ? 'auto' : 'hidden';
    };

    // ============================================================
    // MARKDOWN-IT BASED FORMATTER: format_llm_response
    // ============================================================
    // Enhance code blocks rendered by markdown-it with Copy buttons
    const enhanceCodeBlocks = (container) => {
        container.querySelectorAll('pre code').forEach((codeEl) => {
            const pre = codeEl.parentNode;
            if (pre.parentNode.classList.contains('code-block-wrapper')) return; // already enhanced

            const wrapper = document.createElement('div');
            wrapper.className = 'code-block-wrapper';
            wrapper.style.margin = '15px 0';

            // Detect language from class e.g. "language-python"
            const langClass = codeEl.className.match(/language-(\w+)/);
            const lang = langClass ? langClass[1] : 'code';

            const header = document.createElement('div');
            header.className = 'code-header';
            header.innerHTML = `<span>${lang}</span>
                <button class="copy-btn">
                    <svg class="copy-icon" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                    </svg>
                    <span class="btn-text">Copy</span>
                </button>`;

            const copyBtn = header.querySelector('.copy-btn');
            const btnText = header.querySelector('.btn-text');
            const icon = header.querySelector('svg');

            copyBtn.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(codeEl.innerText);
                    copyBtn.classList.add('copied');
                    btnText.innerText = 'Copied!';
                    const orig = icon.innerHTML;
                    icon.innerHTML = '<polyline points="20 6 9 17 4 12"></polyline>';
                    setTimeout(() => {
                        copyBtn.classList.remove('copied');
                        btnText.innerText = 'Copy';
                        icon.innerHTML = orig;
                    }, 2000);
                } catch(e) { console.error('Copy failed', e); }
            });

            pre.parentNode.insertBefore(wrapper, pre);
            pre.className = 'code-block';
            pre.style.cssText = 'padding:14px; margin:0; overflow-x:auto;';
            wrapper.appendChild(header);
            wrapper.appendChild(pre);
        });
    };

    // Main formatter: runs markdown-it on full accumulated text, then enhances code blocks
    const format_llm_response = (text) => {
        if (!text) return "";
        // Normalize line endings
        const normalized = text.replace(/\r\n/g, '\n');

        // Render markdown to HTML via markdown-it (safe check)
        let html = normalized;
        if (window.markdownit) {
            try {
                html = window.markdownit({ html: false, linkify: true, typographer: true }).render(normalized);
            } catch(e) { console.error("MD render error", e); }
        }

        // Parse into a temp DOM element so we can enhance code blocks
        const temp = document.createElement('div');
        temp.innerHTML = html;
        enhanceCodeBlocks(temp);

        return temp.innerHTML;
    };

    const chatHistory = wrapper.querySelector('#chat-history');
    const appendMessage = async (text, role) => {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${role}`;
        msgDiv.innerHTML = `<div class="bubble">${text}</div>`;
        chatHistory.appendChild(msgDiv);
        
        // Render it with formatting
        const bubble = msgDiv.querySelector('.bubble');
        await ensureMarkdownIt();
        bubble.innerHTML = format_llm_response(text);
        
        chatHistory.scrollTop = chatHistory.scrollHeight;
    };

    const renderDefaultGreeting = async () => {
        chatHistory.innerHTML = '';
        await appendMessage('Hello! I am your AI assistant. How can I help you today?', 'assistant');
    };

    const renderChatMessages = async (messages) => {
        const list = Array.isArray(messages) ? messages : [];
        if (!list.length) {
            await renderDefaultGreeting();
            return;
        }

        chatHistory.innerHTML = '';
        for (const msg of list) {
            const role = (msg && msg.role) === 'user' ? 'user' : 'assistant';
            const text = String((msg && msg.content) || '').trim();
            if (text) await appendMessage(text, role);
        }
    };

    let shouldAutoScroll = true;
    chatHistory.onscroll = () => {
        const threshold = 60; // Slightly larger for better 're-catch'
        const distanceToBottom = chatHistory.scrollHeight - chatHistory.clientHeight - chatHistory.scrollTop;
        shouldAutoScroll = distanceToBottom < threshold;
    };

    const scrollToBottom = (instant = false) => {
        if (shouldAutoScroll) {
            chatHistory.scrollTo({
                top: chatHistory.scrollHeight,
                behavior: instant ? 'auto' : 'smooth'
            });
        }
    };

    // ---- Stop / AbortController state ----
    let currentAbortController = null;

    const sendBtn  = wrapper.querySelector('#chat-send');
    const stopBtn  = wrapper.querySelector('#chat-stop');

    const tabStorageKey = 'cp_tab_id';
    const tabId = (() => {
        let existing = sessionStorage.getItem(tabStorageKey);
        if (existing) return existing;
        const generated = (window.crypto && typeof window.crypto.randomUUID === 'function')
            ? window.crypto.randomUUID()
            : ('tab_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2));
        sessionStorage.setItem(tabStorageKey, generated);
        return generated;
    })();

    const hashString = (raw) => {
        let hash = 0;
        for (let i = 0; i < raw.length; i++) {
            hash = ((hash << 5) - hash) + raw.charCodeAt(i);
            hash |= 0;
        }
        return Math.abs(hash);
    };

    const getNotebookSessionKey = () => {
        const notebookUrl = getNotebookUrl() || 'unknown_notebook';
        return 'cp_session_id_' + hashString(notebookUrl) + '_' + tabId;
    };

    const getCurrentMode = () => {
        const activeTab = wrapper.querySelector('.tab-item.active');
        if (activeTab && activeTab.dataset && activeTab.dataset.tab === 'debug-tab') {
            return 'dependency';
        }
        return 'simple';
    };

    const setStreaming = (active) => {
        sendBtn.disabled = active;
        sendBtn.style.opacity = active ? '0.5' : '1';
        stopBtn.style.display  = active ? 'flex' : 'none';
    };

    const closeHistoryDropdown = () => {
        if (historyDropdown) historyDropdown.classList.remove('active');
        if (historyToggleBtn) historyToggleBtn.classList.remove('active');
    };

    const toggleHistoryDropdown = async (event) => {
        if (event) event.stopPropagation();
        if (!historyDropdown) return;

        const shouldOpen = !historyDropdown.classList.contains('active');
        closeHistoryDropdown();

        if (shouldOpen) {
            await loadConversationList();
            historyDropdown.classList.add('active');
            if (historyToggleBtn) historyToggleBtn.classList.add('active');
        }
    };

    if (historyToggleBtn) {
        historyToggleBtn.addEventListener('click', (event) => {
            toggleHistoryDropdown(event).catch((err) => {
                console.warn('Could not toggle history dropdown:', err);
            });
        });
    }

    if (historyDropdown) {
        historyDropdown.addEventListener('click', (event) => event.stopPropagation());
    }

    document.addEventListener('click', (event) => {
        if (!historyDropdown || !historyDropdown.classList.contains('active')) return;
        const target = event.target;
        if (historyDropdown.contains(target)) return;
        if (historyToggleBtn && historyToggleBtn.contains(target)) return;
        closeHistoryDropdown();
    });

    const formatHistoryTime = (value) => {
        if (!value) return 'No messages yet';
        try {
            const d = new Date(String(value).replace(' ', 'T'));
            if (Number.isNaN(d.getTime())) return String(value);
            return d.toLocaleString();
        } catch (_e) {
            return String(value);
        }
    };

    const renderConversationList = (conversations, activeSessionId) => {
        const list = Array.isArray(conversations) ? conversations : [];
        historyList.innerHTML = '';

        if (!list.length) {
            historyList.innerHTML = '<p class="history-placeholder">No saved conversations yet.</p>';
            return;
        }

        list.forEach((conv) => {
            const sid = String((conv && conv.session_id) || '').trim();
            if (!sid) return;

            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'history-item' + (sid === activeSessionId ? ' active' : '');
            item.dataset.sessionId = sid;

            const title = document.createElement('div');
            title.className = 'history-title';
            title.textContent = String((conv && conv.title) || 'New conversation');

            const meta = document.createElement('div');
            meta.className = 'history-meta';
            const count = Number((conv && conv.message_count) || 0);
            meta.textContent = `${count} messages • ${formatHistoryTime(conv && conv.updated_at)}`;

            item.appendChild(title);
            item.appendChild(meta);
            item.addEventListener('click', () => switchConversation(sid));
            historyList.appendChild(item);
        });
    };

    const loadConversationList = async () => {
        try {
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const params = new URLSearchParams({
                url: notebookUrl,
                tab_id: tabId
            });
            const response = await fetch('http://localhost:8080/history/list?' + params.toString());
            if (!response.ok) return;

            const payload = await response.json();
            const stored = sessionStorage.getItem(notebookSessionKey);
            const active = String(stored || payload.active_session_id || '').trim() || null;
            if (!stored && active) sessionStorage.setItem(notebookSessionKey, active);

            renderConversationList(payload.conversations, active);
        } catch (err) {
            console.warn('Could not load conversation list:', err);
        }
    };

    const switchConversation = async (targetSessionId) => {
        if (!targetSessionId) return;

        try {
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const params = new URLSearchParams({
                url: notebookUrl,
                tab_id: tabId,
                session_id: targetSessionId
            });
            const response = await fetch('http://localhost:8080/history?' + params.toString());
            if (!response.ok) return;

            const payload = await response.json();
            const sid = String((payload && payload.session_id) || '').trim();
            if (sid) sessionStorage.setItem(notebookSessionKey, sid);

            const messages = Array.isArray(payload && payload.messages) ? payload.messages : [];
            await renderChatMessages(messages);
            await loadConversationList();

            const chatTabBtn = wrapper.querySelector('.tab-item[data-tab="chat-tab"]');
            if (chatTabBtn) chatTabBtn.click();
            closeHistoryDropdown();
        } catch (err) {
            console.warn('Could not switch conversation:', err);
        }
    };

    const startNewConversation = async () => {
        if (currentAbortController) currentAbortController.abort();

        try {
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const params = new URLSearchParams({
                url: notebookUrl,
                tab_id: tabId
            });
            const response = await fetch('http://localhost:8080/history/new?' + params.toString(), { method: 'POST' });
            if (!response.ok) return;

            const payload = await response.json();
            const sid = String((payload && payload.session_id) || '').trim();
            if (sid) sessionStorage.setItem(notebookSessionKey, sid);

            await renderDefaultGreeting();
            await loadConversationList();

            const chatTabBtn = wrapper.querySelector('.tab-item[data-tab="chat-tab"]');
            if (chatTabBtn) chatTabBtn.click();
            closeHistoryDropdown();
        } catch (err) {
            console.warn('Could not start a new conversation:', err);
        }
    };

    if (historyNewBtn) historyNewBtn.addEventListener('click', startNewConversation);

    const loadPersistedHistory = async () => {
        try {
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const sid = sessionStorage.getItem(notebookSessionKey) || null;
            const params = new URLSearchParams({
                url: notebookUrl,
                tab_id: tabId
            });
            if (sid) params.set('session_id', sid);

            const response = await fetch('http://localhost:8080/history?' + params.toString());
            if (!response.ok) return;

            const payload = await response.json();
            const session = String((payload && payload.session_id) || '').trim();
            if (session) sessionStorage.setItem(notebookSessionKey, session);

            const messages = Array.isArray(payload && payload.messages) ? payload.messages : [];
            await renderChatMessages(messages);
        } catch (err) {
            console.warn('Could not load local chat history:', err);
            await renderDefaultGreeting();
        }
    };

    const historyLoadPromise = (async () => {
        await loadPersistedHistory();
        await loadConversationList();
    })();

    const sendPrompt = async (prompt, forcedMode = null) => {
        await historyLoadPromise;
        if (!prompt || currentAbortController) return;

        const selectedMode = forcedMode || getCurrentMode();

        appendMessage(prompt, 'user');

        shouldAutoScroll = true;
        chatHistory.scrollTo({ top: chatHistory.scrollHeight, behavior: 'smooth' });

        const botMsgDiv = document.createElement('div');
        botMsgDiv.className = 'message assistant';
        botMsgDiv.innerHTML = '<div class="bubble typing-cursor">Thinking...</div>';
        chatHistory.appendChild(botMsgDiv);

        const bubble = botMsgDiv.querySelector('.bubble');
        let fullText = "";

        // Create AbortController for this request
        currentAbortController = new AbortController();
        const { signal } = currentAbortController;

        setStreaming(true);

        const notebookSessionKey = getNotebookSessionKey();
        const notebookUrl = getNotebookUrl();
        const body = {
            prompt,
            mode: selectedMode,
            debug: selectedMode === 'dependency',
            session_id: sessionStorage.getItem(notebookSessionKey) || null,
            tab_id: tabId,
            notebook_url: notebookUrl
        };

        try {
            await ensureMarkdownIt(); // Ensure markdown-it is loaded before processing LLM response
            const response = await fetch('http://localhost:8080/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal
            });

            const sid = response.headers.get('X-Session-ID');
            if (sid) sessionStorage.setItem(notebookSessionKey, sid);

            if (!response.body) throw new Error("No response body");

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            bubble.classList.add('typing-cursor');

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true });
                if (fullText === "") bubble.innerHTML = "";
                fullText += chunk;
                bubble.innerHTML = format_llm_response(fullText);
                scrollToBottom(true);
            }
            bubble.classList.remove('typing-cursor');

        } catch (err) {
            if (err.name === 'AbortError') {
                // User stopped — clean up silently, keep whatever was rendered
                bubble.classList.remove('typing-cursor');
                if (fullText === "") bubble.innerHTML = '<em style="color:#aaa;">Stopped.</em>';
            } else {
                bubble.innerHTML = '<span style="color:red">Error: ' + err.message + '</span>';
                bubble.classList.remove('typing-cursor');
            }
        } finally {
            currentAbortController = null;
            setStreaming(false);
            loadConversationList().catch(() => {});
        }
    };

    const sendMessage = async () => {
        const prompt = textarea.value.trim();
        if (!prompt) return;

        textarea.value = '';
        textarea.style.height = 'auto';
        await sendPrompt(prompt);
    };

    // Stop button handler
    stopBtn.onclick = () => {
        if (currentAbortController) {
            currentAbortController.abort();
        }
        // Also notify the backend to stop generating
        const notebookSessionKey = getNotebookSessionKey();
        const sid = sessionStorage.getItem(notebookSessionKey);
        if (sid) {
            fetch('http://localhost:8080/stop?session_id=' + encodeURIComponent(sid), {
                method: 'POST'
            }).catch(() => {});
        }
    };

    window.addEventListener('message', (event) => {
        const payload = event.data;
        if (!payload || payload.type !== 'cp_explain_error') return;

        const rawError = String(payload.errorText || '').trim();
        if (!rawError) return;

        const parsedCell = parseInt(payload.cellNumber, 10);
        const cellLabel = Number.isNaN(parsedCell) ? 'a notebook cell' : `cell ${parsedCell}`;
        const explainPrompt = `Explain this ${cellLabel} error, why it happened, and how to fix it.\n\n${rawError}`;

        if (!isOpen) togglePanel();
        sendPrompt(explainPrompt, 'explain_error');
    });

    sendBtn.onclick  = sendMessage;
    textarea.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } };

    // Header Actions
    // Removed #header-toggle-btn listener as requested

    // Responsive Handling
    const handleResize = () => {
        positionButton(btn, targetSelector);
        if (window.innerWidth < 600) {
            wrapper.style.width = '100vw';
        } else {
            wrapper.style.width = '395px';
        }
    };
    window.addEventListener('resize', handleResize);
    handleResize();


    return 'SUCCESS';
})({content_quoted}, {selector_quoted});
    injectionFunc(contentStr, '*[data-testid="workspace-container"], body');
})();
