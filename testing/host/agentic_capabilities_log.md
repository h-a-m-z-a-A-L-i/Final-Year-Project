# Agentic Capabilities Log

This file tracks notebook-operation capabilities added for the agentic workflow.

## 2026-05-10

- `click_cell_by_index`: Clicks a notebook cell by zero-based index using `data-windowed-list-index`, searches across frames and shadow DOM, scrolls the target into view, and returns a click result or error.
  - Implemented in `testing/extension/background.js`.
  - Message type: `CLICK_CELL_BY_INDEX`.
  - Result types: `CLICK_CELL_RESULT` and `CLICK_CELL_ERROR`.
- `click_selector`: Clicks a CSS selector in the top-level Kaggle/JupyterLab chrome, useful for toolbar buttons outside the notebook iframe.
  - Implemented in `testing/extension/kernel_state_listener.js` and routed by `testing/extension/background.js`.
  - Message type: `CLICK_SELECTOR`.
  - Result types: `CLICK_SELECTOR_RESULT` and `CLICK_SELECTOR_ERROR`.
- `cell_ops_tester`: Small Playwright REPL/one-shot tester for notebook cell operations.
  - Implemented in `testing/host/cell_ops_tester.py`.
  - Commands: `list`, `click <index>`, `inspect <index>`, `url <new_url>`, `shot [path]`, `quit`.
