import sys
from pathlib import Path
import tempfile
import threading
import time
import json

HERE = Path(__file__).resolve().parent
HOST_DIR = HERE.parent
sys.path.insert(0, str(HOST_DIR))

import persistence
import update_cell_execution


def scraper_writer(path: Path, url: str, iterations=10):
    for i in range(iterations):
        data = {
            "tabUrl": url,
            "title": "notebook",
            "lastUpdated": time.strftime('%Y-%m-%dT%H:%M:%S'),
            "cells": [
                {"index": 1, "input": "print(1)", "output": "", "execution_order": None, "execution_title": ""}
            ]
        }
        persistence.atomic_write_json(path / persistence.get_safe_filename(url), data)
        time.sleep(0.01)


def updater(path: Path, url: str, iterations=10):
    # point update_cell_execution to temp scraped dir
    update_cell_execution.SCRAPED_DIR = path
    for i in range(iterations):
        update_cell_execution.update_cell_execution(1, url, exec_timestamp_ms=int(time.time() * 1000), exec_order=i)
        time.sleep(0.02)


def test_concurrent_writes():
    tmpdir = Path(tempfile.mkdtemp())
    url = "https://example.com/code/owner/notebook"
    # Start threads
    t1 = threading.Thread(target=scraper_writer, args=(tmpdir, url, 30))
    t2 = threading.Thread(target=updater, args=(tmpdir, url, 30))
    t1.start(); t2.start()
    t1.join(); t2.join()

    fpath = tmpdir / persistence.get_safe_filename(url)
    with fpath.open('r', encoding='utf-8') as f:
        data = json.load(f)
    # Basic sanity checks
    assert isinstance(data, dict)
    assert data.get('cells') and isinstance(data['cells'], list)
    cell = data['cells'][0]
    assert 'execution_title' in cell
    print('concurrency test OK')


if __name__ == '__main__':
    test_concurrent_writes()
