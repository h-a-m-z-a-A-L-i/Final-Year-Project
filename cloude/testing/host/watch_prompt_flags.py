import re
import time
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "data" / "logs" / "host.log"
PATTERN = re.compile(r"PROMPT_SIGNAL\s+cell=(?P<cell>\d+|\?)\s+text=(?P<text>.*)$")


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

            match = PATTERN.search(line)
            if match:
                cell = match.group("cell")
                text = match.group("text").strip()
                print(f"cell {cell}: {text}", flush=True)


if __name__ == "__main__":
    tail_log(LOG_PATH)
