"""
screener.py — US Growth Stock Screener
Navigates stockanalysis.com, applies 7 filters, extracts results,
and generates screener.html + screener.json in the output/ folder.

Usage:
    python screener.py
"""

import json
import os
import time
import re
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import THRESHOLDS, EXCLUDE_DIVIDENDS, SITE_PRESETS

OUTPUT_DIR = Path(__file__).parent.parent / "output"
DATA_DIR = OUTPUT_DIR / "data"
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

SCREENER_URL = "https://stockanalysis.com/stocks/screener/"

FILTERS = SITE_PRESETS


def dismiss_cookies(page):
    try:
        page.click("text=Manage options", timeout=4000)
        time.sleep(0.5)
        page.click("text=Confirm choices", timeout=4000)
        time.sleep(1)
        print("  Cookie banner dismissed.")
    except PWTimeout:
        pass


def add_and_configure_filters(page):
    """Check each filter checkbox, then apply the preset button for each."""
    print("  Opening filter panel...")
    page.click("text=Add Filters", timeout=10000)
    time.sleep(2)

    for cb_id, label, _ in FILTERS:
        try:
            page.locator(f"input#{cb_id}").first.click(timeout=5000)
            time.sleep(0.3)
            print(f"    Checked: {label}")
        except Exception as e:
            print(f"    Warning: could not check '{label}': {e}")

    page.keyboard.press("Escape")
    time.sleep(1)
    print("  Filters added. Applying presets...")

    for _, label, preset in FILTERS:
        if preset is None:
            print(f"    Skip preset: {label} (client-side only)")
            continue
        try:
            row = page.locator("div.hide-scroll").filter(has_text=label).first
            row_container = row.locator(
                "xpath=ancestor::div[contains(@class,'flex') and contains(@class,'justify-between')]"
            ).first
            any_btn = row_container.locator("button[aria-haspopup='menu']").first
            any_btn.click()
            time.sleep(0.6)

            menu = page.locator("[role='menu']").first
            menu.locator("button").filter(has_text=preset).first.click(timeout=4000)
            time.sleep(0.8)
            print(f"    Set: {label} → {preset}")
        except Exception as e:
            print(f"    Warning: could not set '{label}': {e}")

    # Wait for table to update
    time.sleep(2)
    try:
        count = page.locator("text=/\\d+ matches/").first.inner_text()
        print(f"  Server-side matches: {count}")
    except Exception:
        pass


def set_rows_per_page(page, n=100):
    """Click the rows-per-page button and select n rows from the dropdown."""
    try:
        btn = page.locator("button").filter(has_text=re.compile(r"\d+\s*rows", re.IGNORECASE)).first
        btn.click()
        time.sleep(0.6)
        page.locator("[role='menu'] button, [role='listbox'] button").filter(
            has_text=re.compile(f"^{n}", re.IGNORECASE)
        ).first.click(timeout=3000)
        time.sleep(1)
        print(f"  Rows per page set to {n}.")
    except Exception as e:
        print(f"  Warning: could not set rows per page: {e}")


def parse_num(val):
    """Parse a value like '2.5B', '300M', '1.2T', '15.3%', '123' to a float."""
    if not val or val == "N/A":
        return None
    s = val.replace(",", "").replace("%", "").strip()
    m = re.search(r"([-\d.]+)([BTMK]?)", s)
    if not m:
        return None
    n = float(m.group(1))
    suffix = m.group(2).upper()
    if suffix == "T": return n * 1_000_000
    if suffix == "B": return n * 1_000
    if suffix == "K": return n / 1_000
    return n


def apply_client_filters(stocks):
    """Apply exact numeric thresholds from config.THRESHOLDS."""
    filtered = []
    for s in stocks:
        keep = True
        for field, threshold in THRESHOLDS.items():
            val = parse_num(s.get(field, ""))
            if val is None or val < threshold:
                keep = False
                break
        if keep:
            filtered.append(s)
    return filtered


def extract_table_page(page):
    rows = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('table tbody tr').forEach(r => {
            const c = r.querySelectorAll('td');
            if (c.length >= 8) {
                results.push({
                    ticker:        c[0]?.innerText?.trim() || '',
                    name:          c[1]?.innerText?.trim() || '',
                    market_cap:    c[2]?.innerText?.trim() || '',
                    price:         c[3]?.innerText?.trim() || '',
                    revenue_growth:c[5]?.innerText?.trim() || '',
                    avg_volume:    c[6]?.innerText?.trim() || '',
                    eps_growth:    c[7]?.innerText?.trim() || '',
                    eps_next_year: c[8]?.innerText?.trim() || '',
                    high_52w_chg:  c[9]?.innerText?.trim() || '',
                    _all: Array.from(c).map((x, i) => i + ':' + x.innerText.trim()),
                });
            }
        });
        return results;
    }""")
    return rows


def scrape_all_pages(page):
    all_rows = []
    page_num = 1
    while True:
        print(f"  Extracting page {page_num}...")
        rows = extract_table_page(page)
        all_rows.extend(rows)

        # Try next page
        next_btn = page.locator("button:has-text('Next'), a:has-text('Next')").last
        if next_btn.count() == 0 or not next_btn.is_enabled():
            break
        try:
            next_btn.click(timeout=5000)
            time.sleep(1.5)
            page_num += 1
        except PWTimeout:
            break

    return all_rows


def run_screener():
    print("\n=== Stock Screener ===")
    print(f"Target: {SCREENER_URL}\n")

    import shutil
    chromium_path = (
        os.environ.get("CHROMIUM_PATH")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    launch_kwargs = {"headless": True, "args": [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--single-process",
    ]}
    if chromium_path:
        launch_kwargs["executable_path"] = chromium_path
    print(f"  Chromium: {chromium_path or 'playwright default'}", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()

        print("Step 1 — Loading screener...")
        page.goto(SCREENER_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("table tbody tr", timeout=30000)
        dismiss_cookies(page)

        print("Step 2 — Applying filters...")
        add_and_configure_filters(page)

        print("Step 3 — Extracting data (default page size to preserve filters)...")
        stocks = scrape_all_pages(page)

        browser.close()

    print(f"  {len(stocks)} stocks extracted from server-filtered results.")
    for s in stocks:
        s.pop('_all', None)
    stocks = apply_client_filters(stocks)
    print(f"  {len(stocks)} stocks passed all filters after client-side filtering.")

    # Save JSON
    from html_templates import _build_chips
    ts = datetime.now().isoformat()
    data = {"timestamp": ts, "count": len(stocks), "chips": _build_chips(), "stocks": stocks}
    json_path = DATA_DIR / "screener.json"
    json_path.write_text(json.dumps(data, indent=2))
    print(f"  Data saved to {json_path}")

    # Generate HTML
    generate_screener_html(stocks, ts)

    return stocks


def generate_screener_html(stocks, timestamp):
    from html_templates import render_screener
    html = render_screener(stocks, timestamp)
    out = OUTPUT_DIR / "screener.html"
    out.write_text(html, encoding="utf-8")
    print(f"  screener.html saved to {out}")
    print(f"\n  Open: file://{out.resolve()}\n")


if __name__ == "__main__":
    run_screener()
