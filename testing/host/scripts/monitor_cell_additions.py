#!/usr/bin/env python3

"""

Live terminal monitor for notebook cell structure changes (add/delete).



Uses only the in-memory cell_structure_observer + the latest *live* scrape file.

Does NOT merge persistent/legacy snapshots (avoids duplicate/noisy events).



Prerequisites:

  - host.py running (extension pushes NOTEBOOK_DATA → live JSON)

  - Notebook tab open in Chrome



Usage:

  python testing/host/scripts/monitor_cell_additions.py testing-ol

  python testing/host/scripts/monitor_cell_additions.py --url "https://www.kaggle.com/code/.../edit"

  python testing/host/scripts/monitor_cell_additions.py testing-ol --interval 0.1

"""



from __future__ import annotations



import argparse

import sys

import time

from datetime import datetime

from pathlib import Path



_REPO_ROOT = Path(__file__).resolve().parents[3]

if str(_REPO_ROOT) not in sys.path:

    sys.path.insert(0, str(_REPO_ROOT))



from testing.host.cell_structure_live import read_live_cells  # noqa: E402

from testing.host.cell_structure_observer import CellStructureTracker  # noqa: E402





def _ts() -> str:

    return datetime.now().strftime("%H:%M:%S")





def _resolve_url(arg: str) -> str:

    text = str(arg or "").strip().rstrip("/")

    if text.startswith("http"):

        return text

    from testing.host.cell_structure_live import _LIVE_ROOT, _read_json



    needles = [text.lower(), text.lower().replace("-", "_"), text.lower().replace("_", "-")]

    if _LIVE_ROOT.is_dir():

        for path in _LIVE_ROOT.glob("*.json"):

            data = _read_json(path) or {}

            tab = str(data.get("tabUrl") or "").lower()

            if any(n in tab for n in needles if n):

                return str(data.get("tabUrl") or "").strip().rstrip("/")

    raise SystemExit(

        f"Could not resolve notebook URL from {arg!r}. "

        "Open the notebook in Chrome (host running) or pass --url."

    )





def monitor(

    url: str,

    *,

    interval: float = 0.1,

    settle_reads: int = 2,

    list_existing: bool = False,

    verbose: bool = False,

) -> None:

    tracker = CellStructureTracker(settle_reads=max(1, settle_reads))

    primed = False



    print(f"Cell structure monitor (live-only) — {url}")

    print(f"Observer settle_reads={tracker.settle_reads} poll={interval}s  Ctrl+C to stop")

    print("─" * 72, flush=True)



    while True:

        cells, meta = read_live_cells(url)

        if not cells:

            if verbose:

                print(f"[{_ts()}] waiting for live scrape…", flush=True)

            time.sleep(interval)

            continue



        if not primed:

            tracker.reset(cells)

            primed = True

            indices = sorted(tracker.committed_indices)

            if list_existing:

                for cell in cells:

                    print(

                        f"[{_ts()}] CELL index={cell.index} type={cell.cell_type} "

                        f"total={len(cells)} (baseline) | {cell.input[:72] or '(empty)'}",

                        flush=True,

                    )

            else:

                span = (

                    str(indices)

                    if len(indices) <= 24

                    else f"{indices[0]}..{indices[-1]} ({len(indices)} cells)"

                )

                print(f"[{_ts()}] baseline {len(cells)} cell(s): {span}", flush=True)

                if verbose and meta.get("path"):

                    print(f"[{_ts()}] live file: {meta['path']}", flush=True)

            time.sleep(interval)

            continue



        events = tracker.observe(cells)

        for ev in events.additions:

            print(

                f"[{_ts()}] + CELL index={ev.index} type={ev.cell_type} "

                f"total={ev.total_cells} | {ev.input_preview}",

                flush=True,

            )

        for ev in events.deletions:

            print(

                f"[{_ts()}] - CELL index={ev.index} type={ev.cell_type} "

                f"total={ev.total_cells} deleted | was: {ev.input_preview}",

                flush=True,

            )

        if verbose and (events.additions or events.deletions):

            print(f"[{_ts()}]        live @ {meta.get('lastUpdated', '')[:19]}", flush=True)



        time.sleep(interval if not (events.additions or events.deletions) else min(interval, 0.08))





def main() -> int:

    parser = argparse.ArgumentParser(

        description="Detect notebook cell add/delete from live scrape (verification observer).",

    )

    parser.add_argument("notebook", nargs="?", default="", help="URL or slug (e.g. testing-ol)")

    parser.add_argument("--url", default="", help="Notebook /edit URL")

    parser.add_argument("--interval", type=float, default=0.1, help="Poll seconds (default 0.1)")

    parser.add_argument(

        "--settle-reads",

        type=int,

        default=3,

        help="Identical live reads required before accepting a change (default 3)",

    )

    parser.add_argument("--list-existing", action="store_true", help="Print baseline cells on start")

    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()



    url = (args.url or args.notebook or "").strip()

    if not url:

        parser.error("Provide notebook URL or short name")

    if not url.startswith("http"):

        url = _resolve_url(url)



    try:

        monitor(

            url,

            interval=max(0.05, float(args.interval)),

            settle_reads=max(1, int(args.settle_reads)),

            list_existing=args.list_existing,

            verbose=args.verbose,

        )

    except KeyboardInterrupt:

        print("\nStopped.", flush=True)

    return 0





if __name__ == "__main__":

    raise SystemExit(main())


