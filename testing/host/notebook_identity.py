import json
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    from .config import DATA_ROOT
    from .notebook_data_handler import _normalized_url
except Exception:
    from config import DATA_ROOT
    from notebook_data_handler import _normalized_url

NOTEBOOK_REGISTRY_PATH = DATA_ROOT / "meta" / "notebook_registry.json"
_REGISTRY_LOCK = threading.Lock()


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_notebook_key(notebook_id) -> str | None:
    try:
        nid = int(notebook_id)
    except (TypeError, ValueError):
        return None
    if nid <= 0:
        return None
    return f"kaggle:kernel:{nid}"


def _load_registry() -> dict:
    reg = _read_json(NOTEBOOK_REGISTRY_PATH)
    reg.setdefault("url_to_key", {})
    reg.setdefault("kernels", {})
    return reg


def _save_registry(reg: dict) -> None:
    _write_json(NOTEBOOK_REGISTRY_PATH, reg)


def migrate_notebook_keys(memory_store, source_keys: list[str], target_key: str, *, log=None) -> int:
    if not memory_store or not target_key:
        return 0
    migrated = 0
    seen = set()
    for raw in source_keys:
        key = str(raw or "").strip()
        if not key or key == target_key or key in seen:
            continue
        seen.add(key)
        count = memory_store.migrate_notebook_key(key, target_key)
        if count and log:
            log(f"[notebook_identity] Migrated {count} chat rows: {key} -> {target_key}")
        migrated += int(count or 0)
    return migrated


def register_notebook_identity(
    url: str,
    notebook_id,
    *,
    memory_store=None,
    log=None,
    old_url: str | None = None,
) -> dict:
    """Register url -> stable kernel id and migrate chat rows to the stable key."""
    normalized = _normalized_url(url or "")
    normalized_old = _normalized_url(old_url or "") if old_url else ""
    stable = stable_notebook_key(notebook_id)
    result = {
        "url": normalized,
        "notebookId": int(notebook_id) if stable else None,
        "notebookKey": stable or normalized,
        "migratedRows": 0,
    }
    if not normalized:
        return result

    with _REGISTRY_LOCK:
        reg = _load_registry()
        url_to_key = reg["url_to_key"]
        kernels = reg["kernels"]

        migration_sources = [normalized]
        if normalized_old and normalized_old != normalized:
            migration_sources.append(normalized_old)

        if stable:
            entry = kernels.setdefault(
                stable,
                {
                    "notebookId": int(notebook_id),
                    "urls": [],
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
            urls = entry.setdefault("urls", [])
            for candidate in migration_sources:
                if candidate and candidate not in urls:
                    urls.append(candidate)
                url_to_key[candidate] = stable
            entry["updatedAt"] = datetime.now(timezone.utc).isoformat()
            result["notebookKey"] = stable

            prior_keys = set()
            for candidate in migration_sources:
                prior_keys.add(candidate)
            result["migratedRows"] = migrate_notebook_keys(
                memory_store,
                sorted(prior_keys),
                stable,
                log=log,
            )
        else:
            url_to_key.setdefault(normalized, normalized)
            if normalized_old and normalized_old != normalized:
                old_mapped = url_to_key.get(normalized_old)
                if old_mapped:
                    url_to_key[normalized] = old_mapped
                    result["notebookKey"] = old_mapped
                    result["migratedRows"] = migrate_notebook_keys(
                        memory_store,
                        [normalized_old, normalized],
                        old_mapped,
                        log=log,
                    )

        _save_registry(reg)

    return result


def resolve_history_key(
    url: str,
    notebook_id=None,
    *,
    memory_store=None,
    log=None,
) -> str:
    """Return the notebook key used for chat memory (stable kernel id when known)."""
    normalized = _normalized_url(url or "")
    if not normalized:
        return ""

    resolved_id = notebook_id
    if resolved_id is None:
        try:
            from .kaggle_kernel_client import resolve_kernel_id_for_url
        except Exception:
            from kaggle_kernel_client import resolve_kernel_id_for_url
        resolved_id = resolve_kernel_id_for_url(normalized, log=log)

    stable = stable_notebook_key(resolved_id)
    if stable:
        info = register_notebook_identity(
            normalized,
            resolved_id,
            memory_store=memory_store,
            log=log,
        )
        return info.get("notebookKey") or stable

    with _REGISTRY_LOCK:
        reg = _load_registry()
        mapped = reg.get("url_to_key", {}).get(normalized)
        if mapped:
            return mapped
    return normalized


def resolve_notebook_identity(
    url: str,
    notebook_id=None,
    *,
    memory_store=None,
    log=None,
) -> dict:
    """Resolve url + stable kernel id + chat key for UI/host messages."""
    normalized = _normalized_url(url or "")
    key = resolve_history_key(
        normalized,
        notebook_id,
        memory_store=memory_store,
        log=log,
    )
    parsed_id = None
    if key.startswith("kaggle:kernel:"):
        try:
            parsed_id = int(key.split(":", 2)[2])
        except (IndexError, ValueError):
            parsed_id = None
    return {
        "url": normalized,
        "notebookId": parsed_id,
        "notebookKey": key or normalized,
    }


def handle_notebook_url_changed(
    old_url: str,
    new_url: str,
    notebook_id=None,
    *,
    memory_store=None,
    log=None,
) -> dict:
    resolved_id = notebook_id
    if resolved_id is None:
        try:
            from .kaggle_kernel_client import resolve_kernel_id_for_url
        except Exception:
            from kaggle_kernel_client import resolve_kernel_id_for_url
        resolved_id = resolve_kernel_id_for_url(_normalized_url(new_url or ""), log=log)
    return register_notebook_identity(
        new_url,
        resolved_id,
        memory_store=memory_store,
        log=log,
        old_url=old_url,
    )
