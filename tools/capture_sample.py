"""Pobiera strone wynikow AZair i zapisuje raw HTML + kazdy div.result osobno.

Uzycie:
    python tools/capture_sample.py 'https://www.azair.eu/azfin.php?...'

Wynik:
    tests/fixtures/azair_full.html         - cala strona
    tests/fixtures/azair_result_01.html    - pierwszy div.result
    tests/fixtures/azair_result_02.html    - drugi div.result
    ...
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def capture(url, max_results=10):
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_selector("div.result", timeout=35000)
        except Exception:
            page.wait_for_timeout(4000)

        # Cala strona
        full_html = page.content()
        full_path = FIXTURES_DIR / "azair_full.html"
        full_path.write_text(full_html, encoding="utf-8")
        print(f"zapisano: {full_path} ({len(full_html)} znakow)")

        # Kazdy div.result osobno
        elements = page.locator("div.result").all()[:max_results]
        for idx, element in enumerate(elements, start=1):
            outer = element.evaluate("e => e.outerHTML")
            target = FIXTURES_DIR / f"azair_result_{idx:02d}.html"
            target.write_text(outer, encoding="utf-8")
            print(f"zapisano: {target} ({len(outer)} znakow)")

        browser.close()
        print(f"\nLacznie zapisano {len(elements)} ofert.")


def main():
    if len(sys.argv) < 2:
        print("Uzycie: python tools/capture_sample.py 'URL'")
        sys.exit(1)
    capture(sys.argv[1].strip())


if __name__ == "__main__":
    main()
