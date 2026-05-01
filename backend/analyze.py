"""
analyze.py — On-demand Stock Analysis
Scrapes stockanalysis.com for a single ticker and writes its JSON data file.
The analysis.html page then reads that JSON to render the report.

Usage:
    python analyze.py AAPL
    python analyze.py BE
"""

import json
import os
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUTPUT_DIR = Path(__file__).parent.parent / "output"
DATA_DIR = OUTPUT_DIR / "data" / "tickers"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://stockanalysis.com/stocks"


def safe_text(page, selector, fallback="N/A"):
    try:
        el = page.locator(selector).first
        el.wait_for(timeout=5000)
        return el.inner_text().strip()
    except Exception:
        return fallback


def safe_texts(page, selector):
    try:
        return [el.inner_text().strip() for el in page.locator(selector).all()]
    except Exception:
        return []


def scrape_overview(page, ticker):
    """Scrape the main overview page."""
    url = f"{BASE}/{ticker.lower()}/"
    print(f"  Loading {url}...")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    data = {}

    # Company name
    data["name"] = safe_text(page, "h1", ticker)

    # Price & market cap from the stat bar
    stats = safe_texts(page, "[data-test='overview-info'] td, .snapshot-td2")
    data["price_raw"] = stats[0] if len(stats) > 0 else "N/A"
    data["market_cap_raw"] = stats[1] if len(stats) > 1 else "N/A"

    # Try alternate selectors for price
    if data["price_raw"] == "N/A":
        data["price_raw"] = safe_text(page, "[data-test='price'], .text-4xl, .price")

    # Exchange for TradingView
    exchange_text = safe_text(page, ".exchange, [data-test='exchange']", "NASDAQ")
    data["exchange"] = "NASDAQ" if "NASDAQ" in exchange_text.upper() else (
        "NYSE" if "NYSE" in exchange_text.upper() else "NASDAQ"
    )

    # Scrape key stats table
    data["stats"] = {}
    try:
        rows = page.locator("table tr, .stats-table tr").all()
        for row in rows:
            cells = row.locator("td").all()
            if len(cells) >= 2:
                key = cells[0].inner_text().strip()
                val = cells[1].inner_text().strip()
                if key:
                    data["stats"][key] = val
    except Exception:
        pass

    # News from overview page — /news/ sub-page no longer exists
    data["news_raw"] = []
    try:
        items = page.locator("[class*=news]").all()
        for item in items[:8]:
            try:
                title_el = item.locator("h3, h2").first
                title = title_el.inner_text().strip()
                a_el = item.locator("a").first
                href = a_el.get_attribute("href") or ""
                if not href.startswith("http"):
                    href = "https://stockanalysis.com" + href
                try:
                    date = item.locator("time").first.inner_text().strip()
                except Exception:
                    date = ""
                if title and len(title) > 5:
                    data["news_raw"].append({"title": title, "url": href, "date": date})
            except Exception:
                continue
    except Exception:
        pass

    # Company profile from /company/ page
    company_url = f"{BASE}/{ticker.lower()}/company/"
    print(f"  Loading company profile: {company_url}...")
    page.goto(company_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    data["description"] = "No description available."
    profile = {}
    try:
        body_text = page.inner_text("body")
        lines = [l.strip() for l in body_text.splitlines() if l.strip()]
        # Description follows "Company Description" heading
        for i, line in enumerate(lines):
            if line == "Company Description" and i + 1 < len(lines):
                data["description"] = lines[i + 1]
                break
        # Profile fields are tab-separated key\tvalue lines
        profile_keys = {"Country", "Founded", "IPO Date", "Industry", "Sector", "Employees", "CEO", "Website"}
        for line in lines:
            if "\t" in line:
                parts = line.split("\t", 1)
                if parts[0].strip() in profile_keys:
                    profile[parts[0].strip()] = parts[1].strip()
    except Exception as e:
        print(f"    Warning: company profile scrape partial: {e}")

    data["sector"]    = profile.get("Sector", "N/A")
    data["industry"]  = profile.get("Industry", "N/A")
    data["employees"] = profile.get("Employees", "N/A")
    data["founded"]   = profile.get("Founded", "N/A")
    data["country"]   = profile.get("Country", "N/A")
    data["website"]   = profile.get("Website", "N/A")

    # Back to overview page to read stats (already have them, just derive remaining fields)
    # 52W High and Low — site shows "52-Week Range" as "low - high"
    range_str = data["stats"].get("52-Week Range", "")
    if " - " in range_str:
        parts = range_str.split(" - ", 1)
        data["low_52w"]  = parts[0].strip()
        data["high_52w"] = parts[1].strip()
    else:
        data["high_52w"] = (
            data["stats"].get("52-Week High") or
            data["stats"].get("52W High") or
            safe_text(page, "[data-test='52w-high']", "N/A")
        )
        data["low_52w"] = (
            data["stats"].get("52-Week Low") or
            data["stats"].get("52W Low") or
            safe_text(page, "[data-test='52w-low']", "N/A")
        )

    # ATH
    data["ath"] = data["stats"].get("All-Time High", "N/A")
    data["ath_date"] = data["stats"].get("ATH Date", "N/A")

    # Market cap — strip trailing percentage change if present (e.g. "74.60B +783.8%")
    raw_mc = data["stats"].get("Market Cap") or data["market_cap_raw"] or "N/A"
    data["market_cap"] = raw_mc.split()[0] if raw_mc != "N/A" else "N/A"

    # Shares, PE, etc.
    data["pe_ratio"]    = data["stats"].get("PE Ratio", "N/A")
    eps_raw             = data["stats"].get("EPS", data["stats"].get("EPS (TTM)", "N/A"))
    data["eps_ttm"]     = eps_raw.split()[0] if eps_raw and eps_raw != "N/A" else "N/A"
    rev_raw             = data["stats"].get("Revenue (ttm)", data["stats"].get("Revenue (TTM)", "N/A"))
    data["revenue_ttm"] = rev_raw.split()[0] if rev_raw and rev_raw != "N/A" else "N/A"

    return data


def scrape_financials(page, ticker):
    """Scrape quarterly financials."""
    url = f"{BASE}/{ticker.lower()}/financials/?p=quarterly"
    print(f"  Loading financials: {url}...")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    financials = {"quarters": []}

    try:
        # Get column headers (quarter labels)
        headers = [h.inner_text().strip() for h in page.locator("thead th").all()]

        # Get rows
        rows = page.locator("tbody tr").all()
        for row in rows:
            cells = row.locator("td").all()
            if len(cells) >= 2:
                label = cells[0].inner_text().strip()
                values = [c.inner_text().strip() for c in cells[1:]]
                financials[label] = values

        financials["headers"] = headers[1:] if headers else []
    except Exception as e:
        print(f"    Warning: financials scrape partial: {e}")

    return financials


def scrape_news(page, ticker):
    # News is now collected inside scrape_overview from the main page
    return []


def analyze_ticker(ticker):
    ticker = ticker.upper().strip()
    print(f"\n=== Analyzing {ticker} ===\n")

    result = {
        "ticker": ticker,
        "generated_at": datetime.now().isoformat(),
        "overview": {},
        "financials": {},
        "forecast": {},
        "news": [],
    }

    with sync_playwright() as p:
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
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()

        # Dismiss cookie banner if present
        def dismiss_cookies():
            try:
                page.click("text=Consent", timeout=3000)
                time.sleep(0.8)
            except PWTimeout:
                pass

        page.goto(f"{BASE}/{ticker.lower()}/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        dismiss_cookies()

        result["overview"]   = scrape_overview(page, ticker)
        result["financials"] = scrape_financials(page, ticker)
        result["news"]       = result["overview"].pop("news_raw", [])

        browser.close()

    # Save JSON
    out = DATA_DIR / f"{ticker}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n  Data saved: {out}")
    print(f"  Open analysis: file://{(OUTPUT_DIR / 'analysis.html').resolve()}?ticker={ticker}\n")

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze.py TICKER")
        print("Example: python analyze.py AAPL")
        sys.exit(1)

    ticker = sys.argv[1]
    analyze_ticker(ticker)
