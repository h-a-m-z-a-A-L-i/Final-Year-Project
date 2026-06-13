"""Fetch and cache Kaggle kernel metadata (stable id_no) via the official API."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    from .config import KERNEL_METADATA_DIR, KERNEL_SLUG_INDEX_PATH, KAGGLE_KEY, KAGGLE_USERNAME
except Exception:
    from config import KERNEL_METADATA_DIR, KERNEL_SLUG_INDEX_PATH, KAGGLE_KEY, KAGGLE_USERNAME

_CLIENT_LOCK = threading.Lock()
_SLUG_INDEX_LOCK = threading.Lock()


def parse_kaggle_edit_url(url: str) -> tuple[str, str] | None:
    """Return (owner, slug) for a Kaggle notebook /edit URL."""
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        path = urlparse(raw).path or ""
    except Exception:
        return None
    parts = [p for p in path.split("/") if p]
    try:
        code_idx = parts.index("code")
    except ValueError:
        return None
    if len(parts) < code_idx + 3:
        return None
    if parts[-1].lower() != "edit":
        return None
    owner = parts[code_idx + 1].strip()
    slug = parts[code_idx + 2].strip()
    if not owner or not slug:
        return None
    return owner, slug


def kernel_ref(owner: str, slug: str) -> str:
    return f"{owner}/{slug}"


def kaggle_edit_url(owner: str, slug: str) -> str:
    return f"https://www.kaggle.com/code/{owner}/{slug}/edit"


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


def _load_slug_index() -> dict[str, int]:
    with _SLUG_INDEX_LOCK:
        raw = _read_json(KERNEL_SLUG_INDEX_PATH)
        out: dict[str, int] = {}
        for key, value in raw.items():
            try:
                out[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return out


def _save_slug_index(index: dict[str, int]) -> None:
    with _SLUG_INDEX_LOCK:
        _write_json(KERNEL_SLUG_INDEX_PATH, {k: int(v) for k, v in sorted(index.items())})


def _metadata_path(id_no: int) -> Path:
    return KERNEL_METADATA_DIR / f"{int(id_no)}.json"


def _extract_id_no(payload: dict) -> int | None:
    for key in ("id_no", "id"):
        if key not in payload:
            continue
        try:
            value = int(payload[key])
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _normalize_cached_record(record: dict, *, owner: str, slug: str, url: str | None = None) -> dict | None:
    id_no = _extract_id_no(record)
    if id_no is None:
        return None
    ref = str(record.get("id") or record.get("ref") or kernel_ref(owner, slug)).strip()
    out = dict(record)
    out["id_no"] = id_no
    out["owner"] = str(record.get("owner") or record.get("user") or owner).strip() or owner
    out["slug"] = str(record.get("slug") or slug).strip() or slug
    out["id"] = ref
    urls = [str(u).strip() for u in (record.get("urls") or []) if str(u).strip()]
    if url:
        normalized = url.split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()
        if normalized and normalized not in urls:
            urls.append(normalized)
    out["urls"] = urls
    return out


def _cache_metadata(record: dict) -> dict:
    id_no = int(record["id_no"])
    path = _metadata_path(id_no)
    existing = _read_json(path)
    merged_urls = list(dict.fromkeys((existing.get("urls") or []) + (record.get("urls") or [])))
    record = {**existing, **record, "urls": merged_urls, "updatedAt": datetime.now(timezone.utc).isoformat()}
    _write_json(path, record)

    index = _load_slug_index()
    ref = kernel_ref(record["owner"], record["slug"])
    index[ref] = id_no
    for alt_slug in record.get("historical_slugs") or []:
        index[kernel_ref(record["owner"], str(alt_slug))] = id_no
    _save_slug_index(index)
    return record


def load_cached_metadata(owner: str, slug: str) -> dict | None:
    index = _load_slug_index()
    id_no = index.get(kernel_ref(owner, slug))
    if id_no is None:
        return None
    record = _read_json(_metadata_path(id_no))
    if not record:
        return None
    return _normalize_cached_record(record, owner=owner, slug=slug)


def import_local_kernel_metadata_file(path: Path, *, url: str | None = None) -> dict | None:
    """Import a kernel-metadata.json file (e.g. from `kaggle kernels pull -m`)."""
    data = _read_json(path)
    if not data:
        return None
    ref = str(data.get("id") or "").strip()
    owner = str(data.get("owner") or data.get("user") or "").strip()
    slug = str(data.get("slug") or "").strip()
    if ref and "/" in ref and (not owner or not slug):
        owner, slug = ref.split("/", 1)
    if not owner or not slug:
        parsed = parse_kaggle_edit_url(url or "")
        if parsed:
            owner, slug = parsed
    if not owner or not slug:
        return None
    normalized = _normalize_cached_record(data, owner=owner, slug=slug, url=url)
    if not normalized:
        return None
    return _cache_metadata(normalized)


def _credentials_ready() -> bool:
    return bool(KAGGLE_USERNAME and KAGGLE_KEY)


def _apply_credentials() -> None:
    if KAGGLE_USERNAME:
        os.environ.setdefault("KAGGLE_USERNAME", KAGGLE_USERNAME)
    if KAGGLE_KEY:
        os.environ.setdefault("KAGGLE_KEY", KAGGLE_KEY)


def fetch_kernel_metadata_from_api(owner: str, slug: str, *, url: str | None = None, log=None) -> dict | None:
    if not _credentials_ready():
        if log:
            log("[kaggle] Skipping metadata fetch: KAGGLE_USERNAME/KAGGLE_KEY not set in .env")
        return None

    _apply_credentials()
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
    except Exception as exc:
        if log:
            log(f"[kaggle] API import failed: {exc}")
        return None

    with _CLIENT_LOCK:
        try:
            api = KaggleApi()
            api.authenticate()
            request = ApiGetKernelRequest()
            request.user_name = owner
            request.kernel_slug = slug
            with api.build_kaggle_client() as kaggle:
                response = kaggle.kernels.kernels_api_client.get_kernel(request)
            meta = response.metadata
            if meta is None:
                return None
            record = {
                "id_no": int(meta.id),
                "id": str(meta.ref or kernel_ref(owner, slug)),
                "owner": owner,
                "slug": str(meta.slug or slug),
                "title": str(meta.title or ""),
                "language": str(meta.language or ""),
                "kernel_type": str(meta.kernel_type or ""),
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "urls": [],
            }
            if url:
                record["urls"] = [url]
            else:
                record["urls"] = [kaggle_edit_url(owner, record["slug"])]
            cached = _cache_metadata(record)
            if log:
                log(f"[kaggle] Resolved {owner}/{slug} -> id_no={cached['id_no']}")
            return cached
        except Exception as exc:
            if log:
                log(f"[kaggle] Metadata fetch failed for {owner}/{slug}: {exc}")
            return None


def resolve_kernel_id_for_url(url: str, *, log=None, allow_fetch: bool = True) -> int | None:
    parsed = parse_kaggle_edit_url(url)
    if not parsed:
        return None
    owner, slug = parsed

    cached = load_cached_metadata(owner, slug)
    if cached:
        return int(cached["id_no"])

    if not allow_fetch:
        return None
    fetched = fetch_kernel_metadata_from_api(owner, slug, url=url, log=log)
    if fetched:
        return int(fetched["id_no"])
    return None


def scan_import_local_metadata_files(search_dirs: list[Path] | None = None) -> int:
    """Import any kernel-metadata.json files found under common project folders."""
    roots = search_dirs or [
        Path(__file__).resolve().parents[2],
        KERNEL_METADATA_DIR,
    ]
    seen: set[str] = set()
    imported = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("kernel-metadata.json"):
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            if import_local_kernel_metadata_file(path):
                imported += 1
    return imported
