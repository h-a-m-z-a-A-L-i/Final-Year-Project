import re

def main():
    # 1. Read gui-craft-dep.py to get the exact HTML and CSS strings
    print("Reading gui-craft-dep.py...")
    try:
        with open('d:/FYP/normal-chrome/gui-craft-dep.py', 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"Error reading gui file: {e}")
        return

    # Extract DEFAULT_PANEL_CONTENT (HTML)
    print("Extracting HTML...")
    html_match = re.search(r'DEFAULT_PANEL_CONTENT\s*=\s*\"\"\"(.*?)\"\"\"', text, re.DOTALL)
    if not html_match:
        print("Could not find DEFAULT_PANEL_CONTENT")
        return
    html = html_match.group(1).strip()

    # Extract CSS from JS_INJECTION_TEMPLATE
    print("Extracting CSS...")
    css_match = re.search(r'styleTag\.textContent\s*=\s*`(.*?)`\s*;', text, re.DOTALL)
    if not css_match:
        print("Could not find styleTag.textContent")
        return
    css_content = css_match.group(1).strip()
    # Unescape any curly braces added by Python's format string
    css_content = css_content.replace('{{', '{').replace('}}', '}')

    # 2. Read the current ui_injector.js
    print("Reading ui_injector.js...")
    try:
        with open('d:/FYP/normal-chrome/testing/extension/ui_injector.js', 'r', encoding='utf-8') as f:
            ui_js = f.read()
    except Exception as e:
        print(f"Error reading ui js file: {e}")
        return

    # 3. Replace the CSS block with the one from gui-craft-dep.py
    print("Replacing CSS...")
    ui_js = re.sub(
        r"style\.textContent\s*=\s*`.*?`;", 
        f"style.textContent = `{css_content}`;", 
        ui_js, 
        flags=re.DOTALL
    )

    # 4. Replace the HTML block inside wrapper.innerHTML with DEFAULT_PANEL_CONTENT
    print("Replacing HTML...")
    ui_js = re.sub(
        r"wrapper\.innerHTML\s*=\s*`.*?`;", 
        f"wrapper.innerHTML = `{html}`;", 
        ui_js, 
        flags=re.DOTALL
    )

    # 5. Fix JS IDs that might have changed to match the old design logic
    # The old input was #chat-input, the new one was too, but let's check send button:
    # gui: #chat-send and #chat-stop
    # new: #send-btn
    ui_js = ui_js.replace("wrapper.querySelector('#send-btn')", "wrapper.querySelector('#chat-send')")
    
    # Refresh btn
    # gui: #debug-refresh
    # new: #refresh-deps
    ui_js = ui_js.replace("wrapper.querySelector('#refresh-deps')", "wrapper.querySelector('#debug-refresh')")

    # Tabs
    # gui: [data-tab="chat-tab"], [data-tab="debug-tab"]
    # new: [data-tab="chat-pane"], [data-tab="debug-pane"]
    ui_js = ui_js.replace("chat-pane", "chat-tab")
    ui_js = ui_js.replace("debug-pane", "debug-tab")
    
    # Also adjust the way panes are discovered (since gui has #chat-tab and #debug-tab identical to data attributes)
    ui_js = ui_js.replace("const panes = { 'chat-tab': wrapper.querySelector('#chat-tab'), 'debug-tab': wrapper.querySelector('#debug-tab') };", 
                          "const panes = { 'chat-tab': wrapper.querySelector('#chat-tab'), 'debug-tab': wrapper.querySelector('#debug-tab') };")

    # Chat history div
    # gui: #chat-history
    ui_js = ui_js.replace("const chatHistory = wrapper.querySelector('#chat-tab');", "const chatHistory = wrapper.querySelector('#chat-history');")

    # The header close button doesn't exist natively in gui-craft-dep, it toggles by button click.
    # In my ui_js I had `wrapper.querySelector('header button').onclick = () => wrapper.classList.remove('active');`
    # Let's gracefully remove that or let it stay, it might break if no button is found natively.
    ui_js = ui_js.replace("wrapper.querySelector('header button').onclick", "try{wrapper.querySelector('header button').onclick")
    ui_js = ui_js.replace("wrapper.classList.remove('active');\n    const input", "wrapper.classList.remove('active');}catch(e){}\n    const input")

    # Write back
    print("Writing to file...")
    with open('d:/FYP/normal-chrome/testing/extension/ui_injector.js', 'w', encoding='utf-8') as f:
        f.write(ui_js)
    
    print("Done")

if __name__ == '__main__':
    main()
