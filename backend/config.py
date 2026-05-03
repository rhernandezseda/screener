"""
config.py — Screener filter configuration

Edit the values below to change which stocks pass the screener.
Run `python screener.py` after saving to apply the new filters.
"""

# ── Screener filters ──────────────────────────────────────────────────────────
# Each entry: (checkbox_id, label, action)
#
# action can be:
#   - A preset string   → click that preset button in the dropdown (e.g. "Over 20%")
#   - ("Over", value)   → click "Over" operator then type the value
#   - None              → only add the column; no server-side filter applied
#
# These are the ONLY filters applied. Display-only columns (epsGrowth, epsGrowthQ)
# are handled separately in DISPLAY_COLUMNS below.

SITE_FILTERS = [
    ("marketCap",     "Market Cap",            ("Over", "2000")),   # > $2B (site unit: millions)
    ("price",         "Stock Price",           ("Over", "9")),      # > $9
    ("dividendYield", "Dividend Yield",        "No Dividend"),
    ("revenueGrowth", "Revenue Growth",        "Over 20%"),
    ("averageVolume", "Average Volume",        ("Over", "200000")), # > 200K
    ("epsNextYear",   "EPS Growth Next Year",  "Over 0%"),
    ("high52ch",      "Price Change 52W High", ("Over", "-20")),    # within 20% of 52W high
]

# ── Display-only columns ──────────────────────────────────────────────────────
# Added to the table for card display but NOT used as filters.

DISPLAY_COLUMNS = [
    ("epsGrowth",  "EPS Growth"),       # EPS growth YoY (annual)
    ("epsGrowthQ", "EPS Growth (Q)"),   # EPS growth QoQ (quarterly)
    ("exchange",   "Exchange"),         # NYSE / NASDAQ — used for TradingView symbol prefix
]

# ── Auto-refresh schedule ─────────────────────────────────────────────────────
# How often the backend automatically re-runs the screener (in hours).
# Set to 0 to disable auto-refresh.
REFRESH_INTERVAL_HOURS = 6
