"""Read live notebook scrape only (no persistent merge) for structure observation."""



from __future__ import annotations



import json

from pathlib import Path

from typing import Any



from .cell_execution_observer import (
    CellExecutionObservation,
    cells_from_raw_execution,
    execution_fingerprint,
)
from .cell_structure_observer import CellObservation, cells_from_raw, structure_fingerprint



_HOST_DIR = Path(__file__).resolve().parent

_LIVE_ROOT = _HOST_DIR / "data" / "notebooks" / "live"
_META_ROOT = _HOST_DIR / "data" / "meta"
_EXECUTION_STATE_PATH = _META_ROOT / "execution_state.json"
_HOST_LOG_PATH = _HOST_DIR / "data" / "logs" / "host.log"





def _read_json(path: Path) -> dict | None:

    try:

        with path.open(encoding="utf-8") as fh:

            data = json.load(fh)

        return data if isinstance(data, dict) else None

    except (OSError, json.JSONDecodeError):

        return None





def _safe_filename(url: str) -> str:

    safe = "".join(c if c.isalnum() else "_" for c in (url or "")).strip("_")

    return f"{safe[:200]}.json"





def _url_matches(data: dict, url: str) -> bool:

    tab = str(data.get("tabUrl") or "").strip().rstrip("/").lower()

    want = str(url or "").strip().rstrip("/").lower()

    return bool(tab and want and tab == want)





def _path_score(path: Path, data: dict) -> tuple:

    updated = str(data.get("lastUpdated") or "")

    try:

        mtime = path.stat().st_mtime

    except OSError:

        mtime = 0.0

    cell_count = len(data.get("cells") or []) if isinstance(data.get("cells"), list) else 0

    canonical = 1 if path.name.startswith("kaggle_kernel_") else 0

    return (updated, mtime, cell_count, canonical)





def _live_candidates(url: str) -> list[Path]:

    """All live JSON files that may belong to this notebook URL."""

    if not url or not _LIVE_ROOT.is_dir():

        return []



    seen: set[str] = set()

    candidates: list[Path] = []



    def _add(path: Path) -> None:

        key = str(path.resolve())

        if key not in seen and path.is_file():

            seen.add(key)

            candidates.append(path)



    # Config-free: match by URL slug filename and glob (no notebook_storage/config).

    _add(_LIVE_ROOT / _safe_filename(url))



    for path in _LIVE_ROOT.glob("*.json"):

        data = _read_json(path)

        if data and _url_matches(data, url):

            _add(path)



    return candidates





def live_snapshot_path(url: str) -> Path | None:

    """Return the freshest live scrape file for a notebook URL, if any."""

    candidates = _live_candidates(url)

    if not candidates:

        return None



    best_path: Path | None = None

    best_score: tuple = ("", 0.0, -1, -1)

    for path in candidates:

        data = _read_json(path)

        if not data:

            continue

        score = _path_score(path, data)

        if score > best_score:

            best_score = score

            best_path = path

    return best_path





def read_live_cells(url: str) -> tuple[list[CellObservation], dict[str, Any]]:

    """

    Load cells from the latest live scrape file only.



    Returns (cells, meta) where meta includes path, lastUpdated, cell_count.

    Does not read persistent/legacy snapshots.

    """

    path = live_snapshot_path(url)

    if path is None:

        return [], {"source": "none", "path": None}

    data = _read_json(path) or {}

    raw = data.get("cells") if isinstance(data.get("cells"), list) else []

    cells = cells_from_raw(raw)

    return cells, {

        "source": "live",

        "path": str(path),

        "lastUpdated": str(data.get("lastUpdated") or ""),

        "cell_count": len(cells),

        "tabUrl": str(data.get("tabUrl") or url),

        "fingerprint": structure_fingerprint(cells),

    }





def live_snapshot_mtime(url: str) -> float:

    path = live_snapshot_path(url)

    if path is None:

        return 0.0

    try:

        return path.stat().st_mtime

    except OSError:

        return 0.0





def live_snapshot_signature(url: str) -> tuple[str, str, int]:

    """(lastUpdated, structure_fingerprint, cell_count) for the freshest live file."""

    path = live_snapshot_path(url)

    if path is None:

        return ("", "", 0)

    data = _read_json(path) or {}

    raw = data.get("cells") if isinstance(data.get("cells"), list) else []

    cells = cells_from_raw(raw)

    return (

        str(data.get("lastUpdated") or ""),

        structure_fingerprint(cells),

        len(cells),

    )





def read_live_execution_cells(url: str) -> tuple[list[CellExecutionObservation], dict[str, Any]]:
    """Load code-cell execution fields from the latest live scrape only."""
    path = live_snapshot_path(url)
    if path is None:
        return [], {"source": "none", "path": None}
    data = _read_json(path) or {}
    raw = data.get("cells") if isinstance(data.get("cells"), list) else []
    cells = cells_from_raw_execution(raw)
    return cells, {
        "source": "live",
        "path": str(path),
        "lastUpdated": str(data.get("lastUpdated") or ""),
        "cell_count": len(cells),
        "tabUrl": str(data.get("tabUrl") or url),
        "kernelScenario": str(data.get("kernelScenario") or ""),
        "fingerprint": execution_fingerprint(cells),
    }





def execution_watch_paths(url: str) -> list[Path]:
    """Local files that may update before the next sendTabs interval (~5s)."""
    paths: list[Path] = []
    live = live_snapshot_path(url)
    if live is not None:
        paths.append(live)
    if _EXECUTION_STATE_PATH.is_file():
        paths.append(_EXECUTION_STATE_PATH)
    if _HOST_LOG_PATH.is_file():
        paths.append(_HOST_LOG_PATH)
    return paths





def execution_watch_mtimes(url: str) -> tuple[float, ...]:
    out: list[float] = []
    for path in execution_watch_paths(url):
        try:
            out.append(path.stat().st_mtime)
        except OSError:
            out.append(0.0)
    return tuple(out)





def read_revision_execution_cells(url: str) -> list[CellExecutionObservation]:
    """Optional secondary source when live scrape lacks execution metadata."""
    nb = _execution_state_notebook(url)
    if not isinstance(nb, dict):
        return []
    rev_hash = nb.get("active_revision")
    revisions = nb.get("revisions") if isinstance(nb.get("revisions"), dict) else {}
    rev = revisions.get(rev_hash) if rev_hash else None
    if not isinstance(rev, dict):
        return []
    rows = rev.get("cells")
    if not isinstance(rows, dict):
        return []

    out: list[CellExecutionObservation] = []
    for key, row in rows.items():
        if not isinstance(row, dict):
            continue
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        order = row.get("baseline_order")
        try:
            order = int(order) if order is not None else None
        except (TypeError, ValueError):
            order = None
        title = str(row.get("title") or "").strip()
        status = "running" if row.get("seen_running") else "idle"
        if title and "executed" in title.lower():
            status = "executed"
        out.append(
            CellExecutionObservation(
                index=idx,
                execution_order=order,
                execution_title=title,
                execution_status=status,
                output_hash="",
            )
        )
    out.sort(key=lambda c: c.index)
    return out





def merge_execution_observations(
    live: Iterable[CellExecutionObservation],
    revision: Iterable[CellExecutionObservation],
) -> list[CellExecutionObservation]:
    """Prefer live output/status; fill execution order/title from revision when missing."""
    merged: dict[int, CellExecutionObservation] = {c.index: c for c in live}
    for rev in revision:
        cur = merged.get(rev.index)
        if cur is None:
            merged[rev.index] = rev
            continue
        order = cur.execution_order if cur.execution_order is not None else rev.execution_order
        title = cur.execution_title or rev.execution_title
        status = cur.execution_status
        if status == "idle" and rev.is_running:
            status = rev.execution_status
        if not cur.title_executed and rev.title_executed:
            title = rev.execution_title
        merged[rev.index] = CellExecutionObservation(
            index=cur.index,
            execution_order=order,
            execution_title=title,
            execution_status=status,
            output_hash=cur.output_hash,
        )
    return [merged[i] for i in sorted(merged)]





def normalize_notebook_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def _execution_state_notebook(url: str) -> dict | None:
    state = _read_json(_EXECUTION_STATE_PATH) or {}
    if not isinstance(state, dict):
        return None
    want = normalize_notebook_url(url)
    direct = state.get(url)
    if isinstance(direct, dict):
        return direct
    for key, value in state.items():
        if normalize_notebook_url(str(key)) == want and isinstance(value, dict):
            return value
    return None


def read_kernel_scenario_for_url(url: str) -> str:
    """Kernel scenario from execution_state.json (survives missing live scrape fields)."""
    nb = _execution_state_notebook(url)
    if not isinstance(nb, dict):
        return ""
    return str(nb.get("last_kernel_scenario") or nb.get("kernel_scenario") or "")


def read_live_code_outputs(url: str) -> dict[int, str]:
    """1-based code cell index → output text from the freshest live scrape."""
    path = live_snapshot_path(url)
    if path is None:
        return {}
    data = _read_json(path) or {}
    raw = data.get("cells") if isinstance(data.get("cells"), list) else []
    out: dict[int, str] = {}
    for cell in raw:
        if not isinstance(cell, dict):
            continue
        if str(cell.get("type") or "code").strip().lower() != "code":
            continue
        try:
            idx = int(cell.get("index"))
        except (TypeError, ValueError):
            continue
        if idx >= 1:
            out[idx] = str(cell.get("output") or "")
    return out


def read_host_log_exec_lines(offset: int) -> tuple[list[tuple[int, int]], int]:
    """Parse new EXEC DETECTED lines from host.log; return (pairs, new_offset)."""
    import re

    if not _HOST_LOG_PATH.is_file():
        return [], offset
    try:
        size = _HOST_LOG_PATH.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0
    if size <= offset:
        return [], offset
    try:
        with _HOST_LOG_PATH.open(encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            chunk = fh.read()
    except OSError:
        return [], offset
    found: list[tuple[int, int]] = []
    for line in chunk.splitlines():
        m = re.search(r"EXEC DETECTED cell=(\d+) order=(\d+)", line)
        if m:
            found.append((int(m.group(1)), int(m.group(2))))
    return found, size


def read_host_log_prompt_signals(
    offset: int,
    *,
    cell_index: int | None = None,
) -> tuple[list[tuple[int, str]], int]:
    """Parse PROMPT_SIGNAL lines from host.log; return ([(app_cell_index, text)], new_offset)."""
    import re

    pattern = re.compile(
        r"PROMPT_SIGNAL cell=(\d+)\s+(?:order=\S+\s+)?text=(.+?)(?:\s+ts=\S+)?$"
    )
    if not _HOST_LOG_PATH.is_file():
        return [], offset
    try:
        size = _HOST_LOG_PATH.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0
    if size <= offset:
        return [], offset
    try:
        with _HOST_LOG_PATH.open(encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            chunk = fh.read()
    except OSError:
        return [], offset
    found: list[tuple[int, str]] = []
    for line in chunk.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        try:
            ci = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if cell_index is not None and ci != int(cell_index):
            continue
        text = str(m.group(2) or "").strip()
        if text:
            found.append((ci, text))
    return found, size


