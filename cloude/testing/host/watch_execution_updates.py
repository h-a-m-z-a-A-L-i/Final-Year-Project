import re
import time
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "data" / "logs" / "update_exec.log"
SUCCESS_RE = re.compile(r"SUCCESS updated cell (?P<cell>\d+) order=(?P<order>\d+) time=(?P<time>\S+)")


def tail_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    while not path.exists():
        time.sleep(0.1)

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if not line:
                time.sleep(0.05)
                continue

            match = SUCCESS_RE.search(line)
            if match:
                cell = match.group("cell")
                order = match.group("order")
                exec_time = match.group("time")
                title = f"Cell executed at {exec_time}"
                print(f"cell {cell}: order={order}, title='{title}'", flush=True)


if __name__ == "__main__":
    tail_log(LOG_PATH)