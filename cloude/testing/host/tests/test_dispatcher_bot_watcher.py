import importlib
import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import config
from testing.host import persistence_helpers as ph
from testing.host import dispatcher


def test_handle_bot_command_insert_cell_updates_persistence(tmp_path, monkeypatch):
    temp_scraped = tmp_path / 'notebooks'
    monkeypatch.setattr(config, 'SCRAPED_DIR', temp_scraped)

    url = 'https://www.kaggle.com/code/codekey/qwen2-5-coder-7b-instruct/edit'
    filename = ph.get_safe_filename(url)
    persistent_dir = temp_scraped / 'persistent'
    persistent_dir.mkdir(parents=True, exist_ok=True)
    ppath = persistent_dir / filename
    ph._atomic_write_json(ppath, {'cells': [{'index': 1, 'input': 'a'}]})

    fake_event = {
        'ok': True,
        'requestId': 'req-1',
        'result': {'ok': True, 'phase': 'inserted'},
    }

    with patch.object(dispatcher, 'execute_bot_command_sync', return_value=fake_event):
        event = dispatcher._handle_bot_command({
            'action': 'insert_cell',
            'requestId': 'req-1',
            'index': 1,
            'direction': 'below',
            'url': url,
        })

    assert event['requestId'] == 'req-1'
    assert event['ok'] is True
