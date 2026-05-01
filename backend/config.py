"""
config.py — Screener filter configuration

Edit the values below to change which stocks pass the screener.
Run `python screener.py` after saving to apply the new filters.

THRESHOLDS keys match the column names extracted from stockanalysis.com:
  market_cap      — market capitalisation, in millions (e.g. 2000 = $2B)
  price           — stock price in USD
  revenue_growth  — revenue YoY growth, in percent
  avg_volume      — average daily trading volume
  eps_next_year   — estimated EPS growth next year, in percent
  high_52w_chg    — % distance from 52-week high (negative = below high)
"""

# ── Numeric thresholds ────────────────────────────────────────────────────────
# Each entry: field -> minimum value (stocks below this are excluded)

THRESHOLDS = {
    "market_cap":     2_000,    # > $2B  (site reports in millions)
    "price":          9,        # > $9
    "revenue_growth": 20,       # > 20% YoY
    "avg_volume":     200_000,  # > 200K avg daily volume
    "eps_next_year":  0,        # > 0% estimated EPS growth next year
    "high_52w_chg":  -20,       # within 20% of 52-week high
}

# ── Dividend filter ───────────────────────────────────────────────────────────
# True  = only include stocks with NO dividend (growth screener default)
# False = dividends are allowed
EXCLUDE_DIVIDENDS = True

# ── Screener preset buttons ───────────────────────────────────────────────────
# These are the preset buttons clicked on stockanalysis.com to narrow the
# server-side results before client-side filtering kicks in.
# Only change these if stockanalysis.com changes its UI options.

SITE_PRESETS = [
    ("marketCap",     "Market Cap",            "Over 1B"),
    ("price",         "Stock Price",           "Over 5"),
    ("dividendYield", "Dividend Yield",        "No Dividend"),
    ("revenueGrowth", "Revenue Growth",        "Over 20%"),
    ("averageVolume", "Average Volume",        "Over 100K"),
    ("epsNextYear",   "EPS Growth Next Year",  "Over 0%"),
    ("high52ch",      "Price Change 52W High", None),   # no good preset; client-side only
]
