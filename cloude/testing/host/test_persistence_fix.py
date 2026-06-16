"""Quick smoke test: verify persistent JSON is created from NOTEBOOK_DATA."""
import sys, json, threading
sys.path.insert(0, '.')

from notebook_data_handler import handle_notebook_data
from persistence_helpers import get_safe_filename
from pathlib import Path

SCRAPED_DIR = Path('data/notebooks')
sent_msgs = []

def fake_send(msg):
    sent_msgs.append(msg)

def fake_log(msg):
    print(f'  LOG: {msg}')

ctx = {
    'dep_manager': type('DM', (), {'get_builder': lambda self, u: None})(),
    'send_msg': fake_send,
    'log': fake_log,
    'bot_state': {'tabId': None, 'url': None},
    'bot_state_lock': threading.Lock(),
}

test_url = 'https://www.kaggle.com/code/test/persistence-check'
msg = {
    'type': 'NOTEBOOK_DATA',
    'tabUrl': test_url,
    'tabId': 999,
    'title': 'Test Notebook',
    'kernelStatus': 'running',
    'kernelScenario': 'scenario_2_fresh_kernel_started',
    'kernelState': {},
    'cells': [
        {'type': 'code', 'index': 1, 'source': 'print("hello")', 'output': 'hello',
         'execution_order': 1, 'execution_title': 'Execution #1', 'execution_status': 'executed'},
        {'type': 'code', 'index': 2, 'source': 'x = 42', 'output': '',
         'execution_order': 2, 'execution_title': 'Execution #2', 'execution_status': 'executed'},
    ],
}

print("--- Running NOTEBOOK_DATA handler ---")
handle_notebook_data(ctx, msg)

fn = get_safe_filename(test_url)
live_path = SCRAPED_DIR / 'live' / fn
persistent_path = SCRAPED_DIR / 'persistent' / fn

print(f'Live exists: {live_path.exists()}')
print(f'Persistent exists: {persistent_path.exists()}')

if persistent_path.exists():
    data = json.loads(persistent_path.read_text())
    print(f'Persistent cells count: {len(data.get("cells", []))}')
    for c in data.get('cells', []):
        print(f'  Cell {c.get("index")}: order={c.get("execution_order")} title={c.get("execution_title")}')
    print("SUCCESS: Persistent JSON created correctly!")
else:
    print('FAILURE: persistent JSON was NOT created!')

print(f'Response: {sent_msgs}')

# --- Test empty notebook guard ---
print("\n--- Testing empty notebook guard ---")
empty_msg = {
    'type': 'NOTEBOOK_DATA',
    'tabUrl': test_url,
    'tabId': 999,
    'title': 'Test Notebook',
    'kernelStatus': 'running',
    'kernelScenario': 'scenario_2_fresh_kernel_started',
    'kernelState': {},
    'cells': [],
}

# Record current persistent content
before = persistent_path.read_text() if persistent_path.exists() else None

sent_msgs.clear()
handle_notebook_data(ctx, empty_msg)

after = persistent_path.read_text() if persistent_path.exists() else None

if before == after:
    print("SUCCESS: Empty notebook did NOT overwrite persistent file!")
else:
    print("FAILURE: Empty notebook overwrote persistent file!")

print(f'Response: {sent_msgs}')

# Cleanup test file
try:
    persistent_path.unlink(missing_ok=True)
    live_path.unlink(missing_ok=True)
    legacy_path = SCRAPED_DIR / fn
    legacy_path.unlink(missing_ok=True)
except Exception:
    pass
