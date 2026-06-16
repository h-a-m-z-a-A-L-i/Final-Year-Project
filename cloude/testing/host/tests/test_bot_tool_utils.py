import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import bot_tool_utils as btu
from testing.host import config
from testing.host import persistence_helpers as ph


def test_reject_tab_id_as_cell_index():
    idx, err = btu.normalize_dom_cell_index(2015855861)
    assert idx is None
    assert "tab id" in err.lower()


def test_accept_zero_dom_index():
    idx, err = btu.normalize_dom_cell_index(0)
    assert err is None
    assert idx == 0


def test_reject_negative_dom_index():
    idx, err = btu.normalize_dom_cell_index(-1)
    assert idx is None
    assert ">= 0" in err


def test_accept_valid_dom_index():
    idx, err = btu.normalize_dom_cell_index(3)
    assert err is None
    assert idx == 3


def test_out_of_range_against_snapshot(tmp_path, monkeypatch):
    url = "https://example.com/notebook/edit"
    scraped = tmp_path / "notebooks"
    live = scraped / "live"
    live.mkdir(parents=True)
    ph._atomic_write_json(
        live / ph.get_safe_filename(url),
        {"cells": [{"index": 1, "type": "code", "input": "x=1", "output": ""}]},
    )
    monkeypatch.setattr(config, "SCRAPED_DIR", scraped)

    idx, err = btu.normalize_dom_cell_index(1, url=url)
    assert idx is None
    assert "out of range" in err.lower()
    assert "0..0" in err


def test_index_basis_app_converts_to_dom():
    idx, err = btu.normalize_dom_cell_index_from_args({"cell_index": 2, "index_basis": "app"})
    assert err is None
    assert idx == 1


def test_retriable_errors():
    assert btu.is_retriable_browser_error("Cell not found in this frame tree.")
    assert btu.is_retriable_browser_error("timeout waiting for extension")
    assert not btu.is_retriable_browser_error("cell_index must be >= 0")


def test_normalize_click_cell_args_success():
    cmd, err = btu.normalize_click_cell_args(
        {"url": "https://example.com/edit", "cell_index": 1, "run_cell": True}
    )
    assert err is None
    assert cmd["cellIndex"] == 0
    assert cmd["dom_index"] == 0
    assert cmd["app_index"] == 1
    assert cmd["cell_index"] == 1
    assert cmd["runCell"] is True


def test_normalize_click_cell_default_app_basis():
    cmd, err = btu.normalize_click_cell_args(
        {"url": "https://example.com/edit", "cell_index": 5}
    )
    assert err is None
    assert cmd["dom_index"] == 4
    assert cmd["app_index"] == 5


def test_normalize_click_cell_args_missing_url():
    cmd, err = btu.normalize_click_cell_args({"cell_index": 0})
    assert cmd is None
    assert err["ok"] is False


def test_normalize_run_cell_args_success():
    cmd, err = btu.normalize_run_cell_args(
        {"url": "https://example.com/edit", "cell_index": 3}
    )
    assert err is None
    assert cmd["action"] == "run_cell"
    assert cmd["cellIndex"] == 2
    assert cmd["dom_index"] == 2
    assert cmd["app_index"] == 3
    assert cmd["runCell"] is True


def test_normalize_edit_and_run_args_success():
    cmd, err = btu.normalize_edit_and_run_args(
        {"url": "https://example.com/edit", "cell_index": 2, "content": "x=1"}
    )
    assert err is None
    assert cmd["action"] == "edit_and_run_cell"
    assert cmd["cellIndex"] == 1
    assert cmd["content"] == "x=1"
    assert cmd["runCell"] is True


def test_normalize_select_cell_args_success():
    cmd, err = btu.normalize_select_cell_args(
        {"url": "https://example.com/edit", "cell_index": 2}
    )
    assert err is None
    assert cmd["action"] == "select_cell_by_index"
    assert cmd["cellIndex"] == 1
    assert cmd["runCell"] is False


def test_normalize_insert_cell_args_success():
    cmd, err = btu.normalize_insert_cell_args(
        {"url": "https://example.com/edit", "index": 3, "direction": "above"}
    )
    assert err is None
    assert cmd["action"] == "insert_cell"
    assert cmd["cellIndex"] == 2
    assert cmd["direction"] == "above"


def test_normalize_insert_cell_args_ignores_content():
    cmd, err = btu.normalize_insert_cell_args(
        {
            "url": "https://example.com/edit",
            "index": 1,
            "direction": "below",
            "content": "print(42)",
        }
    )
    assert err is None
    assert "content" not in cmd


def test_map_insert_cell_does_not_forward_content():
    from testing.host import bot_command as bc

    mapped = bc.map_command_to_native(
        {
            "action": "insert_cell",
            "url": "https://example.com/edit",
            "cellIndex": 0,
            "direction": "below",
            "content": "x = 1",
        }
    )
    assert mapped is not None
    assert mapped["type"] == "INSERT_CELL"
    assert "content" not in mapped


def test_map_insert_cell_default_max_wait_ms():
    from testing.host import bot_command as bc

    mapped = bc.map_command_to_native(
        {
            "action": "insert_cell",
            "url": "https://example.com/edit",
            "cellIndex": 0,
            "direction": "below",
        }
    )
    assert mapped is not None
    assert mapped["maxWaitMs"] == 1500
    assert "content" not in mapped


def test_normalize_delete_cell_args_includes_tool_on_error():
    cmd, err = btu.normalize_delete_cell_args({"cell_index": 1})
    assert cmd is None
    assert err["tool"] == "delete_by_index"
