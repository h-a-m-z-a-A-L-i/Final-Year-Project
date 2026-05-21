try:
    from .prompt_utils import _extract_cell_number
except Exception:
    from prompt_utils import _extract_cell_number


extract_cell_number = _extract_cell_number


def run_tests():
    samples = [
        "explain cell 1",
        "explain cell1",
        "explain cell#2",
        "please explain (cell 3)",
        "check [cell 4] dependencies",
        "CELL 5",
        "what is in cell 0",
        "no cell here",
        "cell -2",
    ]

    print("Prompt -> extracted_cell")
    print("-" * 40)
    for s in samples:
        print(f"{s!r} -> {extract_cell_number(s)}")


if __name__ == "__main__":
    run_tests()
