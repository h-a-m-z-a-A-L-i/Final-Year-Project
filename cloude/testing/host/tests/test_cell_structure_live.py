"""Tests for live-only cell scrape reader."""

from __future__ import annotations

import json
import os
import sys
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.cell_structure_live import (  # noqa: E402
    live_snapshot_path,
    read_live_cells,
)


def test_read_live_cells_only(tmp_path, monkeypatch):
    import testing.host.cell_structure_live as live

    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "kaggle_kernel_1.json"
    path.write_text(
        json.dumps(
            {
                "tabUrl": "https://example.com/notebook/edit",
                "lastUpdated": "t1",
                "cells": [{"type": "code", "index": 1, "input": "x=1"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(live, "_LIVE_ROOT", live_dir)

    cells, meta = read_live_cells("https://example.com/notebook/edit")
    assert len(cells) == 1
    assert cells[0].index == 1
    assert meta["source"] == "live"


def test_prefers_fresh_kernel_file_over_stale_url_slug(tmp_path, monkeypatch):
    import testing.host.cell_structure_live as live

    live_dir = tmp_path / "live"
    live_dir.mkdir()
    url = "https://example.com/notebook/edit"

    stale_slug = live_dir / live._safe_filename(url)
    stale_slug.write_text(
        json.dumps(
            {
                "tabUrl": url,
                "lastUpdated": "2020-01-01T00:00:00",
                "cells": [{"type": "code", "index": 1, "input": "old"}],
            }
        ),
        encoding="utf-8",
    )

    kernel_path = live_dir / "kaggle_kernel_99.json"
    kernel_path.write_text(
        json.dumps(
            {
                "tabUrl": url,
                "lastUpdated": "2026-06-15T12:00:00",
                "cells": [
                    {"type": "code", "index": 1, "input": "new"},
                    {"type": "code", "index": 2, "input": "added"},
                ],
            }
        ),
        encoding="utf-8",
    )
    time.sleep(0.01)
    stale_slug.touch()

    monkeypatch.setattr(live, "_LIVE_ROOT", live_dir)

    resolved = live_snapshot_path(url)
    assert resolved == kernel_path

    cells, meta = read_live_cells(url)
    assert len(cells) == 2
    assert cells[1].input == "added"
    assert meta["path"] == str(kernel_path)
