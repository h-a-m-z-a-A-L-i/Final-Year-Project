import re

with open('d:/FYP/normal-chrome/testing/extension/ui_injector_gen.js', 'r', encoding='utf-8') as f:
    code = f.read()

# 1. Remove WebSocket initialization block entirely
code = re.sub(r'let wsTimeout;.*?window\.ws = ws;\n\s*\}\n*connectWebSocket\(\);', '', code, flags=re.DOTALL)

# 2. Replace WS sends with Chrome Extension messages
code = re.sub(r'window\.ws\.send\((.*?)\);', r'chrome.runtime.sendMessage(\1);', code)
code = re.sub(r'if \(!window\.ws \|\| window\.ws\.readyState !== WebSocket\.OPEN\) .*?;', '', code)

# 3. Replace the old history fetching initialization
code = code.replace("window.ws.send(JSON.stringify({type: 'GET_HISTORY', url: getNotebookUrl()}));", 
                    "chrome.runtime.sendMessage({type: 'GET_HISTORY', url: getNotebookUrl()});")

# 4. Integrate chrome.runtime.onMessage listener for responses (this replaces ws.onmessage)
# We need to find where ws.onmessage is and replace it
ws_onmessage_pattern = r'window\.ws\.onmessage\s*=\s*(async\s*)?function\s*\(([^)]+)\)\s*{'
# Wait, let's just use string replace for the exact top line of that block if we know it:
# Actually we can just find 'window.ws.onmessage = async function(event) {'
code = code.replace("window.ws.onmessage = async function(event) {", 
                   "chrome.runtime.onMessage.addListener(async function(msg) {")
code = code.replace("window.ws.onmessage = function(event) {", 
                   "chrome.runtime.onMessage.addListener(function(msg) {")

# In the onmessage handler, data was stringified. Chrome's is already an object.
code = code.replace("const msg = JSON.parse(event.data);", "")

# 5. Fix the get_graph_data API fetch request. The debug tab refreshed by fetching HTTP.
# We will replace it with the MSG passing approach
code = re.sub(
    r"const res = await fetch\('http://localhost:8080/graph\?url=' \+ encodeURIComponent\(getNotebookUrl\(\)\)\);.*?const data = await res\.json\(\);",
    "// Data fetched via chrome msg. See onMessage listener for GRAPH_DATA.", 
    code, flags=re.DOTALL
)

# And replace `renderGraph` method to just send the message
code = re.sub(r'const renderGraph = async \(\) => {.*?};', 
'''const renderGraph = () => {
        debugContent.innerHTML = '<p class="debug-placeholder">⏳ Loading graph...</p>';
        chrome.runtime.sendMessage({ type: 'GET_GRAPH', url: getNotebookUrl() });
    };''', code, flags=re.DOTALL)

with open('d:/FYP/normal-chrome/testing/extension/ui_injector.js', 'w', encoding='utf-8') as f:
    f.write(code)
