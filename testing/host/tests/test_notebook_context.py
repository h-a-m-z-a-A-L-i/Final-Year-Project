import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import config
from testing.host import persistence_helpers as ph
from testing.host import notebook_context as nc


FIXTURE_CELLS = {
    "cells": [
        {"index": 1, "type": "code", "input": "import pandas as pd\ndf = pd.read_csv('x.csv')", "output": ""},
        {"index": 2, "type": "code", "input": "print(df.head())", "output": "Traceback...\nNameError: df"},
        {"index": 3, "type": "markdown", "input": "# Notes", "output": ""},
    ]
}


def _write_snapshot(tmp_path, url):
    monkeypatch_scraped = tmp_path / "notebooks"
    persistent = monkeypatch_scraped / "persistent"
    persistent.mkdir(parents=True, exist_ok=True)
    fn = ph.get_safe_filename(url)
    ph._atomic_write_json(persistent / fn, FIXTURE_CELLS)
    return monkeypatch_scraped


def test_build_graph_list(tmp_path, monkeypatch):
    url = "https://example.com/notebook/edit"
    scraped = _write_snapshot(tmp_path, url)
    monkeypatch.setattr(config, "SCRAPED_DIR", scraped)

    graph = nc.build_graph_list(url)
    assert len(graph) >= 2
    cell2 = next((n for n in graph if n["cell_number"] == 2), None)
    assert cell2 is not None
    assert isinstance(cell2.get("dependencies"), list)


def test_pack_dependency_includes_graph(tmp_path, monkeypatch):
    url = "https://example.com/notebook/edit"
    scraped = _write_snapshot(tmp_path, url)
    monkeypatch.setattr(config, "SCRAPED_DIR", scraped)

    pack = nc.pack_context(mode="ask", url=url, prompt="cell 2 dependencies", cell_index=2)
    assert "CONTEXT_MANIFEST" in pack.text
    assert "depends_on" in pack.text
    assert pack.coverage in ("full", "partial")
    assert 2 in (pack.manifest.get("listed_cells") or [])


def test_format_cell_block_includes_output(tmp_path, monkeypatch):
    cell = {
        "index": 2,
        "type": "code",
        "input": "print(x)",
        "output": "NameError: name 'x' is not defined",
    }
    block = nc._format_cell_block(cell, include_output=True)
    assert "NameError" in block
    assert "output:" in block


def test_pack_simple_includes_output(tmp_path, monkeypatch):
    url = "https://example.com/notebook/edit"
    scraped = _write_snapshot(tmp_path, url)
    monkeypatch.setattr(config, "SCRAPED_DIR", scraped)
    pack = nc.pack_context(mode="ask", url=url, prompt="cell 2", cell_index=2)
    assert "NameError" in pack.text or "Traceback" in pack.text or "output:" in pack.text


def test_pack_explain_error_includes_output(tmp_path, monkeypatch):
    url = "https://example.com/notebook/edit"
    scraped = _write_snapshot(tmp_path, url)
    monkeypatch.setattr(config, "SCRAPED_DIR", scraped)

    pack = nc.pack_context(mode="ask", url=url, prompt="fix error in cell 2", cell_index=2)
    assert "NameError" in pack.text or "output" in pack.text
    assert "### Cell [2]" in pack.text


def test_pack_none_snapshot(tmp_path, monkeypatch):
    url = "https://example.com/empty/edit"
    scraped = tmp_path / "notebooks"
    scraped.mkdir(parents=True)
    monkeypatch.setattr(config, "SCRAPED_DIR", scraped)

    pack = nc.pack_context(mode="ask", url=url, prompt="hello")
    assert pack.coverage == "none"
    assert "CONTEXT_MANIFEST" in pack.text


def test_truncate_at_cell_boundaries():
    body = "### Cell [1]\ncode\n" + "### Cell [2]\n" + ("x" * 5000)
    out = nc._truncate_at_cell_boundaries(body, 200)
    assert "Cell [2]" not in out or "omitted" in out
