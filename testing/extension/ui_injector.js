(function() {
    if (document.getElementById('injected-copilot-panel-wrapper')) return;

    // 1. Inject Styles
    const style = document.createElement('style');
    style.id = 'copilot-styles';
    style.textContent = `:root {
            --cp-bg: #16171b;
            --cp-surface: #1e1f24;
            --cp-surface-elevated: #25262d;
            --cp-text: #ececf1;
            --cp-text-muted: #9ca3af;
            --cp-accent: #5b9cff;
            --cp-accent-hover: #7eb1ff;
            --cp-bubble-user: linear-gradient(135deg, #3d7dd8 0%, #5b9cff 100%);
            --cp-bubble-user-text: #ffffff;
            --cp-bubble-bot: #23242a;
            --cp-bubble-bot-border: #35363f;
            --cp-header-bg: rgba(22, 23, 27, 0.92);
            --cp-border: #2e3038;
            --cp-shadow: rgba(0, 0, 0, 0.45);
            --cp-code-bg: #12141a;
            --cp-radius: 12px;
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
            padding: 14px 12px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            scroll-behavior: smooth;
            overscroll-behavior: contain;
        }
        .chat-scroll-area::-webkit-scrollbar { width: 5px; }
        .chat-scroll-area::-webkit-scrollbar-thumb { background: var(--cp-border); border-radius: 10px; }

        .message {
            display: flex;
            flex-direction: column;
            max-width: 100%;
            width: 100%;
            gap: 8px;
        }
        .message.user {
            align-self: flex-end;
            align-items: flex-end;
            max-width: 92%;
            width: fit-content;
            margin-left: auto;
        }
        .message.assistant {
            align-self: stretch;
            align-items: stretch;
            width: 100%;
        }
        .message.assistant.streaming .code-snippets-stack { display: none; }

        .bubble {
            padding: 10px 12px;
            border-radius: var(--cp-radius);
            font-size: 13.5px;
            line-height: 1.65;
            word-wrap: break-word;
            box-shadow: 0 2px 8px rgba(0,0,0,0.12);
            max-width: 100%;
        }
        .user .bubble {
            background: var(--cp-bubble-user);
            color: var(--cp-bubble-user-text);
            border-bottom-right-radius: 4px;
            border: none;
            white-space: pre-wrap;
        }
        .assistant .bubble.prose-bubble {
            background: var(--cp-bubble-bot);
            border: 1px solid var(--cp-bubble-bot-border);
            border-bottom-left-radius: 4px;
            white-space: normal;
        }

        .code-snippets-stack {
            display: flex;
            flex-direction: column;
            gap: 10px;
            width: 100%;
        }
        .code-snippet-card {
            width: 100%;
            border-radius: var(--cp-radius);
            overflow: hidden;
            border: 1px solid var(--cp-border);
            box-shadow: 0 4px 14px rgba(0,0,0,0.2);
        }
        .code-block-wrapper {
            position: relative;
            margin: 0;
            border-radius: 0;
            overflow: hidden;
            background: var(--cp-code-bg);
            border: none;
        }
        .code-header {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 8px;
            padding: 8px 12px;
            background: #1a1d24;
            font-size: 11px;
            color: var(--cp-text-muted);
            font-family: inherit;
            letter-spacing: 0.04em;
        }
        .code-header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
        }
        .code-insert-row {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 6px;
        }
        .code-insert-label {
            color: var(--cp-text-muted);
            white-space: nowrap;
        }
        .code-insert-index {
            width: 56px;
            padding: 4px 6px;
            border-radius: 6px;
            border: 1px solid var(--cp-border);
            background: var(--cp-surface-elevated);
            color: var(--cp-text);
            font-size: 11px;
        }
        .code-insert-index:focus {
            outline: none;
            border-color: var(--cp-accent);
        }
        .insert-cell-btn {
            background: rgba(91, 156, 255, 0.2);
            border: 1px solid rgba(91, 156, 255, 0.45);
            color: #dceaff;
            padding: 4px 10px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 11px;
            white-space: nowrap;
        }
        .insert-cell-btn:hover:not(:disabled) {
            background: rgba(91, 156, 255, 0.35);
        }
        .insert-cell-btn:disabled {
            opacity: 0.55;
            cursor: wait;
        }
        .insert-cell-btn.success {
            background: rgba(40, 167, 69, 0.35);
            border-color: #28a745;
        }
        .code-insert-status {
            font-size: 10px;
            color: var(--cp-text-muted);
            flex: 1;
            min-width: 80px;
        }
        .code-lang-label {
            font-weight: 600;
            text-transform: uppercase;
            color: var(--cp-accent);
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
            background: var(--cp-code-bg);
            color: #c5d4e8;
            padding: 14px 14px 16px;
            font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
            font-size: 12.5px;
            margin: 0;
            white-space: pre;
            overflow-x: auto;
            line-height: 1.55;
            tab-size: 4;
        }
        .code-block code {
            font-family: inherit;
            background: transparent;
            padding: 0;
            color: inherit;
        }

        /* Prose-only: inline code stays subtle; fenced blocks are extracted to code-snippets-stack */
        .prose-bubble pre { display: none; }
        .prose-bubble p code, .prose-bubble li code {
            background: rgba(91, 156, 255, 0.12);
            color: #b8d4ff;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.88em;
            font-family: "Cascadia Code", "Consolas", monospace;
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
            padding: 10px 12px 12px;
            background: var(--cp-header-bg);
            border-top: 1px solid var(--cp-border);
            flex-shrink: 0;
            backdrop-filter: blur(8px);
        }
        .input-wrapper {
            background: var(--cp-surface-elevated);
            border: 1px solid var(--cp-border);
            border-radius: 10px;
            padding: 8px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: border-color 0.2s, box-shadow 0.2s;
            max-height: 200px;
        }
        .input-wrapper:focus-within {
            border-color: var(--cp-accent);
            box-shadow: 0 0 0 2px rgba(91, 156, 255, 0.15);
        }
        
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


        /* Typing cursor (streaming prose only) */
        .typing-cursor::after {
            content: "";
            display: inline-block;
            width: 2px;
            height: 1em;
            margin-left: 3px;
            background: var(--cp-accent);
            vertical-align: text-bottom;
            animation: cpBlink 1s step-end infinite;
        }
        @keyframes cpBlink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

        .stream-plain {
            white-space: pre-wrap;
            word-break: break-word;
            color: var(--cp-text);
        }

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
            display: flex;
            align-items: stretch;
            gap: 0;
            background: var(--cp-bubble-bot);
            border: 1px solid var(--cp-bubble-bot-border);
            border-radius: 8px;
            padding: 0;
            text-align: left;
            color: var(--cp-text);
            transition: border-color 0.2s, background 0.2s;
            overflow: hidden;
        }
        .history-item:hover { border-color: var(--cp-accent); }
        .history-item.active {
            border-color: var(--cp-accent);
            background: rgba(71,161,255,0.12);
        }
        .history-item.removing {
            opacity: 0;
            transform: translateX(8px);
            transition: opacity 0.15s ease, transform 0.15s ease;
            pointer-events: none;
        }
        .history-delete-btn {
            flex-shrink: 0;
            width: 32px;
            border: none;
            border-left: 1px solid var(--cp-bubble-bot-border);
            background: transparent;
            color: var(--cp-text);
            cursor: pointer;
            opacity: 0.5;
            padding: 0;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .history-delete-btn svg {
            width: 14px;
            height: 14px;
            display: block;
            pointer-events: none;
        }
        .history-delete-btn:hover {
            opacity: 1;
            background: rgba(220, 70, 70, 0.15);
            color: #e85d5d;
        }
        .history-item-body {
            flex: 1;
            min-width: 0;
            padding: 8px 10px;
            cursor: pointer;
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
        .mode-row {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 6px;
            padding: 0 2px;
        }
        .mode-row label {
            font-size: 11px;
            opacity: 0.75;
            white-space: nowrap;
        }
        .mode-select {
            flex: 1;
            font-size: 11px;
            padding: 4px 6px;
            border-radius: 6px;
            border: 1px solid var(--cp-border);
            background: var(--cp-surface);
            color: inherit;
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
                            Hello! I'm your notebook copilot. Use <strong>Ask</strong> for questions, errors, and explanations — <strong>Code</strong> to generate or edit cells.
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
                    <p class="debug-placeholder">Open this tab to load dependencies. Cell numbers match notebook indices (often starting at 0).</p>
                </div>
            </div>
        </main>

        <!-- Footer / Input -->
        <footer class="copilot-footer">
            <div class="mode-row">
                <label for="chat-mode">Mode</label>
                <select id="chat-mode" class="mode-select" title="Ask = explain/debug/placement; Code = generate cells">
                    <option value="ask" selected>Ask</option>
                    <option value="code">Code</option>
                </select>
            </div>
            <div class="input-wrapper">
                <textarea id="chat-input" rows="1" placeholder="Ask about a cell, error, or notebook change..." autocomplete="off"></textarea>
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

    document.body.appendChild(wrapper);
    document.body.appendChild(toggleBtn);

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
    const modeSelect = wrapper.querySelector('#chat-mode');
    const sendBtn = wrapper.querySelector('#chat-send');
    const stopBtn = wrapper.querySelector('#chat-stop');
    const chatHistory = wrapper.querySelector('#chat-history');
    const INPUT_MAX_HEIGHT_PX = 160;
    const md = (window.markdownit) ? window.markdownit({ html: true, linkify: true, typographer: true }) : null;
    let isStreaming = false;
    let streamBuffer = '';
    let streamMessageEl = null;
    let streamPlainEl = null;
    const cancelledSessions = new Set();

    function escapeHtml(str) {
        return String(str || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /** Split assistant text into prose vs fenced code (```lang ... ```). */
    function parseFencedCodeBlocks(raw) {
        const segments = [];
        const s = String(raw || '');
        const re = /```([\w.-]*)\r?\n?([\s\S]*?)```/g;
        let last = 0;
        let m;
        while ((m = re.exec(s))) {
            if (m.index > last) {
                const chunk = s.slice(last, m.index);
                if (chunk.trim()) segments.push({ type: 'text', content: chunk });
            }
            segments.push({
                type: 'code',
                lang: (m[1] || 'code').trim() || 'code',
                content: m[2].replace(/\s+$/, ''),
            });
            last = m.index + m[0].length;
        }
        if (last < s.length) {
            const tail = s.slice(last);
            if (tail.trim()) segments.push({ type: 'text', content: tail });
        }
        if (!segments.length && s.trim()) segments.push({ type: 'text', content: s });
        return segments;
    }

    function renderProseHtml(text) {
        const t = String(text || '').trim();
        if (!t) return '';
        if (md) return md.render(t);
        return escapeHtml(t).replace(/\n/g, '<br>');
    }

    function createCodeSnippetCard(lang, code) {
        const card = document.createElement('div');
        card.className = 'code-snippet-card';
        const block = document.createElement('div');
        block.className = 'code-block-wrapper';
        const header = document.createElement('div');
        header.className = 'code-header';

        const headerTop = document.createElement('div');
        headerTop.className = 'code-header-top';
        const label = document.createElement('span');
        label.className = 'code-lang-label';
        label.textContent = lang || 'code';
        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'copy-btn';
        copyBtn.textContent = 'Copy';
        copyBtn.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(code);
                copyBtn.textContent = 'Copied';
                copyBtn.classList.add('copied');
                setTimeout(() => {
                    copyBtn.textContent = 'Copy';
                    copyBtn.classList.remove('copied');
                }, 2000);
            } catch {
                copyBtn.textContent = 'Failed';
            }
        });
        headerTop.appendChild(label);
        headerTop.appendChild(copyBtn);

        const insertRow = document.createElement('div');
        insertRow.className = 'code-insert-row';
        const insertLabel = document.createElement('span');
        insertLabel.className = 'code-insert-label';
        insertLabel.textContent = 'Create new cell at';
        const indexInput = document.createElement('input');
        indexInput.type = 'number';
        indexInput.min = '0';
        indexInput.step = '1';
        indexInput.className = 'code-insert-index';
        indexInput.placeholder = '0';
        indexInput.title = 'Anchor cell index — a new code cell is inserted below it';
        const insertBtn = document.createElement('button');
        insertBtn.type = 'button';
        insertBtn.className = 'insert-cell-btn';
        insertBtn.textContent = 'Insert below';
        const statusEl = document.createElement('span');
        statusEl.className = 'code-insert-status';

        insertBtn.addEventListener('click', () => {
            const anchor = Number.parseInt(indexInput.value, 10);
            if (!Number.isInteger(anchor) || anchor < 1) {
                statusEl.textContent = 'Enter a valid cell index.';
                return;
            }
            insertBtn.disabled = true;
            statusEl.textContent = 'Inserting…';
            chrome.runtime.sendMessage({
                type: 'INSERT_CODE_CELL',
                url: currentNotebookUrl(),
                index: anchor,
                content: code,
            }, (resp) => {
                insertBtn.disabled = false;
                if (chrome.runtime.lastError) {
                    statusEl.textContent = chrome.runtime.lastError.message || 'Extension error';
                    return;
                }
                if (resp?.ok) {
                    const newIdx = resp?.result?.newCellIndex;
                    statusEl.textContent = newIdx != null
                        ? `New cell at index ${newIdx} (below ${anchor})`
                        : `Inserted below cell ${anchor}`;
                    insertBtn.classList.add('success');
                    setTimeout(() => insertBtn.classList.remove('success'), 2500);
                } else {
                    statusEl.textContent = String(resp?.error || 'Insert failed');
                }
            });
        });

        insertRow.appendChild(insertLabel);
        insertRow.appendChild(indexInput);
        insertRow.appendChild(insertBtn);
        insertRow.appendChild(statusEl);

        header.appendChild(headerTop);
        header.appendChild(insertRow);

        const pre = document.createElement('pre');
        pre.className = 'code-block';
        const codeEl = document.createElement('code');
        codeEl.textContent = code;
        pre.appendChild(codeEl);
        block.appendChild(header);
        block.appendChild(pre);
        card.appendChild(block);
        return card;
    }

    /** Prose bubble + separate code cards (not blended into markdown body). */
    function mountAssistantContent(container, raw) {
        const segments = parseFencedCodeBlocks(raw);
        container.innerHTML = '';
        container.className = 'message assistant';

        const textParts = [];
        const codeParts = [];
        for (const seg of segments) {
            if (seg.type === 'code') codeParts.push(seg);
            else textParts.push(seg.content);
        }

        const proseJoined = textParts.join('\n\n').trim();
        if (proseJoined) {
            const bubble = document.createElement('div');
            bubble.className = 'bubble prose-bubble';
            bubble.innerHTML = renderProseHtml(proseJoined);
            container.appendChild(bubble);
        }

        if (codeParts.length) {
            const stack = document.createElement('div');
            stack.className = 'code-snippets-stack';
            for (const seg of codeParts) {
                stack.appendChild(createCodeSnippetCard(seg.lang, seg.content));
            }
            container.appendChild(stack);
        }

        if (!proseJoined && !codeParts.length) {
            const bubble = document.createElement('div');
            bubble.className = 'bubble prose-bubble';
            bubble.innerHTML = '<em>No response.</em>';
            container.appendChild(bubble);
        }
    }

    function setStreamingState(active) {
        isStreaming = !!active;
        sendBtn.style.display = isStreaming ? 'none' : '';
        stopBtn.style.display = isStreaming ? '' : 'none';
    }

    function ensureStreamMessage() {
        if (streamMessageEl) return streamMessageEl;
        const div = document.createElement('div');
        div.className = 'message assistant streaming';
        const bubble = document.createElement('div');
        bubble.className = 'bubble prose-bubble typing-cursor';
        const plain = document.createElement('span');
        plain.className = 'stream-plain';
        bubble.appendChild(plain);
        div.appendChild(bubble);
        chatHistory.appendChild(div);
        streamMessageEl = div;
        streamPlainEl = plain;
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return streamMessageEl;
    }

    function appendStreamDelta(delta) {
        streamBuffer += String(delta || '');
        ensureStreamMessage();
        if (streamPlainEl) streamPlainEl.textContent = streamBuffer;
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function finalizeStream(opts = {}) {
        if (streamMessageEl) {
            const finalText = (typeof opts.text === 'string' && opts.text.length > 0)
                ? opts.text
                : streamBuffer;
            if (finalText) {
                mountAssistantContent(streamMessageEl, finalText);
            } else if (opts.stopped) {
                streamMessageEl.innerHTML = '';
                const bubble = document.createElement('div');
                bubble.className = 'bubble prose-bubble';
                bubble.innerHTML = '<em>Stopped.</em>';
                streamMessageEl.appendChild(bubble);
            } else {
                streamMessageEl.innerHTML = '';
                const bubble = document.createElement('div');
                bubble.className = 'bubble prose-bubble';
                bubble.innerHTML = '<em>No response.</em>';
                streamMessageEl.appendChild(bubble);
            }
            streamMessageEl.classList.remove('streaming');
        } else if (opts.error) {
            appendMessage('assistant', `Error: ${opts.error}`);
        } else if (opts.stopped) {
            appendMessage('assistant', 'Stopped.');
        } else {
            appendMessage('assistant', 'No response.');
        }

        setStreamingState(false);
        streamMessageEl = null;
        streamPlainEl = null;
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

    let notebookKey = '';
    let notebookId = null;
    let lastObservedNotebookUrl = '';

    function currentNotebookKey() {
        return notebookKey || currentNotebookUrl();
    }

    function applyNotebookIdentity(identity, options = {}) {
        const url = normalizeNotebookUrl(identity?.url || window.location.href);
        const nextKey = String(identity?.notebookKey || url).trim() || url;
        const nextId = identity?.notebookId ?? null;
        const keyChanged = nextKey !== currentNotebookKey();
        const urlChanged = url !== lastObservedNotebookUrl;

        notebookKey = nextKey;
        notebookId = nextId;
        lastObservedNotebookUrl = url;

        if (options.reloadHistory && (keyChanged || urlChanged)) {
            resetChatToDefault();
            requestHistory(getCurrentSessionId());
        }
    }

    function refreshNotebookIdentity(done) {
        if (!chrome?.runtime?.sendMessage) {
            applyNotebookIdentity({ url: currentNotebookUrl(), notebookKey: currentNotebookUrl() }, { reloadHistory: true });
            if (done) done();
            return;
        }
        chrome.runtime.sendMessage({ type: 'GET_TAB_NOTEBOOK_URL' }, (response) => {
            applyNotebookIdentity(response, { reloadHistory: true });
            if (done) done();
        });
    }

    function watchNotebookUrlChanges() {
        const url = currentNotebookUrl();
        if (!url) return;
        if (url === lastObservedNotebookUrl && notebookKey) return;
        refreshNotebookIdentity();
    }

    function notebookScopeMatches(msg) {
        const msgKey = String(msg?.notebookKey || normalizeNotebookUrl(msg?.url || '')).trim();
        if (!msgKey) return true;
        return msgKey === currentNotebookKey();
    }

    function sessionStorageKey() {
        return `copilot_session_${currentNotebookKey()}`;
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
            notebookId,
            notebookKey: currentNotebookKey(),
            sessionId: sessionId || getCurrentSessionId()
        });
    }

    const deletedSessionIds = new Set();

    const historyTrashIcon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
        + '<path stroke-linecap="round" stroke-linejoin="round" d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-7 0l.7 11.2c.1 1.1 1 2 2.1 2h5.4c1.1 0 2-.9 2.1-2L17 7"/>'
        + '<path stroke-linecap="round" d="M10 11v5M14 11v5"/>'
        + '</svg>'
    );

    function getHistoryListEl() {
        return wrapper.querySelector('#history-list');
    }

    function removeConversationFromList(sessionId) {
        const sid = String(sessionId || '').trim();
        const list = getHistoryListEl();
        if (!list || !sid) return;
        const safeSid = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(sid) : sid.replace(/"/g, '\\"');
        const item = list.querySelector(`.history-item[data-session-id="${safeSid}"]`);
        if (item) item.remove();
        if (!list.querySelector('.history-item')) {
            list.innerHTML = '<p class="history-placeholder">No saved conversations yet.</p>';
        }
    }

    function buildHistoryItem(session, activeSessionId, index) {
        const sid = String(session.sessionId || '').trim();
        if (!sid) return null;
        const title = String(session.title || '').trim() || `Conversation ${index + 1}`;

        const item = document.createElement('div');
        item.className = `history-item${sid === activeSessionId ? ' active' : ''}`;
        item.dataset.sessionId = sid;

        const body = document.createElement('div');
        body.className = 'history-item-body';
        body.title = title;
        body.innerHTML = `<div class="history-title">${escapeHtml(title)}</div><div class="history-meta">${Number(session.messageCount || 0)} messages</div>`;
        body.addEventListener('click', () => {
            setCurrentSessionId(sid);
            requestHistory(sid);
            historyDropdown.classList.remove('active');
            historyToggle.classList.remove('active');
        });

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'history-delete-btn';
        deleteBtn.title = 'Delete conversation';
        deleteBtn.setAttribute('aria-label', `Delete conversation: ${title}`);
        deleteBtn.innerHTML = historyTrashIcon;
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            deleteConversation(sid);
        });

        item.appendChild(body);
        item.appendChild(deleteBtn);
        return item;
    }

    function syncDeletedSessionIds(sessions) {
        const serverIds = new Set(
            (Array.isArray(sessions) ? sessions : [])
                .map((s) => String(s.sessionId || '').trim())
                .filter(Boolean)
        );
        deletedSessionIds.forEach((sid) => {
            if (!serverIds.has(sid)) deletedSessionIds.delete(sid);
        });
    }

    function renderConversationList(sessions, activeSessionId) {
        const list = getHistoryListEl();
        if (!list) return;
        list.innerHTML = '';
        const rows = (Array.isArray(sessions) ? sessions : []).filter(
            (s) => !deletedSessionIds.has(String(s.sessionId || '').trim())
        );
        if (rows.length === 0) {
            list.innerHTML = '<p class="history-placeholder">No saved conversations yet.</p>';
            return;
        }
        rows.forEach((s, i) => {
            const item = buildHistoryItem(s, activeSessionId, i);
            if (item) list.appendChild(item);
        });
    }

    function deleteConversation(sessionId) {
        const sid = String(sessionId || '').trim();
        if (!sid) return;
        if (!confirm('Delete this conversation permanently?')) return;

        deletedSessionIds.add(sid);
        removeConversationFromList(sid);
        historyDropdown.classList.add('active');
        historyToggle.classList.add('active');
        if (sid === getCurrentSessionId()) {
            setCurrentSessionId(createSessionId());
            resetChatToDefault();
        }

        chrome.runtime.sendMessage({
            type: 'CLEAR_HISTORY',
            url: currentNotebookUrl(),
            notebookId,
            notebookKey: currentNotebookKey(),
            sessionId: sid
        }, () => {
            requestHistory(getCurrentSessionId());
        });
    }

    function appendMessage(role, text) {
        const div = document.createElement('div');
        div.className = `message ${role}`;
        if (role === 'assistant') {
            mountAssistantContent(div, text);
        } else {
            div.innerHTML = `<div class="bubble">${escapeHtml(String(text || '')).replace(/\n/g, '<br>')}</div>`;
        }
        chatHistory.appendChild(div);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function resetChatToDefault() {
        chatHistory.innerHTML = '<div class="message assistant"><div class="bubble prose-bubble">Hello! I\'m your notebook copilot. Use <strong>Ask</strong> for questions and debugging, or <strong>Code</strong> to generate notebook cells.</div></div>';
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
    refreshNotebookIdentity();
    chrome.runtime.sendMessage({ type: 'GET_GRAPH', url: currentNotebookUrl() });
    setInterval(watchNotebookUrlChanges, 2000);
    window.addEventListener('popstate', watchNotebookUrlChanges);

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
        streamMessageEl = null;
        streamPlainEl = null;
        setStreamingState(true);
        ensureStreamMessage();
        
        chrome.runtime.sendMessage({
            type: 'CHAT_REQUEST',
            url: currentNotebookUrl(),
            notebookId,
            notebookKey: currentNotebookKey(),
            sessionId: sid,
            prompt: text,
            mode: modeSelect ? String(modeSelect.value || 'ask') : 'ask',
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
        const partial = streamBuffer;
        chrome.runtime.sendMessage({
            type: 'STOP_CHAT',
            url: currentNotebookUrl(),
            notebookId,
            notebookKey: currentNotebookKey(),
            sessionId: sid
        });
        finalizeStream({ text: partial, stopped: true });
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
        if (msg.type === 'NOTEBOOK_IDENTITY_UPDATED') {
            applyNotebookIdentity(msg, { reloadHistory: true });
            return;
        }

        if (!notebookScopeMatches(msg)) {
            return;
        }
        const msgSessionId = String(msg?.sessionId || '');
        const sessionScopedTypes = new Set(['CHAT_STREAM', 'CHAT_STREAM_END', 'CHAT_RESPONSE']);
        if (msgSessionId && sessionScopedTypes.has(msg.type) && msgSessionId !== getCurrentSessionId()) {
            return;
        }

        if (msg.type === 'CHAT_STREAM') {
            if (msgSessionId && cancelledSessions.has(msgSessionId)) {
                return;
            }
            if (!isStreaming) {
                setStreamingState(true);
                ensureStreamMessage();
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
        if (msg.type === 'HISTORY_CLEARED') {
            const deletedSid = String(msg.sessionId || '').trim();
            if (deletedSid) deletedSessionIds.add(deletedSid);
            removeConversationFromList(deletedSid);
            if (deletedSid && deletedSid === getCurrentSessionId()) {
                setCurrentSessionId(createSessionId());
                resetChatToDefault();
            }
            requestHistory(getCurrentSessionId());
            return;
        }

        if (msg.type === 'HISTORY_DATA') {
            if (msg.notebookKey) {
                notebookKey = String(msg.notebookKey).trim() || notebookKey;
            }
            const activeSessionId = String(msg.activeSessionId || getCurrentSessionId());
            const history = Array.isArray(msg.history) ? msg.history : [];
            const sessions = Array.isArray(msg.sessions) ? msg.sessions : [];
            syncDeletedSessionIds(sessions);
            renderConversationList(sessions, activeSessionId);

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
                const cellNumber = Number.isFinite(rawCellNumber) ? rawCellNumber : idx;
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
