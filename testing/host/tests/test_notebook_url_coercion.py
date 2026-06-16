"""Host-side coercion of invalid notebook tool URLs to session URL."""

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_batch_executor import _parse_tool_calls
from testing.host.agentic_text_tools import inject_tool_defaults
from testing.host.bot_tool_utils import (
    coerce_notebook_tool_session,
    coerce_notebook_tool_url,
    is_prompt_placeholder_notebook_url,
    is_valid_notebook_edit_url,
    notebook_urls_match,
)

SESSION_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
SESSION_TAB_ID = 112732919
BAD_DATASET_URL = "/kaggle/input/datasets/codekey/zameen-com2026-16-5/zameen_master_dataset.csv"
PLACEHOLDER_URL = "https://www.kaggle.com/code/owner/slug/edit"
OTHER_NOTEBOOK_URL = "https://www.kaggle.com/code/alice/other/edit"


def test_is_valid_notebook_edit_url_rejects_dataset_paths():
    assert is_valid_notebook_edit_url(BAD_DATASET_URL) is False
    assert is_valid_notebook_edit_url("") is False
    assert is_valid_notebook_edit_url("kaggle/input/foo.csv") is False


def test_is_valid_notebook_edit_url_accepts_edit_urls():
    assert is_valid_notebook_edit_url(SESSION_URL) is True
    assert is_valid_notebook_edit_url("https://example.com/edit") is True


def test_is_valid_notebook_edit_url_rejects_prompt_placeholder():
    assert is_prompt_placeholder_notebook_url(PLACEHOLDER_URL) is True
    assert is_valid_notebook_edit_url(PLACEHOLDER_URL) is False


def test_notebook_urls_match_session_variants():
    assert notebook_urls_match(SESSION_URL, SESSION_URL) is True
    assert notebook_urls_match(
        "https://kaggle.com/code/codekey/testing-ol/edit",
        SESSION_URL,
    ) is True
    assert notebook_urls_match(OTHER_NOTEBOOK_URL, SESSION_URL) is False


def test_coerce_notebook_tool_url_replaces_bad_url():
    args = {"url": BAD_DATASET_URL, "cell_index": 2}
    out = coerce_notebook_tool_url(args, session_url=SESSION_URL, tool_name="delete_by_index")
    assert out["url"] == SESSION_URL
    assert out["cell_index"] == 2


def test_coerce_notebook_tool_url_replaces_mismatched_and_placeholder_urls():
    for bad_url in (OTHER_NOTEBOOK_URL, PLACEHOLDER_URL):
        args = {"url": bad_url, "cell_index": 2}
        out = coerce_notebook_tool_url(args, session_url=SESSION_URL, tool_name="delete_by_index")
        assert out["url"] == SESSION_URL
        assert out["cell_index"] == 2


def test_coerce_notebook_tool_url_keeps_matching_session_url():
    args = {"url": SESSION_URL, "cell_index": 1}
    out = coerce_notebook_tool_url(args, session_url=SESSION_URL, tool_name="notebook_list_cells")
    assert out["url"] == SESSION_URL


def test_parse_tool_calls_coerces_placeholder_url():
    raw = [
        {
            "id": "call_del",
            "type": "function",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 7, "url": PLACEHOLDER_URL}),
            },
        },
        {
            "id": "call_list",
            "type": "function",
            "function": {
                "name": "notebook_list_cells",
                "arguments": json.dumps({"url": PLACEHOLDER_URL}),
            },
        },
    ]
    parsed = _parse_tool_calls(raw, url=SESSION_URL, tab_id=None)
    assert len(parsed) == 2
    assert all(c.args["url"] == SESSION_URL for c in parsed)


def test_parse_tool_calls_coerces_dataset_url():
    raw = [
        {
            "id": "call_del",
            "type": "function",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 7, "url": BAD_DATASET_URL}),
            },
        },
        {
            "id": "call_list",
            "type": "function",
            "function": {
                "name": "notebook_list_cells",
                "arguments": json.dumps({"url": BAD_DATASET_URL}),
            },
        },
    ]
    parsed = _parse_tool_calls(raw, url=SESSION_URL, tab_id=None)
    assert len(parsed) == 2
    assert all(c.args["url"] == SESSION_URL for c in parsed)


def test_inject_tool_defaults_coerces_dataset_url():
    calls = [
        {
            "id": "1",
            "type": "function",
            "function": {
                "name": "notebook_list_cells",
                "arguments": json.dumps({"url": BAD_DATASET_URL}),
            },
        }
    ]
    out = inject_tool_defaults(calls, url=SESSION_URL, tab_id=7)
    args = json.loads(out[0]["function"]["arguments"])
    assert args["url"] == SESSION_URL
    assert args["tab_id"] == 7
    assert args["tabId"] == 7


def test_inject_tool_defaults_injects_missing_tab_id():
    calls = [
        {
            "id": "1",
            "type": "function",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"url": SESSION_URL, "cell_index": 2}),
            },
        }
    ]
    out = inject_tool_defaults(calls, url=SESSION_URL, tab_id=SESSION_TAB_ID)
    args = json.loads(out[0]["function"]["arguments"])
    assert args["tab_id"] == SESSION_TAB_ID
    assert args["tabId"] == SESSION_TAB_ID


def test_coerce_notebook_tool_session_overrides_conflicting_tab_id():
    args = {"url": SESSION_URL, "cell_index": 2, "tab_id": 99999, "tabId": 99999}
    out = coerce_notebook_tool_session(
        args,
        session_url=SESSION_URL,
        session_tab_id=SESSION_TAB_ID,
        tool_name="delete_by_index",
    )
    assert out["tab_id"] == SESSION_TAB_ID
    assert out["tabId"] == SESSION_TAB_ID
    assert out["url"] == SESSION_URL


def test_coerce_notebook_tool_session_url_conflict_uses_session_url():
    args = {"url": OTHER_NOTEBOOK_URL, "cell_index": 2, "tab_id": SESSION_TAB_ID}
    out = coerce_notebook_tool_session(
        args,
        session_url=SESSION_URL,
        session_tab_id=SESSION_TAB_ID,
        tool_name="delete_by_index",
    )
    assert out["url"] == SESSION_URL
    assert out["tab_id"] == SESSION_TAB_ID


def test_parse_tool_calls_injects_tab_id():
    raw = [
        {
            "id": "call_del",
            "type": "function",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 7, "url": PLACEHOLDER_URL}),
            },
        },
    ]
    parsed = _parse_tool_calls(raw, url=SESSION_URL, tab_id=SESSION_TAB_ID)
    assert len(parsed) == 1
    assert parsed[0].args["url"] == SESSION_URL
    assert parsed[0].args["tab_id"] == SESSION_TAB_ID
