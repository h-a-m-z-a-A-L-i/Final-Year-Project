"""Canonical notebook JSON paths keyed by stable Kaggle kernel id (or URL fallback)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .execution_metadata import strip_cell, strip_cells
    from .notebook_identity import stable_notebook_key
    from .persistence_helpers import _atomic_write_json, get_safe_filename, read_json_file
except Exception:
    from execution_metadata import strip_cell, strip_cells
    from notebook_identity import stable_notebook_key
    from persistence_helpers import _atomic_write_json, get_safe_filename, read_json_file

_EXEC_TOP_KEYS = ("kernelScenario", "execution_state", "run_session")


def resolve_storage_key(url: str, notebook_id=None, *, memory_store=None, log=None) -> str:
    """Stable key for notebook JSON files (matches chat history key when Kaggle id is known)."""
    try:
        from .notebook_identity import resolve_history_key
    except Exception:
        from notebook_identity import resolve_history_key
    return resolve_history_key(url, notebook_id, memory_store=memory_store, log=log) or str(url or "").strip()


def notebook_filename(storage_key: str) -> str:
    key = str(storage_key or "").strip()
    if key.startswith("kaggle:kernel:"):
        try:
            kernel_id = int(key.split(":", 2)[2])
            if kernel_id > 0:
                return f"kaggle_kernel_{kernel_id}.json"
        except (IndexError, ValueError):
            pass
    return get_safe_filename(key)


def _scraped_dir() -> Path:
    try:
        from . import config as _config
        return _config.SCRAPED_DIR
    except Exception:
        try:
            from config import SCRAPED_DIR
            return SCRAPED_DIR
        except Exception:
            from testing.host.config import SCRAPED_DIR
            return SCRAPED_DIR


def notebook_paths(storage_key: str) -> dict[str, Path]:
    filename = notebook_filename(storage_key)
    scraped = _scraped_dir()
    return {
        "live": scraped / "live" / filename,
        "persistent": scraped / "persistent" / filename,
        "legacy": scraped / filename,
    }


def _legacy_url_filenames(url: str) -> list[str]:
    if not url:
        return []
    return [get_safe_filename(url)]


def _normalize_tab_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        return urlunparse(
            (parsed.scheme.lower(), parsed.netloc.lower(), (parsed.path or "").rstrip("/"), "", "", "")
        )
    except Exception:
        return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")


def _tab_url_variants(url: str) -> set[str]:
    normalized = _normalize_tab_url(url)
    if not normalized:
        return set()
    variants = {normalized}
    if normalized.endswith("/edit"):
        variants.add(normalized[: -len("/edit")])
    else:
        variants.add(f"{normalized}/edit")
    return variants


def find_snapshot_by_tab_url(url: str) -> tuple[dict | None, str]:
    """Scan live/persistent/legacy JSON for a matching tabUrl when key lookup fails."""
    variants = _tab_url_variants(url)
    if not variants:
        return None, "none"

    scraped = _scraped_dir()
    search_dirs = (
        ("live", scraped / "live"),
        ("persistent", scraped / "persistent"),
        ("legacy", scraped),
    )
    best_data: dict | None = None
    best_source = "none"
    best_score = (-1, "")
    for source, subdir in search_dirs:
        if not subdir.is_dir():
            continue
        for path in subdir.glob("*.json"):
            raw = read_json_file(path)
            if not _snapshot_has_cells(raw):
                continue
            tab = _normalize_tab_url(str((raw or {}).get("tabUrl") or ""))
            if tab not in variants:
                continue
            updated = str((raw or {}).get("lastUpdated") or "")
            cell_count = len((raw or {}).get("cells") or [])
            score = (cell_count, updated)
            if score > best_score:
                best_score = score
                best_data = raw
                best_source = source if source != "legacy" else "legacy-url"
    if _snapshot_has_cells(best_data):
        return _clean_notebook_payload(best_data, tab_url=url), best_source
    return None, "none"


def _snapshot_has_cells(data: dict | None) -> bool:
    return bool(isinstance(data, dict) and isinstance(data.get("cells"), list) and data.get("cells"))


def _clean_notebook_payload(data: dict, *, tab_url: str = "") -> dict:
    out = dict(data)
    if tab_url:
        out["tabUrl"] = tab_url
    stable = stable_notebook_key(out.get("notebookId"))
    if stable:
        try:
            out["notebookId"] = int(stable.split(":", 2)[2])
        except (IndexError, ValueError):
            pass
    for key in _EXEC_TOP_KEYS:
        out.pop(key, None)
    cells = out.get("cells")
    if isinstance(cells, list):
        out["cells"] = strip_cells([c for c in cells if isinstance(c, dict)])
    return out


def load_notebook_file(path: Path) -> dict | None:
    data = read_json_file(path)
    if not isinstance(data, dict):
        return None
    return _clean_notebook_payload(data)


def pick_best_snapshot(candidates: list[Path]) -> tuple[dict | None, Path | None]:
    best_data: dict | None = None
    best_path: Path | None = None
    best_score = (-1, "")
    for path in candidates:
        if not path.is_file():
            continue
        raw = read_json_file(path)
        if not _snapshot_has_cells(raw):
            continue
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0
        updated = str((raw or {}).get("lastUpdated") or "")
        cell_count = len((raw or {}).get("cells") or [])
        score = (cell_count, updated or str(mtime))
        if score > best_score:
            best_score = score
            best_data = raw
            best_path = path
    return best_data, best_path


def load_notebook_snapshot_for_key(storage_key: str, *, tab_url: str = "") -> tuple[dict | None, str]:
    """Load notebook JSON for a storage key (live > persistent > legacy canonical file)."""
    paths = notebook_paths(storage_key)
    for source, path in (("live", paths["live"]), ("persistent", paths["persistent"]), ("legacy", paths["legacy"])):
        data = load_notebook_file(path)
        if _snapshot_has_cells(data):
            if tab_url and isinstance(data, dict):
                data["tabUrl"] = tab_url
            return data, source
    return None, "none"


def load_notebook_snapshot_for_url(url: str, notebook_id=None, *, memory_store=None, log=None) -> tuple[dict | None, str]:
    storage_key = resolve_storage_key(url, notebook_id, memory_store=memory_store, log=log)
    data, source = load_notebook_snapshot_for_key(storage_key, tab_url=url)
    if _snapshot_has_cells(data):
        return data, source

    # One-time backward compat: URL-slug files before kernel-id migration.
    legacy_names = _legacy_url_filenames(url)
    legacy_candidates: list[Path] = []
    scraped = _scraped_dir()
    for name in legacy_names:
        legacy_candidates.extend(
            [
                scraped / "live" / name,
                scraped / "persistent" / name,
                scraped / name,
            ]
        )
    raw, found = pick_best_snapshot(legacy_candidates)
    if _snapshot_has_cells(raw) and found is not None:
        cleaned = _clean_notebook_payload(raw, tab_url=url)
        return cleaned, "legacy-url"

    data, source = find_snapshot_by_tab_url(url)
    if _snapshot_has_cells(data):
        return data, source
    return None, "none"


def _collect_alias_paths(storage_key: str, urls: list[str]) -> list[Path]:
    paths: list[Path] = []
    canonical = notebook_paths(storage_key)
    for label in ("live", "persistent", "legacy"):
        paths.append(canonical[label])
    seen: set[str] = set()
    scraped = _scraped_dir()
    for url in urls:
        for name in _legacy_url_filenames(url):
            if name in seen:
                continue
            seen.add(name)
            paths.extend(
                [
                    scraped / "live" / name,
                    scraped / "persistent" / name,
                    scraped / name,
                ]
            )
    return paths


def consolidate_kernel_notebook(
    storage_key: str,
    urls: list[str],
    *,
    preferred_tab_url: str = "",
    log=None,
) -> bool:
    """Merge alias URL files into one kernel-id JSON; strip execution metadata."""
    if not storage_key.startswith("kaggle:kernel:"):
        return False
    candidates = _collect_alias_paths(storage_key, urls)
    raw, _ = pick_best_snapshot(candidates)
    if not _snapshot_has_cells(raw):
        return False

    tab_url = preferred_tab_url or str(raw.get("tabUrl") or "")
    if not tab_url and urls:
        tab_url = urls[-1]
    try:
        kernel_id = int(storage_key.split(":", 2)[2])
    except (IndexError, ValueError):
        kernel_id = None

    payload = _clean_notebook_payload(raw, tab_url=tab_url)
    if kernel_id:
        payload["notebookId"] = kernel_id
    payload["storageKey"] = storage_key
    payload["lastUpdated"] = datetime.now(timezone.utc).isoformat()

    paths = notebook_paths(storage_key)
    paths["live"].parent.mkdir(parents=True, exist_ok=True)
    paths["persistent"].parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(paths["live"], payload)
    _atomic_write_json(paths["persistent"], payload)

    canonical_name = notebook_filename(storage_key)
    for path in candidates:
        if not path.is_file():
            continue
        if path.name == canonical_name and path.parent in (paths["live"].parent, paths["persistent"].parent, _scraped_dir()):
            continue
        try:
            path.unlink()
            if log:
                log(f"[notebook_storage] Removed duplicate {path.name}")
        except Exception as exc:
            if log:
                log(f"[notebook_storage] Failed to remove {path}: {exc}")
    return True


def _registry_url_to_key() -> dict[str, str]:
    try:
        from .notebook_identity import NOTEBOOK_REGISTRY_PATH
    except Exception:
        from notebook_identity import NOTEBOOK_REGISTRY_PATH
    if not NOTEBOOK_REGISTRY_PATH.is_file():
        return {}
    try:
        reg = json.loads(NOTEBOOK_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    mapping = reg.get("url_to_key") if isinstance(reg, dict) else {}
    return mapping if isinstance(mapping, dict) else {}


def _fast_storage_key(url: str) -> str:
    normalized = str(url or "").strip()
    mapped = _registry_url_to_key().get(normalized)
    if mapped:
        return str(mapped)
    return normalized


def purge_url_slug_duplicates(*, log=None) -> int:
    """Remove URL-named JSON when canonical kaggle_kernel_<id>.json exists."""
    removed = 0
    mapping = _registry_url_to_key()
    kernel_urls: dict[str, list[str]] = {}
    for url, key in mapping.items():
        if str(key).startswith("kaggle:kernel:"):
            kernel_urls.setdefault(str(key), []).append(url)

    for storage_key, urls in kernel_urls.items():
        canonical = notebook_paths(storage_key)
        if not canonical["persistent"].is_file() and not canonical["live"].is_file():
            continue
        for url in urls:
            slug = get_safe_filename(url)
            scraped = _scraped_dir()
            for path in (
                scraped / slug,
                scraped / "live" / slug,
                scraped / "persistent" / slug,
            ):
                if path.is_file():
                    try:
                        path.unlink()
                        removed += 1
                        if log:
                            log(f"[notebook_storage] Purged URL slug duplicate {path.name}")
                    except Exception as exc:
                        if log:
                            log(f"[notebook_storage] Purge failed {path}: {exc}")
    return removed


def repair_registry_kernel_urls(*, log=None) -> None:
    """Ensure each URL maps to one kernel entry (drop stale cross-links)."""
    try:
        from .notebook_identity import _load_registry, _save_registry, _REGISTRY_LOCK
    except Exception:
        from notebook_identity import _load_registry, _save_registry, _REGISTRY_LOCK

    with _REGISTRY_LOCK:
        reg = _load_registry()
        url_to_key = reg.get("url_to_key", {})
        kernels = reg.get("kernels", {})
        if not isinstance(url_to_key, dict) or not isinstance(kernels, dict):
            return

        # Rebuild kernels.urls from authoritative url_to_key mapping.
        rebuilt: dict[str, dict] = {}
        for url, key in url_to_key.items():
            if not str(key).startswith("kaggle:kernel:"):
                continue
            entry = rebuilt.setdefault(
                str(key),
                {
                    "notebookId": int(str(key).split(":", 2)[2]),
                    "urls": [],
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
            if url not in entry["urls"]:
                entry["urls"].append(url)
        reg["kernels"] = rebuilt
        _save_registry(reg)
        if log:
            log(f"[notebook_storage] Repaired registry ({len(rebuilt)} kernels)")


def migrate_chat_keys_to_stable(memory_store, *, log=None) -> int:
    """Move chat rows from URL keys to kaggle:kernel:id when registry knows the id."""
    if not memory_store:
        return 0
    migrated = 0
    mapping = _registry_url_to_key()
    try:
        with memory_store._lock:
            with memory_store._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT notebook_url FROM messages WHERE notebook_url NOT LIKE 'kaggle:kernel:%'"
                ).fetchall()
        for (old_key,) in rows:
            old = str(old_key or "").strip()
            if not old or old.startswith("kaggle:kernel:"):
                continue
            new_key = mapping.get(old) or _fast_storage_key(old)
            if not new_key or new_key == old or not str(new_key).startswith("kaggle:kernel:"):
                continue
            count = memory_store.migrate_notebook_key(old, new_key)
            if count and log:
                log(f"[notebook_storage] Migrated {count} chat rows: {old[:48]} -> {new_key}")
            migrated += int(count or 0)
    except Exception as exc:
        if log:
            log(f"[notebook_storage] Chat migration skipped: {exc}")
    return migrated


def migrate_hashes_to_storage_keys(*, log=None) -> None:
    try:
        from .persistence_helpers import _load_hashes, _save_hashes
    except Exception:
        from persistence_helpers import _load_hashes, _save_hashes

    hashes = _load_hashes()
    if not hashes:
        return
    mapping = _registry_url_to_key()
    out: dict[str, str] = {}
    changed = False
    for key, digest in hashes.items():
        if str(key).startswith("kaggle:kernel:"):
            out[key] = digest
            continue
        if str(key).startswith("http"):
            new_key = mapping.get(key) or key
            if new_key != key:
                changed = True
                if log:
                    log(f"[notebook_storage] Hash key {key[:40]}... -> {new_key}")
                key = new_key
        out[key] = digest
    if changed or len(out) != len(hashes):
        _save_hashes(out)


def consolidate_notebook_storage(*, memory_store=None, log=None) -> dict[str, Any]:
    """One-shot: unique kernel JSON files, strip execution data, migrate chats."""
    summary: dict[str, Any] = {"kernels": 0, "chat_rows": 0, "execution_state_removed": False, "purged_slugs": 0}

    try:
        from .config import EXECUTION_STATE_PATH
    except Exception:
        from config import EXECUTION_STATE_PATH

    if EXECUTION_STATE_PATH.is_file():
        try:
            EXECUTION_STATE_PATH.unlink()
            summary["execution_state_removed"] = True
            if log:
                log("[notebook_storage] Removed execution_state.json")
        except Exception as exc:
            if log:
                log(f"[notebook_storage] Could not remove execution_state.json: {exc}")

    repair_registry_kernel_urls(log=log)

    try:
        from .notebook_identity import NOTEBOOK_REGISTRY_PATH
    except Exception:
        from notebook_identity import NOTEBOOK_REGISTRY_PATH

    reg: dict = {}
    if NOTEBOOK_REGISTRY_PATH.is_file():
        try:
            reg = json.loads(NOTEBOOK_REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception:
            reg = {}

    kernels = reg.get("kernels") if isinstance(reg, dict) else {}
    if isinstance(kernels, dict):
        for storage_key, entry in kernels.items():
            if not str(storage_key).startswith("kaggle:kernel:"):
                continue
            urls = entry.get("urls") if isinstance(entry, dict) else []
            if not isinstance(urls, list):
                urls = []
            preferred = urls[-1] if urls else ""
            if consolidate_kernel_notebook(storage_key, urls, preferred_tab_url=preferred, log=log):
                summary["kernels"] += 1

    summary["chat_rows"] = migrate_chat_keys_to_stable(memory_store, log=log)
    migrate_hashes_to_storage_keys(log=log)
    summary["purged_slugs"] = purge_url_slug_duplicates(log=log)
    return summary
