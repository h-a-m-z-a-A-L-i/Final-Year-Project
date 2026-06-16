"""1-based cell indices for JSON, tools, and UI; DOM uses 0-based windowed-list indices."""


def dom_to_app(dom_index: int) -> int:
    return int(dom_index) + 1


def app_to_dom(app_index: int) -> int:
    return int(app_index) - 1


def is_valid_app_index(app_index: int) -> bool:
    try:
        return int(app_index) >= 1
    except Exception:
        return False


def normalize_notebook_cells(cells: list) -> list:
    """Upgrade legacy 0-based stored indices to 1-based in place."""
    if not cells:
        return cells
    nums: list[int] = []
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("index") is None:
            continue
        try:
            nums.append(int(cell["index"]))
        except Exception:
            continue
    if not nums or min(nums) != 0:
        return cells
    for i, cell in enumerate(cells):
        if isinstance(cell, dict):
            cell["index"] = i + 1
    return cells
