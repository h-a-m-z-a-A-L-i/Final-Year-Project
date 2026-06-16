"""Audit printed-page overflow for thesis pages (debug session 7624ce)."""
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT.parents[1] / "debug-7624ce.log"
BUILD = ROOT / "build"
TEX = "_fyp-ch1-preview.tex"


def emit(run_id, hypothesis_id, location, message, data):
    entry = {
        "sessionId": "7624ce",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def printed_to_pdf_index(printed: int) -> int:
    return printed + 18 - 1


def page_overfull(log_text: str, printed: int) -> list:
    # crude: collect all overfull with pt > 5 before next [page] marker near target
    hits = []
    target = f"[{printed}]"
    idx = log_text.find(target)
    if idx < 0:
        return hits
    window = log_text[max(0, idx - 4000): idx + 200]
    for m in re.finditer(r"Overfull \\hbox \(([\d.]+)pt too wide\).*?lines (\d+)--(\d+)", window, re.DOTALL):
        pt = float(m.group(1))
        if pt >= 5:
            hits.append({"pt": pt, "lines": f"{m.group(2)}-{m.group(3)}"})
    return sorted(hits, key=lambda x: -x["pt"])


def audit_page(pdf_path: Path, printed: int) -> dict:
    from pypdf import PdfReader

    r = PdfReader(str(pdf_path))
    idx = printed_to_pdf_index(printed)
    text = r.pages[idx].extract_text() or ""
    return {"printed": printed, "pdf_page": idx + 1, "chars": len(text), "preview": text[:500]}


def main():
    import sys

    run_id = sys.argv[1] if len(sys.argv) > 1 else "audit"
    pages = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else [33, 34, 41, 69]

    for _ in range(2):
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory=build", TEX],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )

    log_text = (BUILD / "_fyp-ch1-preview.log").read_text(encoding="utf-8", errors="replace")
    pdf = BUILD / "_fyp-ch1-preview.pdf"

    for printed in pages:
        overfull = page_overfull(log_text, printed)
        info = audit_page(pdf, printed)
        emit(run_id, "H-overflow", "diag_overflow_pages.py", f"page {printed} overfull", {"printed": printed, "overfull": overfull[:6]})
        emit(run_id, "H-page", "diag_overflow_pages.py", f"page {printed} snapshot", info)
        print(f"PAGE {printed}: top overfull = {overfull[:3]}")

    # extract single-page pdfs
    from pypdf import PdfReader, PdfWriter

    r = PdfReader(str(pdf))
    for printed in pages:
        w = PdfWriter()
        w.add_page(r.pages[printed_to_pdf_index(printed)])
        out = BUILD / f"page{printed}-fixed.pdf"
        with out.open("wb") as f:
            w.write(f)


if __name__ == "__main__":
    main()
