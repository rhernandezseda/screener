"""
analyze.py — Fast on-demand stock analysis report generator.

Fetches 3 pages concurrently from stockanalysis.com (no browser),
extracts SvelteKit-embedded JSON data, and writes a self-contained
HTML report to output/data/tickers/{TICKER}_analisis.html.

Usage:
    python analyze.py AAPL
"""

import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

import httpx

OUTPUT_DIR = Path(__file__).parent.parent / "output"
DATA_DIR = OUTPUT_DIR / "data" / "tickers"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://stockanalysis.com/stocks"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Data extraction ───────────────────────────────────────────────────────────

def fetch_page(url: str) -> str:
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def extract_sveltekit(html: str) -> list:
    """Extract the SvelteKit data array from the page's inline script."""
    scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html)
    sk = next((s for s in scripts if '__sveltekit' in s), '')
    m = re.search(r'data:\s*(\[\{[\s\S]*)', sk)
    if not m:
        return []
    raw = m.group(1)
    # Strip trailing JS after the array
    raw = re.sub(r'\]\s*,\s*form:.*$', ']', raw, flags=re.DOTALL)
    # Convert JS literals to JSON
    raw = re.sub(r'\bvoid\s+0\b', 'null', raw)
    raw = re.sub(r'\bundefined\b', 'null', raw)
    # Quote unquoted object keys
    raw = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', raw)
    # Fix bare leading-dot decimals: -.07 → -0.07  and  :.5 → :0.5
    raw = re.sub(r'(-)\.(\d)', r'-0.\2', raw)
    raw = re.sub(r'([:,\[({])\s*\.(\d)', r'\g<1>0.\2', raw)
    try:
        return json.loads(raw)
    except Exception:
        return []


def get_overview_data(ticker: str) -> dict:
    html = fetch_page(f"{BASE}/{ticker.lower()}/")
    arr = extract_sveltekit(html)
    if len(arr) < 3:
        return {}
    info = arr[1].get("data", {}).get("info", {})
    metrics = arr[2].get("data", {})
    quote = info.get("quote", {})
    return {
        "name": info.get("nameFull", ticker),
        "exchange": info.get("exchange", "NASDAQ"),
        "price": quote.get("p"),
        "price_change_pct": quote.get("cp"),
        "high_52w": quote.get("h52"),
        "low_52w": quote.get("l52"),
        "market_cap": metrics.get("marketCap"),
        "revenue_ttm": metrics.get("revenue"),
        "net_income": metrics.get("netIncome"),
        "eps": metrics.get("eps"),
        "eps_growth": metrics.get("epsGrowth"),
        "pe_ratio": metrics.get("peRatio"),
        "forward_pe": metrics.get("forwardPE"),
        "beta": metrics.get("beta"),
        "analysts": metrics.get("analysts"),
        "price_target": metrics.get("target"),
        "earnings_date": metrics.get("earningsDate"),
        "description": metrics.get("description", ""),
        "industry": next((x["v"] for x in metrics.get("infoTable", []) if x.get("t") == "Industry"), ""),
        "sector": next((x["v"] for x in metrics.get("infoTable", []) if x.get("t") == "Sector"), ""),
        "employees": next((x["v"] for x in metrics.get("infoTable", []) if x.get("t") == "Employees"), ""),
        "website": next((x.get("eu") or x.get("v", "") for x in metrics.get("infoTable", []) if x.get("t") == "Website"), ""),
        "financial_intro": metrics.get("financialIntro", ""),
    }


def get_financials_data(ticker: str) -> dict:
    html = fetch_page(f"{BASE}/{ticker.lower()}/financials/?p=quarterly")
    arr = extract_sveltekit(html)
    if len(arr) < 3:
        return {}
    fd = arr[2].get("data", {}).get("financialData", {})
    dates = fd.get("datekey", [])
    if not dates:
        return {}
    # Most recent two quarters
    def q(key, idx=0):
        vals = fd.get(key, [])
        return vals[idx] if idx < len(vals) else None

    def pct(val):
        if val is None:
            return None
        return round(val * 100, 2)

    return {
        "dates": dates[:4],
        "fiscal_quarters": [f"Q{fd.get('fiscalQuarter', ['?']*4)[i]} {fd.get('fiscalYear', ['?']*4)[i]}" for i in range(min(4, len(dates)))],
        # Last reported quarter (index 0)
        "revenue_last": q("revenue", 0),
        "revenue_yoy": pct(q("revenueGrowth", 0)),
        "net_income_last": q("netIncome", 0),
        "net_income_yoy": pct(q("netIncomeGrowth", 0)),
        "eps_last": q("epsBasic", 0),
        "gross_margin_last": pct(q("grossMargin", 0)) if q("grossMargin", 0) else None,
        "operating_margin_last": pct(q("operatingMargin", 0)) if q("operatingMargin", 0) else None,
        # Prior quarter for context
        "revenue_prior": q("revenue", 1),
        "eps_prior": q("epsBasic", 1),
    }


def get_forecast_data(ticker: str) -> dict:
    html = fetch_page(f"{BASE}/{ticker.lower()}/forecast/")
    arr = extract_sveltekit(html)
    if len(arr) < 3:
        return {}
    est = arr[2].get("data", {}).get("estimates", {})
    stats = est.get("stats", {})
    qtr = stats.get("quarterly", {})
    ann = stats.get("annual", {})
    table = est.get("table", {}).get("quarterly", {})
    dates = table.get("dates", [])
    last_idx = table.get("lastDate", 0)
    # Next quarter is lastDate + 1
    next_idx = last_idx + 1

    def safe(d, key):
        v = d.get(key, {})
        return v.get("this") if isinstance(v, dict) else None

    return {
        "next_q_date": dates[next_idx] if next_idx < len(dates) else None,
        "next_q_label": f"Q{table.get('fiscalQuarter', [])[next_idx] if next_idx < len(table.get('fiscalQuarter', [])) else '?'} {table.get('fiscalYear', [])[next_idx] if next_idx < len(table.get('fiscalYear', [])) else '?'}",
        "eps_next_q": safe(qtr, "epsNext"),
        "eps_next_q_growth": qtr.get("epsNext", {}).get("growth") if isinstance(qtr.get("epsNext"), dict) else None,
        "revenue_next_q": safe(qtr, "revenueNext"),
        "revenue_next_q_growth": qtr.get("revenueNext", {}).get("growth") if isinstance(qtr.get("revenueNext"), dict) else None,
        "eps_this_year": safe(ann, "epsThis"),
        "eps_next_year": safe(ann, "epsNext"),
        "revenue_this_year": safe(ann, "revenueThis"),
        "revenue_next_year": safe(ann, "revenueNext"),
        "analyst_consensus": arr[1].get("data", {}).get("info", {}).get("quote", {}),
    }


def get_ath_data(ticker: str) -> dict:
    """Derive ATH from price history page."""
    html = fetch_page(f"{BASE}/{ticker.lower()}/history/?p=annual")
    arr = extract_sveltekit(html)
    if len(arr) < 3:
        return {}
    outer = arr[2].get("data", {})
    # Structure: arr[2]['data']['data']['data'] = list of {t, o, h, l, c, v, ...}
    container = outer.get("data", {})
    if isinstance(container, dict):
        history = container.get("data", [])
    else:
        history = container
    if not history or not isinstance(history, list):
        return {}
    # Filter to dicts only and find record close
    records = [x for x in history if isinstance(x, dict)]
    if not records:
        return {}
    ath_entry = max(records, key=lambda x: x.get("c", 0) or 0)
    return {
        "ath_close": ath_entry.get("c"),
        "ath_date": ath_entry.get("t"),
        "ath_intraday": max((x.get("h", 0) or 0) for x in records),
    }


# ── Formatting helpers ────────────────────────────────────────────────────────

def _to_float(n):
    """Coerce n to float, returning None on failure."""
    if n is None:
        return None
    if isinstance(n, (int, float)):
        return float(n)
    try:
        return float(str(n).replace(",", ""))
    except (ValueError, TypeError):
        return None


def fmt_large(n):
    if n is None:
        return "N/A"
    if isinstance(n, str):
        return n  # already formatted (e.g. "4.82T")
    if n >= 1e12:
        return f"${n/1e12:.2f}T"
    if n >= 1e9:
        return f"${n/1e9:.2f}B"
    if n >= 1e6:
        return f"${n/1e6:.2f}M"
    return f"${n:,.0f}"


def fmt_pct(n, decimals=2):
    v = _to_float(n)
    if v is None:
        return "N/A"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def fmt_price(n):
    v = _to_float(n)
    if v is None:
        return "N/A"
    return f"${v:,.2f}"


def pct_from_ath(price, ath):
    p = _to_float(price)
    a = _to_float(ath)
    if not p or not a or a == 0:
        return None
    return round((p - a) / a * 100, 2)


def color_class(val):
    """Return 'pos' or 'neg' CSS class based on numeric value."""
    v = _to_float(val)
    if v is None:
        return ""
    return "pos" if v >= 0 else "neg"


# ── Brand palettes ────────────────────────────────────────────────────────────

BRAND_PALETTES = {
    "AAPL": {"navy": "#1D1D1F", "accent": "#0071E3"},
    "MSFT": {"navy": "#243A5E", "accent": "#0078D4"},
    "AMZN": {"navy": "#232F3E", "accent": "#FF9900"},
    "GOOGL": {"navy": "#174EA6", "accent": "#4285F4"},
    "GOOG":  {"navy": "#174EA6", "accent": "#4285F4"},
    "META":  {"navy": "#0866FF", "accent": "#1877F2"},
    "NVDA":  {"navy": "#1A1A2E", "accent": "#76B900"},
    "TSLA":  {"navy": "#CC0000", "accent": "#E82127"},
    "NFLX":  {"navy": "#141414", "accent": "#E50914"},
    "AMD":   {"navy": "#1A1A1A", "accent": "#ED1C24"},
    "AVGO":  {"navy": "#CC0000", "accent": "#CC0000"},
    "CRM":   {"navy": "#032D60", "accent": "#00A1E0"},
    "ORCL":  {"navy": "#C74634", "accent": "#F80000"},
    "ADBE":  {"navy": "#FF0000", "accent": "#FA0F00"},
    "NOW":   {"navy": "#293E40", "accent": "#62D84E"},
    "SNOW":  {"navy": "#29B5E8", "accent": "#29B5E8"},
    "DDOG":  {"navy": "#632CA6", "accent": "#774AA4"},
    "CRWD":  {"navy": "#FF0000", "accent": "#FC1944"},
    "NET":   {"navy": "#F6821F", "accent": "#F6821F"},
    "ZS":    {"navy": "#005DAA", "accent": "#00AEEF"},
    "MDB":   {"navy": "#00684A", "accent": "#00ED64"},
    "TTD":   {"navy": "#0083CB", "accent": "#0083CB"},
    "UBER":  {"navy": "#000000", "accent": "#276EF1"},
    "SHOP":  {"navy": "#96BF48", "accent": "#5C6AC4"},
    "SQ":    {"navy": "#3E4348", "accent": "#006AFF"},
    "PYPL":  {"navy": "#003087", "accent": "#009CDE"},
    "COIN":  {"navy": "#0052FF", "accent": "#0052FF"},
    "MELI":  {"navy": "#FFE600", "accent": "#FFE600"},
}

DEFAULT_PALETTE = {"navy": "#1A1D2E", "accent": "#6366F1"}


def get_palette(ticker: str) -> dict:
    return BRAND_PALETTES.get(ticker.upper(), DEFAULT_PALETTE)


# ── SVG logo builder ──────────────────────────────────────────────────────────

def build_logo_svg(ticker: str, palette: dict) -> str:
    """Generate a clean typographic SVG logo for any ticker."""
    accent = palette["accent"]
    initials = ticker[:2] if len(ticker) >= 2 else ticker
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 110 110" width="110" height="110">
  <rect width="110" height="110" rx="16" fill="{accent}"/>
  <text x="55" y="68" font-family="Georgia,serif" font-size="38" font-weight="bold"
        text-anchor="middle" fill="white">{initials}</text>
</svg>"""


# ── HTML report generator ─────────────────────────────────────────────────────

def build_report(ticker: str, ov: dict, fin: dict, fcast: dict, ath: dict) -> str:
    palette = get_palette(ticker)
    navy = palette["navy"]
    accent = palette["accent"]

    price = ov.get("price")
    ath_close = ath.get("ath_close")
    ath_intraday = ath.get("ath_intraday")
    ath_date = ath.get("ath_date", "N/A")
    dist_ath = pct_from_ath(price, ath_close)

    exchange = ov.get("exchange", "NASDAQ")
    month_year = datetime.now().strftime("%B %Y")
    generated = datetime.now().strftime("%B %d, %Y · %H:%M")

    logo_svg = build_logo_svg(ticker, palette)

    # Last reported quarter label
    last_q = fin.get("fiscal_quarters", ["Q?"])[0] if fin.get("fiscal_quarters") else "Last Quarter"
    next_q = fcast.get("next_q_label", "Next Quarter")
    next_q_date = fcast.get("next_q_date", "")
    next_q_date_fmt = f"reports ~{next_q_date}" if next_q_date else ""

    rev_yoy = fin.get("revenue_yoy")
    eps_last = fin.get("eps_last")
    ni_yoy = fin.get("net_income_yoy")

    eps_next_q = fcast.get("eps_next_q")
    eps_next_q_growth = fcast.get("eps_next_q_growth")
    rev_next_q = fcast.get("revenue_next_q")
    rev_next_q_growth = fcast.get("revenue_next_q_growth")

    description = ov.get("description", "")
    if len(description) > 600:
        description = description[:600] + "…"

    dist_class = color_class(dist_ath)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{ticker} · Fundamental Analysis</title>
<style>
  :root {{
    --navy:   {navy};
    --accent: {accent};
    --light:  #F7F7F7;
    --white:  #ffffff;
    --text:   #1a1a1a;
    --muted:  #6b7280;
    --pos:    #16a34a;
    --neg:    #dc2626;
    --border: #e5e7eb;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "Segoe UI", Calibri, Arial, sans-serif;
    background: var(--light);
    color: var(--text);
    font-size: 14px;
    line-height: 1.6;
  }}
  h1, h2, h3 {{ font-family: Georgia, serif; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .pos {{ color: var(--pos); }}
  .neg {{ color: var(--neg); }}
  .accent {{ color: var(--accent); font-weight: 600; }}

  /* ── HERO ── */
  .hero {{
    background: var(--navy);
    border-left: 14px solid var(--accent);
    padding: 48px 56px 40px;
    color: white;
  }}
  .hero-top {{
    display: flex;
    align-items: center;
    gap: 28px;
    flex-wrap: nowrap;
    margin-bottom: 10px;
  }}
  .logo-box {{
    flex-shrink: 0;
    width: 110px;
    height: 110px;
    border-radius: 16px;
    overflow: hidden;
    background: white;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .hero-title {{ flex: 1; min-width: 0; }}
  .hero-title h1 {{
    font-size: 44px;
    font-weight: normal;
    color: white;
    line-height: 1.1;
    margin-bottom: 8px;
  }}
  .ticker-badge {{
    display: inline-block;
    border: 1.5px solid var(--accent);
    color: var(--accent);
    padding: 3px 12px;
    border-radius: 6px;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 1px;
    margin-left: 12px;
    vertical-align: middle;
  }}
  .hero-sub {{
    color: rgba(255,255,255,0.55);
    font-size: 13px;
    margin-top: 4px;
  }}

  /* stat strip */
  .stat-strip {{
    display: flex;
    gap: 12px;
    margin-top: 32px;
    flex-wrap: wrap;
  }}
  .stat-card {{
    background: rgba(255,255,255,0.07);
    border: 1px solid var(--accent);
    border-radius: 8px;
    padding: 14px 20px;
    min-width: 140px;
    flex: 1;
  }}
  .stat-card .label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: rgba(255,255,255,0.5);
    margin-bottom: 6px;
  }}
  .stat-card .value {{
    font-size: 24px;
    font-weight: 700;
    color: white;
    line-height: 1;
  }}
  .stat-card .sub {{
    font-size: 11px;
    color: rgba(255,255,255,0.45);
    margin-top: 4px;
  }}

  /* ── SECTIONS ── */
  .section {{
    padding: 40px 56px;
    border-bottom: 1px solid var(--border);
  }}
  .section:last-child {{ border-bottom: none; }}
  .section-title {{
    font-size: 22px;
    font-weight: normal;
    color: var(--navy);
    margin-bottom: 24px;
    padding-bottom: 10px;
    border-bottom: 2px solid var(--accent);
    display: inline-block;
  }}

  /* ── DESCRIPTION ── */
  .desc-card {{
    background: white;
    border-radius: 10px;
    padding: 24px 28px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    line-height: 1.75;
    font-size: 14.5px;
    color: #374151;
  }}

  /* ── FUNDAMENTALS ── */
  .mktcap-card {{
    background: white;
    border-radius: 10px;
    padding: 20px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    margin-bottom: 20px;
    flex-wrap: wrap;
    gap: 16px;
  }}
  .mktcap-big {{
    font-size: 46px;
    font-weight: 700;
    color: var(--navy);
    font-family: Georgia, serif;
    line-height: 1;
  }}
  .mktcap-label {{
    font-size: 13px;
    color: var(--muted);
    margin-top: 4px;
  }}
  .mktcap-metrics {{
    display: flex;
    gap: 28px;
    flex-wrap: wrap;
  }}
  .mini-metric .label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    color: var(--muted);
    margin-bottom: 3px;
  }}
  .mini-metric .val {{
    font-size: 18px;
    font-weight: 600;
    color: var(--navy);
  }}

  .tables-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }}
  .data-table {{
    background: white;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }}
  .data-table thead tr {{
    background: var(--navy);
    color: white;
  }}
  .data-table.accent-head thead tr {{
    background: var(--accent);
  }}
  .data-table th {{
    padding: 12px 16px;
    font-size: 12px;
    font-weight: 600;
    text-align: left;
    letter-spacing: 0.4px;
  }}
  .data-table td {{
    padding: 11px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13.5px;
  }}
  .data-table tr:last-child td {{ border-bottom: none; }}
  .data-table tr:hover td {{ background: #fafafa; }}
  .data-table td:last-child {{ text-align: right; font-weight: 600; }}

  /* ── TECHNICAL ── */
  .technical-grid {{
    display: grid;
    grid-template-columns: 1fr 1.4fr;
    gap: 20px;
    align-items: start;
  }}
  .ath-block {{
    background: var(--navy);
    border-radius: 10px;
    padding: 36px 32px;
    color: white;
    text-align: center;
  }}
  .ath-pct {{
    font-family: Georgia, serif;
    font-size: 96px;
    font-weight: 700;
    line-height: 1;
    margin-bottom: 16px;
  }}
  .ath-pct.pos {{ color: #4ade80; }}
  .ath-pct.neg {{ color: #f87171; }}
  .ath-details {{
    list-style: none;
    padding: 0;
    margin-top: 16px;
  }}
  .ath-details li {{
    padding: 6px 0;
    border-top: 1px solid rgba(255,255,255,0.1);
    font-size: 13px;
    color: rgba(255,255,255,0.7);
    display: flex;
    justify-content: space-between;
  }}
  .ath-details li span {{ color: white; font-weight: 600; }}
  .technical-notes {{
    background: white;
    border-left: 6px solid var(--accent);
    border-radius: 8px;
    padding: 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }}
  .technical-notes h4 {{
    font-size: 15px;
    font-weight: 700;
    color: var(--navy);
    margin-bottom: 14px;
  }}
  .technical-notes table {{
    width: 100%;
    border-collapse: collapse;
  }}
  .technical-notes td {{
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    font-size: 13.5px;
    color: #374151;
  }}
  .technical-notes td:last-child {{
    text-align: right;
    font-weight: 600;
    color: var(--navy);
  }}
  .technical-notes tr:last-child td {{ border-bottom: none; }}

  /* ── CHART ── */
  .chart-section {{
    padding: 0 56px 40px;
  }}
  .chart-section h2 {{
    font-size: 22px;
    font-weight: normal;
    color: var(--navy);
    margin-bottom: 16px;
    padding-bottom: 10px;
    border-bottom: 2px solid var(--accent);
    display: inline-block;
  }}
  .chart-wrap iframe {{
    width: 100%;
    height: 560px;
    border: none;
    border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.1);
  }}

  /* ── FOOTER ── */
  footer {{
    background: var(--navy);
    color: rgba(255,255,255,0.5);
    padding: 24px 56px;
    font-size: 12px;
  }}
  footer strong {{ color: white; }}
  footer .sources {{ margin-top: 8px; }}
  footer .sources a {{ color: rgba(255,255,255,0.55); }}
  footer .sources a:hover {{ color: var(--accent); }}

  @media (max-width: 640px) {{
    .hero {{ padding: 28px 20px; border-left-width: 6px; }}
    .hero-top {{ flex-wrap: wrap; }}
    .hero-title h1 {{ font-size: 28px; }}
    .section {{ padding: 28px 20px; }}
    .tables-grid, .technical-grid {{ grid-template-columns: 1fr; }}
    .ath-pct {{ font-size: 64px; }}
    .chart-section {{ padding: 0 20px 28px; }}
    footer {{ padding: 20px; }}
  }}
</style>
</head>
<body>

<!-- ── A · HERO ── -->
<header class="hero">
  <div class="hero-top">
    <div class="logo-box">
      {logo_svg}
    </div>
    <div class="hero-title">
      <h1>{ov.get("name", ticker)} <span class="ticker-badge">{ticker}</span></h1>
      <div class="hero-sub">Fundamental &amp; Technical Analysis · {month_year}</div>
      <div class="hero-sub" style="margin-top:4px">{ov.get("sector", "")} · {ov.get("industry", "")} · {ov.get("exchange", "")}</div>
    </div>
  </div>
  <div class="stat-strip">
    <div class="stat-card">
      <div class="label">Price</div>
      <div class="value">{fmt_price(price)}</div>
      <div class="sub">{fmt_pct(ov.get("price_change_pct"))} today</div>
    </div>
    <div class="stat-card">
      <div class="label">Market Cap</div>
      <div class="value">{ov.get("market_cap", "N/A")}</div>
      <div class="sub">{ov.get("exchange", "")}</div>
    </div>
    <div class="stat-card">
      <div class="label">Revenue {last_q} YoY</div>
      <div class="value {'pos' if (rev_yoy or 0) >= 0 else 'neg'}">{fmt_pct(rev_yoy)}</div>
      <div class="sub">{fmt_large(fin.get("revenue_last"))}</div>
    </div>
    <div class="stat-card">
      <div class="label">EPS {last_q} YoY</div>
      <div class="value {'pos' if (ni_yoy or 0) >= 0 else 'neg'}">{fmt_pct(ni_yoy)}</div>
      <div class="sub">EPS {fmt_price(eps_last)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Dist. from ATH</div>
      <div class="value {dist_class}">{fmt_pct(dist_ath)}</div>
      <div class="sub">ATH {fmt_price(ath_close)}</div>
    </div>
  </div>
</header>

<!-- ── B · ABOUT ── -->
<section class="section">
  <h2 class="section-title">What does it do?</h2>
  <div class="desc-card">
    <p>{description}</p>
    {f'<p style="margin-top:12px; font-size:13px; color:#6b7280">{ov.get("financial_intro","")}</p>' if ov.get("financial_intro") else ""}
    <p style="margin-top:14px; font-size:13px;">
      <strong>Employees:</strong> {ov.get("employees","N/A")} &nbsp;·&nbsp;
      <strong>Sector:</strong> <span class="accent">{ov.get("sector","N/A")}</span> &nbsp;·&nbsp;
      <strong>Industry:</strong> <span class="accent">{ov.get("industry","N/A")}</span>
      {f' &nbsp;·&nbsp; <a href="{ov.get("website")}" target="_blank">{ov.get("website","")}</a>' if ov.get("website") else ""}
    </p>
  </div>
</section>

<!-- ── E · FUNDAMENTALS ── -->
<section class="section">
  <h2 class="section-title">Fundamental Data</h2>
  <div class="mktcap-card">
    <div>
      <div class="mktcap-big">{ov.get("market_cap","N/A")}</div>
      <div class="mktcap-label">Market Capitalisation · {ov.get("exchange","")}: {ticker}</div>
    </div>
    <div class="mktcap-metrics">
      <div class="mini-metric">
        <div class="label">P/E Ratio</div>
        <div class="val">{ov.get("pe_ratio","N/A")}</div>
      </div>
      <div class="mini-metric">
        <div class="label">Forward P/E</div>
        <div class="val">{ov.get("forward_pe","N/A")}</div>
      </div>
      <div class="mini-metric">
        <div class="label">EPS (TTM)</div>
        <div class="val">{fmt_price(ov.get("eps"))}</div>
      </div>
      <div class="mini-metric">
        <div class="label">Beta</div>
        <div class="val">{ov.get("beta","N/A")}</div>
      </div>
      <div class="mini-metric">
        <div class="label">Revenue (TTM)</div>
        <div class="val">{ov.get("revenue_ttm","N/A")}</div>
      </div>
    </div>
  </div>
  <div class="tables-grid">
    <table class="data-table">
      <thead>
        <tr><th colspan="2">{last_q} · Last reported quarter</th></tr>
      </thead>
      <tbody>
        <tr><td>Revenue</td><td>{fmt_large(fin.get("revenue_last"))}</td></tr>
        <tr><td>Revenue growth YoY</td><td class="{color_class(rev_yoy)}">{fmt_pct(rev_yoy)}</td></tr>
        <tr><td>Net income</td><td>{fmt_large(fin.get("net_income_last"))}</td></tr>
        <tr><td>Net income growth YoY</td><td class="{color_class(ni_yoy)}">{fmt_pct(ni_yoy)}</td></tr>
        <tr><td>Basic EPS</td><td>{fmt_price(fin.get("eps_last"))}</td></tr>
        <tr><td>Gross margin</td><td>{fmt_pct(fin.get("gross_margin_last"))}</td></tr>
        <tr><td>Operating margin</td><td>{fmt_pct(fin.get("operating_margin_last"))}</td></tr>
      </tbody>
    </table>
    <table class="data-table accent-head">
      <thead>
        <tr><th colspan="2">{next_q} · Analyst estimates · {next_q_date_fmt}</th></tr>
      </thead>
      <tbody>
        <tr><td>Est. revenue</td><td>{fmt_large(rev_next_q)}</td></tr>
        <tr><td>Est. revenue growth</td><td class="{color_class(rev_next_q_growth)}">{fmt_pct(rev_next_q_growth)}</td></tr>
        <tr><td>Est. EPS</td><td>{fmt_price(eps_next_q)}</td></tr>
        <tr><td>Est. EPS growth</td><td class="{color_class(eps_next_q_growth)}">{fmt_pct(eps_next_q_growth)}</td></tr>
        <tr><td>EPS this fiscal year</td><td>{fmt_price(fcast.get("eps_this_year"))}</td></tr>
        <tr><td>EPS next fiscal year</td><td>{fmt_price(fcast.get("eps_next_year"))}</td></tr>
        <tr><td>Analyst consensus</td><td class="accent">{ov.get("analysts","N/A")}</td></tr>
      </tbody>
    </table>
  </div>
</section>

<!-- ── F · TECHNICAL ANALYSIS ── -->
<section class="section">
  <h2 class="section-title">Technical Analysis · Distance from All-Time High</h2>
  <div class="technical-grid">
    <div class="ath-block">
      <div class="ath-pct {dist_class}">{fmt_pct(dist_ath)}</div>
      <div style="color:rgba(255,255,255,0.5); font-size:13px">from closing ATH</div>
      <ul class="ath-details">
        <li>Current price <span>{fmt_price(price)}</span></li>
        <li>ATH close <span>{fmt_price(ath_close)}</span></li>
        <li>ATH date <span>{ath_date}</span></li>
        <li>52W intraday high <span>{fmt_price(ov.get("high_52w"))}</span></li>
        <li>52W intraday low <span>{fmt_price(ov.get("low_52w"))}</span></li>
      </ul>
    </div>
    <div class="technical-notes">
      <h4>Key Levels</h4>
      <table>
        <tr><td>52-week high</td><td>{fmt_price(ov.get("high_52w"))}</td></tr>
        <tr><td>52-week low</td><td>{fmt_price(ov.get("low_52w"))}</td></tr>
        <tr><td>ATH close</td><td>{fmt_price(ath_close)}</td></tr>
        <tr><td>ATH intraday</td><td>{fmt_price(ath_intraday)}</td></tr>
        <tr><td>Distance from ATH</td><td class="{dist_class}">{fmt_pct(dist_ath)}</td></tr>
        <tr><td>Next earnings</td><td>{ov.get("earnings_date","N/A")}</td></tr>
        <tr><td>Analyst consensus</td><td class="accent">{ov.get("analysts","N/A")}</td></tr>
        <tr><td>Price target</td><td>{ov.get("price_target","N/A")}</td></tr>
        <tr><td>Beta</td><td>{ov.get("beta","N/A")}</td></tr>
      </table>
    </div>
  </div>
</section>

<!-- ── G · CHART ── -->
<div class="chart-section">
  <h2>Price Chart · 24 months</h2>
  <div class="chart-wrap">
    <iframe
      src="https://s.tradingview.com/widgetembed/?symbol={exchange}%3A{ticker}&interval=W&theme=light&style=1&locale=en&toolbarbg=F1F3F6&hideideas=1&range=24M&hidetoptoolbar=0&hidesidetoolbar=1&saveimage=0&studies=%5B%5D"
    ></iframe>
  </div>
</div>

<!-- ── H · FOOTER ── -->
<footer>
  <div><strong>Report generated:</strong> {generated} · <strong>Ticker:</strong> {ticker} · <strong>Data source:</strong> stockanalysis.com</div>
  <div class="sources">
    <strong>Sources:</strong>
    <a href="https://stockanalysis.com/stocks/{ticker.lower()}/" target="_blank">stockanalysis.com/stocks/{ticker.lower()}</a> ·
    <a href="https://stockanalysis.com/stocks/{ticker.lower()}/financials/?p=quarterly" target="_blank">Financials</a> ·
    <a href="https://stockanalysis.com/stocks/{ticker.lower()}/forecast/" target="_blank">Forecast</a> ·
    <a href="https://www.tradingview.com/chart/?symbol={exchange}:{ticker}" target="_blank">TradingView</a>
  </div>
</footer>

</body>
</html>"""


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze_ticker(ticker: str):
    ticker = ticker.upper().strip()
    print(f"\n=== Analyzing {ticker} ===", flush=True)

    results = {}
    errors = []

    def run(key, fn, *args):
        try:
            results[key] = fn(*args)
            print(f"  ✓ {key}", flush=True)
        except Exception as e:
            results[key] = {}
            errors.append(f"{key}: {e}")
            print(f"  ✗ {key}: {e}", flush=True)

    # Fetch all 4 pages concurrently
    threads = [
        threading.Thread(target=run, args=("overview",   get_overview_data,  ticker)),
        threading.Thread(target=run, args=("financials", get_financials_data, ticker)),
        threading.Thread(target=run, args=("forecast",   get_forecast_data,   ticker)),
        threading.Thread(target=run, args=("ath",        get_ath_data,        ticker)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ov    = results.get("overview",   {})
    fin   = results.get("financials", {})
    fcast = results.get("forecast",   {})
    ath   = results.get("ath",        {})

    html = build_report(ticker, ov, fin, fcast, ath)

    out = DATA_DIR / f"{ticker}_analisis.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n  Saved: {out}", flush=True)
    return str(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze.py TICKER")
        sys.exit(1)
    analyze_ticker(sys.argv[1])
