import sys
from pathlib import Path
import tempfile
import json

HERE = Path(__file__).resolve().parent
HOST_DIR = HERE.parent
sys.path.insert(0, str(HOST_DIR))

import persistence


def test_atomic_write_and_read():
    tmpdir = Path(tempfile.mkdtemp())
    fpath = tmpdir / "test.json"
    payload = {"a": 1, "cells": [{"index": 1}]}
    persistence.atomic_write_json(fpath, payload)
    with fpath.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data == payload


if __name__ == '__main__':
    test_atomic_write_and_read()
    print('persistence test OK')
