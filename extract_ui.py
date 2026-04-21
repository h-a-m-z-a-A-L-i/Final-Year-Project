import sys, re

with open('d:/FYP/normal-chrome/gui-craft-dep.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Extract DEFAULT_PANEL_CONTENT
html_match = re.search(r'DEFAULT_PANEL_CONTENT = """(.*?)"""', text, re.DOTALL)
html = html_match.group(1).strip()

# Extract JS_INJECTION_TEMPLATE
js_match = re.search(r'JS_INJECTION_TEMPLATE = r"""(.*?)"""', text, re.DOTALL)
js = js_match.group(1).strip()
js = js.replace('{{', '{').replace('}}', '}')

out = f"""(function() {{
    const contentStr = `{html}`;
    const injectionFunc = {js};
    injectionFunc(contentStr, '*[data-testid="workspace-container"], body');
}})();
"""

with open('d:/FYP/normal-chrome/testing/extension/ui_injector_gen.js', 'w', encoding='utf-8') as f:
    f.write(out)
