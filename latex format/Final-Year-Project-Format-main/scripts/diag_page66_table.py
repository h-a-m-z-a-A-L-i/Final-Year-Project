"""Build thesis and log Table A.1 / page-66 layout metrics for debug session 7624ce."""
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT.parents[1] / "debug-7624ce.log"
BUILD = ROOT / "build"
TEX = "_fyp-ch1-preview.tex"
APPENDIX = ROOT / "chapters" / "appendix-benchmarks.tex"


def emit(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
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


def parse_table_overfull(log_text: str) -> list[dict]:
    hits = []
    for m in re.finditer(
        r"Overfull \\hbox \(([\d.]+)pt too wide\).*?appendix-benchmarks\.tex.*?lines (\d+)--(\d+)",
        log_text,
        re.DOTALL,
    ):
        hits.append({"pt": float(m.group(1)), "line_start": int(m.group(2)), "line_end": int(m.group(3))})
    # also catch overfull lines attributed only by line number near table rows
    for m in re.finditer(
        r"Overfull \\hbox \(([\d.]+)pt too wide\).*?lines (4[0-9]|50)--(4[0-9]|50)",
        log_text,
    ):
        hits.append({"pt": float(m.group(1)), "line_start": int(m.group(2)), "line_end": int(m.group(3))})
    return hits


def parse_filename_overfull(log_text: str) -> list[dict]:
    hits = []
    for m in re.finditer(
        r"Overfull \\hbox \(([\d.]+)pt too wide\).*?lines (\d+)--\2\s*\n\[\]\|\T1/lmtt/m/n/\d+ ([^\|]+)",
        log_text,
    ):
        hits.append({"pt": float(m.group(1)), "line": int(m.group(2)), "filename": m.group(3).strip()})
    return hits


def page66_audit(pdf_path: Path) -> dict:
    from pypdf import PdfReader

    r = PdfReader(str(pdf_path))
    idx = 83  # printed page 66 when front matter = 18 pages
    t66 = r.pages[idx].extract_text() or ""
    t67 = r.pages[idx + 1].extract_text() or ""
    return {
        "pdf_page": idx + 1,
        "printed_page_footer": "66" in t66.split()[-3:],
        "has_table_a1": "Table A.1" in t66 or "113620421" in t66,
        "a32_on_66": "A.3.2" in t66,
        "code_correctness_on_66": "notebook variable names" in t66,
        "placement_on_66": "Placement accuracy" in t66,
        "placement_on_67": "Placement accuracy" in t67,
        "code_contract_on_66": "zero browser tool dispatches" in t66,
        "a33_on_67": "A.3.3" in t67,
        "a32_on_67": "A.3.2" in t67,
        "excluded_from_thesis_on_66": "excluded from thesis" in t66,
        "total_pages": len(r.pages),
    }


def main() -> None:
    import sys

    run_id = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    src = APPENDIX.read_text(encoding="utf-8")
    emit(
        run_id,
        "H1",
        "diag_page66_table.py:main",
        "table source snapshot",
        {
            "uses_p_columns": "p{" in src.split("tab:app-notebooks")[1].split("\\end{table}")[0],
            "uses_l_columns": "|l|" in src or "|l|" in src.replace(" ", ""),
            "uses_resizebox": "\\resizebox" in src,
            "uses_enlargethispage": "\\enlargethispage" in src.split("tab:app-notebooks")[0],
        },
    )

    for _ in range(2):
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory=build", TEX],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    log_text = (BUILD / "_fyp-ch1-preview.log").read_text(encoding="utf-8", errors="replace")
    filename_overfull = []
    for m in re.finditer(
        r"Overfull \\hbox \(([\d.]+)pt too wide\).*?lines (\d+)--\2",
        log_text,
    ):
        line = int(m.group(2))
        if 45 <= line <= 52:
            snippet = log_text[m.start() : m.end() + 120]
            filename_overfull.append(
                {"pt": float(m.group(1)), "line": line, "snippet": snippet[:200]}
            )

    emit(
        run_id,
        "H1",
        "diag_page66_table.py:log",
        "filename cell overfull warnings",
        {"count": len(filename_overfull), "entries": filename_overfull[:8]},
    )

    emit(
        run_id,
        "H2",
        "diag_page66_table.py:log",
        "p-column constrains tt but tt ignores width",
        {"confirmed": any(e["pt"] > 20 for e in filename_overfull)},
    )

    pdf = BUILD / "_fyp-ch1-preview.pdf"
    audit = page66_audit(pdf)
    emit(run_id, "H3", "diag_page66_table.py:pdf", "page 66 pagination audit", audit)

    print(json.dumps({"run_id": run_id, "filename_overfull": filename_overfull, "audit": audit}, indent=2))


if __name__ == "__main__":
    main()
