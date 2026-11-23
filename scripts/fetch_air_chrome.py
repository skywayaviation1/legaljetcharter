#!/usr/bin/env python3
"""Download the FAA Air Operator Part 135 CSV through a real Chrome session.

Akamai rejects scripted HTTP clients (including curl with a Chrome user agent)
before the workbook responds. A normal Chrome load of the FAA page, then the
Tableau view, gets a browser session. The letter CSVs are then requested with
that session's cookies, from inside the page, one letter at a time.

Writes A.csv … Z.csv into the directory given as the first argument.
Exits non-zero, and writes nothing usable, if the host refuses the browser
or a slice is not the Part 135 sheet.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

AIR_PAGE = "https://www.faa.gov/av-info/air-operator-far-search"
TABLEAU_URL = (
    "https://explore.dot.gov/t/FAA/views/AVInfo_AirOperators/AirOperators"
    "?:embed=y&:toolbar=bottom&:showVizHome=no"
)
CSV_URL = "https://explore.dot.gov/t/FAA/views/AVInfo_AirOperators/Download.csv"
LETTERS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
PAUSE_SECONDS = 1.0


def chrome_path() -> str:
    candidates = [
        os.environ.get("CHROME_PATH") or "",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Google Chrome was not found. Set CHROME_PATH.")


def headed() -> bool:
    mode = os.environ.get("FAA_CHROME_HEADED", "auto")
    if mode == "1":
        return True
    if mode == "0":
        return False
    return bool(os.environ.get("DISPLAY"))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: fetch_air_chrome.py <output-dir>", file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed. pip install -r scripts/requirements.txt", file=sys.stderr)
        return 1

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chrome_path(),
            headless=not headed(),
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1400,1000",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 1000},
            locale="en-US",
            timezone_id="America/New_York",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = context.new_page()
        try:
            open_workbook(page)
            for index, letter in enumerate(LETTERS):
                if index:
                    time.sleep(PAUSE_SECONDS)
                text = fetch_letter(page, letter)
                (out_dir / f"{letter}.csv").write_text(text)
                print(f"Air Operator slice {letter}: {text.count(chr(10))} lines", file=sys.stderr)
        finally:
            browser.close()
    return 0


def open_workbook(page) -> None:
    print("Opening the FAA Air Operator page in Chrome…", file=sys.stderr)
    page.goto(AIR_PAGE, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1500)
    print("Opening the Air Operator workbook…", file=sys.stderr)
    response = page.goto(TABLEAU_URL, wait_until="domcontentloaded", timeout=120000)
    try:
        page.wait_for_function(
            """() => {
                const text = document.body ? document.body.innerText : '';
                const title = document.title || '';
                return text.includes('Data Updated')
                    || text.includes('Access Denied')
                    || title.includes('Access Denied');
            }""",
            timeout=60000,
        )
    except Exception as exc:
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        raise SystemExit(f"The Air Operator workbook did not finish loading ({exc}). Title: {title}") from exc
    body = ""
    try:
        body = page.inner_text("body")[:600]
    except Exception:
        body = ""
    status = response.status if response is not None else 0
    if status == 403 or "Access Denied" in body or "Access Denied" in (page.title() or ""):
        raise SystemExit(
            "Chrome was refused by the Air Operator host before the workbook loaded. "
            f"HTTP {status}. {body[:240]}"
        )
    if "Data Updated" not in body:
        raise SystemExit(
            "Chrome opened the Air Operator host but the FAA 'Data Updated' label was not there. "
            "Refusing to download slices."
        )


def fetch_letter(page, letter: str) -> str:
    url = f"{CSV_URL}?Operator%20FAR%20Parameter=135&Alphabet%20Parameter={letter}"
    result = page.evaluate(
        """async (url) => {
            const response = await fetch(url, {
                credentials: 'include',
                headers: { 'Accept': 'text/csv,text/plain,*/*' },
            });
            const text = await response.text();
            return { status: response.status, text };
        }""",
        url,
    )
    status = int(result.get("status") or 0)
    text = result.get("text") or ""
    if status != 200 or "Air_Operator_Name" not in text[:800] or "Access Denied" in text[:400]:
        raise SystemExit(
            f"Air Operator slice {letter} was not a CSV (HTTP {status}). "
            f"{text[:180]}"
        )
    return text


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Air Operator browser fetch failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
