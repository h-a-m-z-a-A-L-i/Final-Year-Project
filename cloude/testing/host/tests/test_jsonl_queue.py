import sys
from pathlib import Path
import tempfile
import json

HERE = Path(__file__).resolve().parent
HOST_DIR = HERE.parent
sys.path.insert(0, str(HOST_DIR))

import jsonl_queue


def test_append_read_tail():
    tmpdir = Path(tempfile.mkdtemp())
    fpath = tmpdir / "queue.jsonl"
    p1 = {"requestId": "r1", "ok": True}
    p2 = {"requestId": "r2", "ok": True}
    jsonl_queue.append_jsonl(fpath, p1)
    jsonl_queue.append_jsonl(fpath, p2)
    all_items = jsonl_queue.read_jsonl(fpath)
    assert any(i.get("requestId") == "r1" for i in all_items)
    assert any(i.get("requestId") == "r2" for i in all_items)

    # test tail_from
    offset = 0
    new_offset, items = jsonl_queue.tail_from(fpath, offset)
    assert len(items) == 2

    # test wait_for_request_result
    res = jsonl_queue.wait_for_request_result("r2", fpath, 0, 1.0)
    assert res and res.get("requestId") == "r2"


if __name__ == '__main__':
    test_append_read_tail()
    print('jsonl_queue test OK')
