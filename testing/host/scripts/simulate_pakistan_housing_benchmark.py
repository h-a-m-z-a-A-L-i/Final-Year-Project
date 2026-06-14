#!/usr/bin/env python3
"""
Simulate a complex multi-tool agent workflow on the Pakistan housing notebook JSON.
Measures local read-tool latency and mocked browser-tool round-trip cost (no live browser).
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.tool_registry import BROWSER_TOOL_NAMES, registry  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/pakistan-housing/edit"
NOTEBOOK_FILE = (
    REPO
    / "testing/host/data/notebooks/https___www_kaggle_com_code_codekey_pakistan_housing_edit.json"
)

# Simulated user task (complex, multi-step)
TASK = (
    "Analyze the CatBoost training pipeline: locate where catboost_model is defined, "
    "inspect upstream feature engineering cells, add a markdown note above the CatBoost cell "
    "explaining GPU training, insert a new code cell below it to print feature importances, "
    "and verify notebook structure before running the new cell."
)


@dataclass
class ToolResult:
    name: str
    phase: str
    ok: bool
    elapsed_ms: float
    payload_bytes: int
    summary: str
    error: str | None = None


@dataclass
class BenchmarkReport:
    task: str
    notebook_url: str
    notebook_file: str
    cell_count: int
    results: list[ToolResult] = field(default_factory=list)

    def add(self, r: ToolResult) -> None:
        self.results.append(r)


def _payload_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _run_tool(reg, name: str, args: dict, phase: str) -> ToolResult:
    t0 = time.perf_counter()
    try:
        out = reg.call(name, args)
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return ToolResult(name, phase, False, elapsed, 0, "", str(e))
    elapsed = (time.perf_counter() - t0) * 1000
    ok = bool(out.get("ok", False))
    summary = _summarize(name, out)
    return ToolResult(
        name=name,
        phase=phase,
        ok=ok,
        elapsed_ms=elapsed,
        payload_bytes=_payload_size(out),
        summary=summary,
        error=out.get("error") if not ok else None,
    )


def _summarize(name: str, out: dict) -> str:
    if not out.get("ok"):
        return str(out.get("error") or "failed")[:120]
    if name == "notebook_snapshot_status":
        return f"snapshot={out.get('snapshot')} cells={out.get('cell_count')} code={out.get('code_cells')}"
    if name == "notebook_list_cells":
        return f"listed {out.get('cell_count')} cells"
    if name == "notebook_search":
        return f"hits={out.get('hit_count')} first={((out.get('hits') or [{}])[0]).get('cell_index')}"
    if name == "notebook_find_symbol":
        return f"latest_def_cell={out.get('latest_definition_cell')} defs={len(out.get('definitions') or [])}"
    if name == "notebook_graph_query":
        g = out.get("graph") or []
        return f"graph_nodes={len(g)}"
    if name == "notebook_get_cell":
        c = out.get("cell") or {}
        return f"cell {c.get('index')} type={c.get('type')} input_len={len(c.get('input') or '')}"
    if name == "notebook_get_cells":
        return f"fetched {len(out.get('cells') or [])} cells"
    if name == "notebook_cell_neighbors":
        return f"up={len(out.get('direct_upstream') or [])} down={len(out.get('direct_downstream') or [])}"
    if name == "notebook_recommend_placement":
        rec = out.get("recommendation") or {}
        return f"insert_below={rec.get('insert_below_cell_index')}"
    if name in BROWSER_TOOL_NAMES:
        return f"phase={out.get('phase', 'mock')} cell={out.get('cell_index') or out.get('new_cell_index')}"
    return "ok"


class MockBrowserState:
    """In-memory notebook mirror for mocked write tools."""

    def __init__(self, cells: list[dict]):
        self.cells = [dict(c) for c in cells]
        self._next_dom = len(cells)

    def select_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        if not any(int(c.get("index", 0)) == idx for c in self.cells):
            return {"ok": False, "error": f"Cell {idx} not found"}
        return {"ok": True, "tool": "select_cell_by_index", "cell_index": idx, "phase": "selected"}

    def click_cell(self, args: dict) -> dict:
        idx = int(args.get("cell_index") or 1)
        return {"ok": True, "tool": "click_cell", "cell_index": idx, "domIndex": idx - 1, "phase": "focused"}

    def insert_cell(self, args: dict) -> dict:
        anchor = int(args["index"])
        direction = args.get("direction", "below")
        pos = anchor if direction == "below" else max(0, anchor - 1)
        new_cell = {"type": "code", "index": 0, "input": "", "output": ""}
        insert_at = min(pos, len(self.cells))
        self.cells.insert(insert_at, new_cell)
        for i, c in enumerate(self.cells, start=1):
            c["index"] = i
        new_idx = insert_at + 1
        self._next_dom += 1
        return {
            "ok": True,
            "tool": "insert_cell",
            "new_cell_index": new_idx,
            "new_dom_index": insert_at,
            "direction": direction,
            "phase": "inserted",
        }

    def edit_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        content = args.get("content") or ""
        for c in self.cells:
            if int(c.get("index", 0)) == idx:
                c["input"] = content
                return {
                    "ok": True,
                    "tool": "edit_cell_by_index",
                    "cell_index": idx,
                    "phase": "content_set",
                    "chars": len(content),
                }
        return {"ok": False, "error": f"Cell {idx} not found"}

    def run_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        return {
            "ok": True,
            "tool": "run_cell",
            "cell_index": idx,
            "phase": "executed",
            "execution_title": f"Execution #{idx}",
        }

    def delete_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        before = len(self.cells)
        self.cells = [c for c in self.cells if int(c.get("index", 0)) != idx]
        if len(self.cells) == before:
            return {"ok": False, "error": f"Cell {idx} not found"}
        for i, c in enumerate(self.cells, start=1):
            c["index"] = i
        return {"ok": True, "tool": "delete_by_index", "cell_index": idx, "phase": "deleted"}

    def create_markdown(self, args: dict) -> dict:
        anchor = int(args["index"])
        new_cell = {"type": "markdown", "index": 0, "input": "", "output": ""}
        insert_at = max(0, anchor - 1)
        self.cells.insert(insert_at, new_cell)
        for i, c in enumerate(self.cells, start=1):
            c["index"] = i
        return {
            "ok": True,
            "tool": "creating_markdown_by_index",
            "new_cell_index": insert_at + 1,
            "phase": "markdown_inserted",
        }


def _mock_browser_call(state: MockBrowserState, name: str, args: dict) -> dict:
    args = dict(args)
    args.setdefault("url", URL)
    handlers: dict[str, Callable] = {
        "select_cell_by_index": state.select_cell,
        "click_cell": state.click_cell,
        "insert_cell": state.insert_cell,
        "edit_cell_by_index": state.edit_cell,
        "run_cell": state.run_cell,
        "delete_by_index": state.delete_cell,
        "creating_markdown_by_index": state.create_markdown,
    }
    fn = handlers.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown browser tool {name}"}
    return fn(args)


def _find_catboost_cell(reg) -> int | None:
    out = reg.call("notebook_search", {"url": URL, "query": "CatBoostRegressor", "limit": 5})
    hits = out.get("hits") or []
    if hits:
        return int(hits[0]["cell_index"])
    sym = reg.call("notebook_find_symbol", {"url": URL, "symbol": "catboost_model"})
    if sym.get("ok") and sym.get("latest_definition_cell"):
        return int(sym["latest_definition_cell"])
    return None


def run_simulation() -> BenchmarkReport:
    reg = registry()
    raw = json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8"))
    cells = raw.get("cells") or []
    report = BenchmarkReport(
        task=TASK,
        notebook_url=URL,
        notebook_file=str(NOTEBOOK_FILE),
        cell_count=len(cells),
    )

    # --- Phase 1: Discovery (read tools) ---
    read_steps = [
        ("notebook_snapshot_status", {"url": URL}),
        ("notebook_list_cells", {"url": URL, "preview_chars": 80}),
        ("notebook_search", {"url": URL, "query": "CatBoostRegressor", "limit": 10}),
        ("notebook_search", {"url": URL, "query": "corr_with_price", "limit": 5}),
        ("notebook_find_symbol", {"url": URL, "symbol": "catboost_model"}),
        ("notebook_find_symbol", {"url": URL, "symbol": "feature_cols"}),
        ("notebook_find_symbol", {"url": URL, "symbol": "df1"}),
        ("notebook_graph_query", {"url": URL}),
    ]
    for name, args in read_steps:
        report.add(_run_tool(reg, name, args, "discovery"))

    catboost_cell = _find_catboost_cell(reg)
    if catboost_cell is None:
        catboost_cell = max(int(c.get("index", 1)) for c in cells)

    report.add(
        _run_tool(reg, "notebook_get_cell", {"url": URL, "cell_index": catboost_cell, "include_output": True}, "analysis")
    )
    report.add(
        _run_tool(
            reg,
            "notebook_get_cells",
            {"url": URL, "cell_indices": [1, 5, 8, 19, catboost_cell], "include_output": False},
            "analysis",
        )
    )
    report.add(
        _run_tool(reg, "notebook_cell_neighbors", {"url": URL, "cell_index": catboost_cell}, "analysis")
    )
    report.add(
        _run_tool(
            reg,
            "notebook_recommend_placement",
            {"url": URL, "symbols": ["catboost_model", "feature_cols"]},
            "planning",
        )
    )

    # --- Phase 2: Write workflow (mocked browser tools) ---
    browser_state = MockBrowserState(cells)
    markdown_idx = catboost_cell
    new_code_idx = catboost_cell + 1  # shifts after markdown insert

    browser_steps = [
        ("select_cell_by_index", {"cell_index": catboost_cell}),
        ("click_cell", {"cell_index": catboost_cell}),
        (
            "creating_markdown_by_index",
            {"index": markdown_idx},
        ),
        (
            "edit_cell_by_index",
            {
                "cell_index": markdown_idx,
                "content": (
                    "## CatBoost GPU Training\n\n"
                    "This cell trains a CatBoost regressor on log-price with early stopping "
                    "on the validation set. Feature importances can be extracted via "
                    "`catboost_model.get_feature_importance()`."
                ),
            },
        ),
        ("insert_cell", {"index": catboost_cell + 1, "direction": "below"}),
        (
            "edit_cell_by_index",
            {
                "cell_index": new_code_idx + 1,
                "content": (
                    "importances = catboost_model.get_feature_importance()\n"
                    "feat_imp = sorted(zip(feature_cols, importances), key=lambda x: -x[1])\n"
                    "for name, score in feat_imp[:10]:\n"
                    "    print(f'{name:25s} {score:.4f}')"
                ),
            },
        ),
        ("select_cell_by_index", {"cell_index": new_code_idx + 1}),
        ("run_cell", {"cell_index": new_code_idx + 1}),
        ("notebook_list_cells", {"url": URL, "preview_chars": 60}),
        ("delete_by_index", {"cell_index": new_code_idx + 1}),
    ]

    for name, args in browser_steps:
        if name.startswith("notebook_"):
            report.add(_run_tool(reg, name, args, "verify"))
        else:
            t0 = time.perf_counter()
            out = _mock_browser_call(browser_state, name, args)
            elapsed = (time.perf_counter() - t0) * 1000
            report.add(
                ToolResult(
                    name=name,
                    phase="write",
                    ok=bool(out.get("ok")),
                    elapsed_ms=elapsed,
                    payload_bytes=_payload_size(out),
                    summary=_summarize(name, out),
                    error=out.get("error") if not out.get("ok") else None,
                )
            )

    return report


def print_report(report: BenchmarkReport) -> None:
    print("=" * 72)
    print("PAKISTAN HOUSING NOTEBOOK — COMPLEX TASK SIMULATION")
    print("=" * 72)
    print(f"Notebook URL : {report.notebook_url}")
    print(f"JSON file    : {report.notebook_file}")
    print(f"Cells        : {report.cell_count}")
    print(f"\nTask:\n  {report.task}\n")

    by_phase: dict[str, list[ToolResult]] = {}
    for r in report.results:
        by_phase.setdefault(r.phase, []).append(r)

    total_ms = 0.0
    ok_count = 0
    for phase, items in by_phase.items():
        print(f"--- {phase.upper()} ({len(items)} tool calls) ---")
        for r in items:
            status = "OK" if r.ok else "FAIL"
            print(
                f"  [{status}] {r.name:32s} {r.elapsed_ms:7.2f} ms  "
                f"payload={r.payload_bytes:6d} B  {r.summary}"
            )
            if r.error:
                print(f"         error: {r.error}")
            total_ms += r.elapsed_ms
            ok_count += int(r.ok)
        print()

    read_times = [r.elapsed_ms for r in report.results if r.name.startswith("notebook_")]
    write_times = [r.elapsed_ms for r in report.results if r.name in BROWSER_TOOL_NAMES]
    all_times = [r.elapsed_ms for r in report.results]

    print("=" * 72)
    print("PERFORMANCE SUMMARY")
    print("=" * 72)
    print(f"Total tool calls     : {len(report.results)}")
    print(f"Success rate         : {ok_count}/{len(report.results)} ({100*ok_count/len(report.results):.1f}%)")
    print(f"Total wall time      : {total_ms:.2f} ms ({total_ms/1000:.3f} s)")
    print(f"Avg per call         : {statistics.mean(all_times):.2f} ms")
    print(f"Median per call      : {statistics.median(all_times):.2f} ms")
    print(f"P95 per call         : {sorted(all_times)[int(len(all_times)*0.95)-1]:.2f} ms")
    if read_times:
        print(f"Read tools avg       : {statistics.mean(read_times):.2f} ms  (n={len(read_times)})")
    if write_times:
        print(f"Browser mock avg     : {statistics.mean(write_times):.2f} ms  (n={len(write_times)})")
    payload_total = sum(r.payload_bytes for r in report.results)
    print(f"Total response bytes : {payload_total:,} B ({payload_total/1024:.1f} KiB)")
    print(f"Largest payload      : {max(r.payload_bytes for r in report.results):,} B "
          f"({max(report.results, key=lambda x: x.payload_bytes).name})")
    print("=" * 72)


def main() -> int:
    if not NOTEBOOK_FILE.is_file():
        print(f"Missing notebook JSON: {NOTEBOOK_FILE}", file=sys.stderr)
        return 1
    report = run_simulation()
    print_report(report)
    failed = [r for r in report.results if not r.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
