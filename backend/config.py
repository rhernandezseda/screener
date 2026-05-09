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
    ("dividendYield", "Dividend Yield",        ("Under", "1")),  # < 1% (includes no dividend)
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
# Day of week to run the screener (0=Monday … 6=Sunday). Set to -1 to disable.
REFRESH_DAY_OF_WEEK = 6   # Sunday
REFRESH_INTERVAL_HOURS = 0  # legacy — unused when REFRESH_DAY_OF_WEEK >= 0

# ── Shortlist agent schedule ───────────────────────────────────────────────────
# Days (0=Mon … 6=Sun) on which the agent runs at SHORTLIST_LOCAL_TIME.
# Also runs once on server startup.
SHORTLIST_DAYS = {0, 1, 2, 3, 5}  # Mon, Tue, Wed, Thu, Sat
SHORTLIST_LOCAL_TIME = (21, 30)    # 9:30 PM local
SHORTLIST_TIMEZONE = "Europe/Madrid"
