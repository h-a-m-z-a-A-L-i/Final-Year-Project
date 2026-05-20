import json
import os
import time
from pathlib import Path
from typing import Tuple, List

def append_jsonl(path: Path, payload: dict, flush: bool = True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    # Open in binary append mode to allow fsync
    with open(path, "ab") as f:
        f.write(data)
        if flush:
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass

def read_jsonl(path: Path) -> List[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out

def tail_from(path: Path, offset: int) -> Tuple[int, List[dict]]:
    """Read from byte offset and return new offset and parsed JSON lines."""
    path = Path(path)
    if not path.exists():
        return 0, []
    out = []
    with path.open("r", encoding="utf-8") as f:
        f.seek(offset)
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except Exception:
                continue
        new_offset = f.tell()
    return new_offset, out

def wait_for_request_result(request_id: str, path: Path, arg3, arg4):
    """
    Wait for a specific request ID to appear in the JSONL queue.
    Supports both caller signatures:
      Type A: (request_id, path, since_size, timeout_seconds)
      Type B: (request_id, path, timeout_seconds, before_size)
    """
    # Detect signatures: since_size is 0 in Type A.
    if arg3 == 0:
        since_size = 0
        timeout_seconds = float(arg4)
    else:
        timeout_seconds = float(arg3)
        since_size = int(arg4)

    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        try:
            _, events = tail_from(path, since_size)
            for ev in events:
                if ev.get("requestId") == request_id:
                    return ev
        except Exception:
            pass
        time.sleep(0.2)
    return None
