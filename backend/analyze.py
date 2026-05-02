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
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUTPUT_DIR = Path(__file__).parent.parent / "output"
DATA_DIR = OUTPUT_DIR / "data" / "tickers"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://stockanalysis.com/stocks"


def wait_for_content(page, selector, timeout=12000):
    try:
        page.wait_for_selector(selector, timeout=timeout)
    except Exception:
        pass


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
    """Scrape the main overview page (already loaded by caller)."""
    data = {}

    # Wait for the stats table to appear rather than sleeping
    wait_for_content(page, "table tr, h1")

    # Company name
    data["name"] = safe_text(page, "h1", ticker)

    # Price & market cap from the stat bar
    stats = safe_texts(page, "[data-test='overview-info'] td, .snapshot-td2")
    data["price_raw"] = stats[0] if len(stats) > 0 else "N/A"
    data["market_cap_raw"] = stats[1] if len(stats) > 1 else "N/A"

    if data["price_raw"] == "N/A":
        data["price_raw"] = safe_text(page, "[data-test='price'], .text-4xl, .price")

    exchange_text = safe_text(page, ".exchange, [data-test='exchange']", "NASDAQ")
    data["exchange"] = "NASDAQ" if "NASDAQ" in exchange_text.upper() else (
        "NYSE" if "NYSE" in exchange_text.upper() else "NASDAQ"
    )

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

    # News from overview page
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

    return data


def scrape_company(page, ticker):
    """Scrape company profile page (already loaded by caller)."""
    wait_for_content(page, "body")

    description = "No description available."
    profile = {}
    try:
        body_text = page.inner_text("body")
        lines = [l.strip() for l in body_text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            if line == "Company Description" and i + 1 < len(lines):
                description = lines[i + 1]
                break
        profile_keys = {"Country", "Founded", "IPO Date", "Industry", "Sector", "Employees", "CEO", "Website"}
        for line in lines:
            if "\t" in line:
                parts = line.split("\t", 1)
                if parts[0].strip() in profile_keys:
                    profile[parts[0].strip()] = parts[1].strip()
    except Exception as e:
        print(f"    Warning: company profile scrape partial: {e}")

    return description, profile


def scrape_financials(page, ticker):
    """Scrape quarterly financials (already loaded by caller)."""
    wait_for_content(page, "tbody tr")

    financials = {"quarters": []}
    try:
        headers = [h.inner_text().strip() for h in page.locator("thead th").all()]
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

        # Block images, fonts, and media — no visual rendering needed
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,mp4,mp3}", lambda r: r.abort())

        def dismiss_cookies():
            try:
                page.click("text=Consent", timeout=3000)
            except PWTimeout:
                pass

        # Page 1: Overview
        url_overview = f"{BASE}/{ticker.lower()}/"
        print(f"  Loading {url_overview}...")
        page.goto(url_overview, wait_until="domcontentloaded", timeout=30000)
        dismiss_cookies()
        ov = scrape_overview(page, ticker)

        # Page 2: Company profile
        url_company = f"{BASE}/{ticker.lower()}/company/"
        print(f"  Loading {url_company}...")
        page.goto(url_company, wait_until="domcontentloaded", timeout=30000)
        description, profile = scrape_company(page, ticker)

        # Page 3: Financials
        url_fin = f"{BASE}/{ticker.lower()}/financials/?p=quarterly"
        print(f"  Loading {url_fin}...")
        page.goto(url_fin, wait_until="domcontentloaded", timeout=30000)
        fin = scrape_financials(page, ticker)

        browser.close()

    # Merge overview + company data
    ov["description"] = description
    ov["sector"]    = profile.get("Sector", "N/A")
    ov["industry"]  = profile.get("Industry", "N/A")
    ov["employees"] = profile.get("Employees", "N/A")
    ov["founded"]   = profile.get("Founded", "N/A")
    ov["country"]   = profile.get("Country", "N/A")
    ov["website"]   = profile.get("Website", "N/A")

    range_str = ov["stats"].get("52-Week Range", "")
    if " - " in range_str:
        parts = range_str.split(" - ", 1)
        ov["low_52w"]  = parts[0].strip()
        ov["high_52w"] = parts[1].strip()
    else:
        ov["high_52w"] = ov["stats"].get("52-Week High") or ov["stats"].get("52W High", "N/A")
        ov["low_52w"]  = ov["stats"].get("52-Week Low")  or ov["stats"].get("52W Low",  "N/A")

    ov["ath"]      = ov["stats"].get("All-Time High", "N/A")
    ov["ath_date"] = ov["stats"].get("ATH Date", "N/A")

    raw_mc = ov["stats"].get("Market Cap") or ov.get("market_cap_raw", "N/A")
    ov["market_cap"] = raw_mc.split()[0] if raw_mc and raw_mc != "N/A" else "N/A"

    ov["pe_ratio"]    = ov["stats"].get("PE Ratio", "N/A")
    eps_raw           = ov["stats"].get("EPS", ov["stats"].get("EPS (TTM)", "N/A"))
    ov["eps_ttm"]     = eps_raw.split()[0] if eps_raw and eps_raw != "N/A" else "N/A"
    rev_raw           = ov["stats"].get("Revenue (ttm)", ov["stats"].get("Revenue (TTM)", "N/A"))
    ov["revenue_ttm"] = rev_raw.split()[0] if rev_raw and rev_raw != "N/A" else "N/A"

    result["overview"]   = ov
    result["financials"] = fin
    result["news"]       = ov.pop("news_raw", [])

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
