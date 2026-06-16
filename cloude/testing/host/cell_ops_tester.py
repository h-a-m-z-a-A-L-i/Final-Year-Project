import argparse
import asyncio
import shlex
from pathlib import Path

from playwright.async_api import async_playwright

CELL_SELECTOR = '[data-windowed-list-index="{index}"]'


async def _find_cell_locator(page, index: int):
    selector = CELL_SELECTOR.format(index=index)
    for frame in page.frames:
        try:
            locator = frame.locator(selector).first
            if await locator.count() > 0:
                return locator, frame.url
        except Exception:
            continue
    return None, None


async def _list_cells(page):
    seen = []
    for frame in page.frames:
        try:
            count = await frame.locator('[data-windowed-list-index]').count()
            if count == 0:
                continue
            for i in range(count):
                locator = frame.locator('[data-windowed-list-index]').nth(i)
                try:
                    idx = await locator.get_attribute('data-windowed-list-index')
                except Exception:
                    idx = None
                if idx is None:
                    continue
                seen.append((frame.url, idx))
        except Exception:
            continue
    return seen


async def _click_cell(page, index: int):
    locator, frame_url = await _find_cell_locator(page, index)
    if locator is None:
        return {"ok": False, "error": f"Cell index {index} not found."}

    try:
        await locator.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        await locator.click()
    except Exception as error:
        return {"ok": False, "error": str(error), "frame_url": frame_url}

    return {"ok": True, "index": index, "frame_url": frame_url}


async def _inspect_cell(page, index: int):
    locator, frame_url = await _find_cell_locator(page, index)
    if locator is None:
        return {"ok": False, "error": f"Cell index {index} not found."}

    try:
        info = await locator.evaluate(
            """(element) => {
                const text = (element.innerText || element.textContent || '').trim();
                const attrs = {};
                for (const name of element.getAttributeNames()) {
                    attrs[name] = element.getAttribute(name);
                }
                return {
                    tagName: element.tagName.toLowerCase(),
                    text: text.slice(0, 300),
                    attrs,
                };
            }"""
        )
    except Exception as error:
        return {"ok": False, "error": str(error), "frame_url": frame_url}

    return {"ok": True, "index": index, "frame_url": frame_url, "element": info}


async def _repl(page):
    print("Commands: click <index>, inspect <index>, list, url <new_url>, shot [path], quit")
    while True:
        try:
            raw = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return

        command = raw.strip()
        if not command:
            continue

        parts = shlex.split(command)
        action = parts[0].lower()

        if action in {"quit", "exit"}:
            return

        if action == "list":
            cells = await _list_cells(page)
            if not cells:
                print("No cells found.")
                continue
            for frame_url, idx in cells:
                print(f"frame={frame_url} index={idx}")
            continue

        if action == "click" and len(parts) >= 2:
            result = await _click_cell(page, int(parts[1]))
            print(result)
            continue

        if action == "inspect" and len(parts) >= 2:
            result = await _inspect_cell(page, int(parts[1]))
            print(result)
            continue

        if action == "url" and len(parts) >= 2:
            await page.goto(parts[1], wait_until="domcontentloaded")
            print(f"Loaded {page.url}")
            continue

        if action == "shot":
            out_path = Path(parts[1]) if len(parts) >= 2 else Path("cell_ops_tester.png")
            await page.screenshot(path=str(out_path), full_page=True)
            print(f"Saved {out_path}")
            continue

        print("Unknown command.")


async def main():
    parser = argparse.ArgumentParser(description="Notebook cell operation tester")
    parser.add_argument("--url", required=True, help="Notebook URL to open")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"], help="Browser engine")
    parser.add_argument("--command", default=None, help="Run one command and exit, e.g. 'click 0' or 'inspect 0'")
    args = parser.parse_args()

    async with async_playwright() as p:
        browser_type = getattr(p, args.browser)
        browser = await browser_type.launch(headless=args.headless)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(args.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        if args.command:
            parts = shlex.split(args.command)
            if not parts:
                print({"ok": False, "error": "Empty command."})
            else:
                action = parts[0].lower()
                if action == "click" and len(parts) >= 2:
                    print(await _click_cell(page, int(parts[1])))
                elif action == "inspect" and len(parts) >= 2:
                    print(await _inspect_cell(page, int(parts[1])))
                elif action == "list":
                    print(await _list_cells(page))
                else:
                    print({"ok": False, "error": "Unknown one-shot command."})
        else:
            await _repl(page)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
