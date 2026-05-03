"""
shortlist.py — Momentum ranking agent for screened stocks.

Reads screener.json, enriches each ticker with live yfinance data,
sends a single Claude Sonnet call to score and rank them, saves top 10
to output/data/shortlist.json.

Usage:
    python shortlist.py
"""

import json
import os
import sys
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

import anthropic
import yfinance as yf

OUTPUT_DIR = Path(__file__).parent.parent / "output"
DATA_DIR = OUTPUT_DIR / "data"
SCREENER_JSON = DATA_DIR / "screener.json"
SHORTLIST_JSON = DATA_DIR / "shortlist.json"

# Sector ETF map — used for sector flow signal
SECTOR_ETF = {
    "Technology":             "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Healthcare":             "XLV",
    "Financials":             "XLF",
    "Industrials":            "XLI",
    "Energy":                 "XLE",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
    "Basic Materials":        "XLB",
}


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_vix():
    try:
        info = yf.Ticker("^VIX").info
        return info.get("regularMarketPrice")
    except Exception:
        return None


def fetch_sector_flow(sector: str) -> dict:
    """Return 5-day % change for the sector ETF."""
    etf = SECTOR_ETF.get(sector)
    if not etf:
        return {"etf": None, "flow_5d_pct": None}
    try:
        hist = yf.Ticker(etf).history(period="5d", interval="1d")
        if len(hist) < 2:
            return {"etf": etf, "flow_5d_pct": None}
        pct = round((hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100, 2)
        return {"etf": etf, "flow_5d_pct": pct}
    except Exception:
        return {"etf": etf, "flow_5d_pct": None}


def fetch_ticker_data(screener_row: dict) -> dict:
    """Fetch live yfinance data for one ticker and merge with screener fields."""
    ticker = screener_row["ticker"]
    base = {
        # Pass through everything already in screener.json — no re-fetching
        "ticker":          ticker,
        "name":            screener_row.get("name", ""),
        "market_cap":      screener_row.get("market_cap", ""),
        "price_screener":  screener_row.get("price", ""),
        "revenue_growth":  screener_row.get("revenue_growth", ""),
        "avg_volume_screener": screener_row.get("avg_volume", ""),
        "eps_growth_yoy":  screener_row.get("eps_growth", ""),
        "eps_growth_qoq":  screener_row.get("eps_growth_q", ""),
        "eps_next_year":   screener_row.get("eps_next_year", ""),
        "high_52w_chg":    screener_row.get("high_52w_chg", ""),
        "exchange":        screener_row.get("exchange", ""),
    }

    try:
        t = yf.Ticker(ticker)
        info = t.info

        # 5-day price + volume history
        hist = t.history(period="5d", interval="1d")
        hist5 = []
        for dt, row in hist.iterrows():
            hist5.append({
                "date":   str(dt.date()),
                "open":   round(float(row["Open"]), 2),
                "high":   round(float(row["High"]), 2),
                "low":    round(float(row["Low"]), 2),
                "close":  round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })

        today = hist5[-1] if hist5 else {}
        prev  = hist5[-2] if len(hist5) >= 2 else {}

        # Price position within today's range (0–100%)
        day_high = today.get("high", 0)
        day_low  = today.get("low", 0)
        day_close = today.get("close", 0)
        range_position = None
        if day_high and day_low and day_high != day_low:
            range_position = round((day_close - day_low) / (day_high - day_low) * 100, 1)

        # Volume ratio vs 20-day avg
        avg_vol = info.get("averageVolume") or info.get("averageVolume10days")
        today_vol = today.get("volume")
        vol_ratio = round(today_vol / avg_vol, 2) if today_vol and avg_vol else None

        # Volume trend: is each of last 3 days > prior day?
        vol_expanding = None
        if len(hist5) >= 3:
            vols = [d["volume"] for d in hist5[-3:]]
            vol_expanding = vols[1] > vols[0] and vols[2] > vols[1]

        # Gap check (today's open vs prior close)
        gap_pct = None
        if today.get("open") and prev.get("close") and prev["close"] > 0:
            gap_pct = round((today["open"] / prev["close"] - 1) * 100, 2)

        # 52W range position
        hi52 = info.get("fiftyTwoWeekHigh")
        lo52 = info.get("fiftyTwoWeekLow")
        price = info.get("regularMarketPrice") or day_close
        range_52w_pct = None
        if hi52 and lo52 and hi52 != lo52:
            range_52w_pct = round((price - lo52) / (hi52 - lo52) * 100, 1)

        # Bid-ask spread %
        bid = info.get("bid")
        ask = info.get("ask")
        spread_pct = None
        if bid and ask and bid > 0:
            spread_pct = round((ask - bid) / bid * 100, 3)

        # Price change today %
        prev_close = info.get("previousClose")
        change_pct = None
        if price and prev_close and prev_close > 0:
            change_pct = round((price / prev_close - 1) * 100, 2)

        base.update({
            "price_live":        price,
            "prev_close":        prev_close,
            "change_pct_today":  change_pct,
            "day_high":          day_high,
            "day_low":           day_low,
            "range_position_pct": range_position,   # % position in today's range
            "volume_today":      today_vol,
            "avg_volume_20d":    avg_vol,
            "volume_ratio":      vol_ratio,          # today vol / 20d avg
            "volume_expanding":  vol_expanding,      # last 3 days each > prior
            "gap_pct":           gap_pct,            # today open vs prior close
            "high_52w":          hi52,
            "low_52w":           lo52,
            "range_52w_pct":     range_52w_pct,      # where price sits in 52W range
            "short_pct_float":   round(info.get("shortPercentOfFloat", 0) * 100, 2) if info.get("shortPercentOfFloat") else None,
            "days_to_cover":     info.get("shortRatio"),
            "float_shares":      info.get("floatShares"),
            "bid_ask_spread_pct": spread_pct,
            "beta":              info.get("beta"),
            "forward_pe":        info.get("forwardPE"),
            "hist_5d":           hist5,
        })

    except Exception as e:
        base["fetch_error"] = str(e)

    return base


def fetch_all_tickers(stocks: list) -> list:
    """Fetch yfinance data for all tickers concurrently."""
    results = [None] * len(stocks)
    errors = []

    def worker(i, row):
        try:
            results[i] = fetch_ticker_data(row)
            print(f"  ✓ {row['ticker']}", flush=True)
        except Exception as e:
            results[i] = {"ticker": row["ticker"], "fetch_error": str(e)}
            errors.append(row["ticker"])
            print(f"  ✗ {row['ticker']}: {e}", flush=True)

    threads = [threading.Thread(target=worker, args=(i, row)) for i, row in enumerate(stocks)]
    # Run in batches of 20 to avoid hammering yfinance
    batch_size = 20
    for i in range(0, len(threads), batch_size):
        batch = threads[i:i + batch_size]
        for t in batch:
            t.start()
        for t in batch:
            t.join()

    return [r for r in results if r is not None]


# ── Claude scoring ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a momentum trading research agent specializing in US equities.
Your job is to evaluate a list of screened companies and rank the top 10 by their quality
as SHORT-TERM trading opportunities — setups actionable TODAY.

You will receive enriched data for each ticker. Some fields come directly from the screener
(already validated), others from live yfinance data. Use all of it.

SCORING MODEL (100 points total):

A. Breakout readiness (max 35 pts) — price and volume action RIGHT NOW
   +12  Price within 3% of 52W high (nearest resistance proxy)
   +6   Price within 3–7% of 52W high
   +12  volume_ratio > 2.0 (today's volume > 2x 20-day avg)
   +6   volume_ratio 1.5–2.0
   +8   volume_expanding = true (each of last 3 days > prior)
   +5   range_position_pct > 75 (strong close, price in top 25% of day's range)

B. Squeeze potential (max 30 pts) — fuel for acceleration
   +15  short_pct_float > 20%
   +8   short_pct_float 10–20%
   +10  days_to_cover > 5
   +5   float_shares < 20,000,000 (low float)

C. Momentum quality (max 25 pts) — trend strength
   +8   range_52w_pct > 70 (price near 52W highs)
   +10  revenue_growth > 30% (from screener — already validated)
   +7   sector_flow_5d_pct > 0 (positive sector ETF flow this week)

D. Risk adjustment (start +10, subtract for risks)
   −8   earnings within 5 trading days (use earnings_date if known)
   −4   bid_ask_spread_pct > 0.5%
   −3   vix > 25
   −5   change_pct_today < −5% (falling knife)

AUTO-DISQUALIFICATION (remove entirely before scoring):
   - avg_volume_20d < 500,000
   - bid_ask_spread_pct > 1%
   - price_live < 5
   - earnings within 3 trading days

IMPORTANT RULES:
- Use ONLY the data provided. Do not invent or assume any value not present.
- If a field is null/missing, score that signal as 0 and note it.
- Be skeptical: if confidence in a data point is low, upgrade risk flag to Medium.
- Do not recommend — you surface setups, the human decides.
- When in doubt on disqualification, disqualify.

OUTPUT: Return a single valid JSON object with this exact structure:
{
  "generated_at": "<ISO timestamp>",
  "vix": <number or null>,
  "top10": [
    {
      "rank": 1,
      "ticker": "...",
      "score": <0-100>,
      "setup_type": "Breakout + squeeze | Breakout only | Squeeze only | Momentum only",
      "breakout_pts": <0-35>,
      "squeeze_pts": <0-30>,
      "momentum_pts": <0-25>,
      "risk_pts": <0-10>,
      "si_pct": <number or null>,
      "days_to_cover": <number or null>,
      "dist_to_52w_high_pct": <number or null>,
      "volume_ratio": <number or null>,
      "change_pct_today": <number or null>,
      "risk_flag": "Low | Medium | High",
      "thesis": "<1-2 sentence thesis using specific data points>"
    }
  ],
  "top_pick_rationale": "<3-5 sentences on #1 pick with specific data. State what would invalidate the thesis.>",
  "disqualified": ["TICK1", "TICK2"],
  "notes": "<any caveats about missing data or low-confidence signals>"
}"""


def call_claude(enriched: list, vix, sector_flows: dict) -> dict:
    """Send all enriched ticker data to Claude Sonnet for scoring."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # Build compact payload — only fields Claude needs, skip raw hist_5d bulk
    tickers_payload = []
    for d in enriched:
        sector = None
        # Try to infer sector from name (not in screener.json — use exchange as proxy)
        flow = sector_flows.get(d.get("ticker", ""), {})
        tickers_payload.append({
            "ticker":              d.get("ticker"),
            "name":                d.get("name"),
            "market_cap":          d.get("market_cap"),
            "price":               d.get("price_live") or d.get("price_screener"),
            "change_pct_today":    d.get("change_pct_today"),
            "revenue_growth":      d.get("revenue_growth"),
            "eps_growth_yoy":      d.get("eps_growth_yoy"),
            "eps_next_year":       d.get("eps_next_year"),
            "high_52w_chg":        d.get("high_52w_chg"),
            "range_52w_pct":       d.get("range_52w_pct"),
            "range_position_pct":  d.get("range_position_pct"),
            "volume_today":        d.get("volume_today"),
            "avg_volume_20d":      d.get("avg_volume_20d"),
            "volume_ratio":        d.get("volume_ratio"),
            "volume_expanding":    d.get("volume_expanding"),
            "gap_pct":             d.get("gap_pct"),
            "high_52w":            d.get("high_52w"),
            "low_52w":             d.get("low_52w"),
            "short_pct_float":     d.get("short_pct_float"),
            "days_to_cover":       d.get("days_to_cover"),
            "float_shares":        d.get("float_shares"),
            "bid_ask_spread_pct":  d.get("bid_ask_spread_pct"),
            "beta":                d.get("beta"),
            "forward_pe":          d.get("forward_pe"),
            "sector_flow_5d_pct":  flow.get("flow_5d_pct"),
            "sector_etf":          flow.get("etf"),
            "fetch_error":         d.get("fetch_error"),
        })

    user_msg = json.dumps({
        "vix": vix,
        "ticker_count": len(tickers_payload),
        "tickers": tickers_payload,
    }, indent=2)

    print(f"  Calling Claude Sonnet with {len(tickers_payload)} tickers...", flush=True)
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_shortlist():
    print("\n=== Shortlist Agent ===", flush=True)

    if not SCREENER_JSON.exists():
        print("  screener.json not found — run screener first.", flush=True)
        sys.exit(1)

    data = json.loads(SCREENER_JSON.read_text())
    stocks = data.get("stocks", [])
    print(f"  {len(stocks)} tickers from screener.", flush=True)

    # Fetch VIX once
    print("  Fetching VIX...", flush=True)
    vix = fetch_vix()
    print(f"  VIX: {vix}", flush=True)

    # Fetch sector flows for unique sectors
    # Sector is not in screener.json — fetch it from yfinance info for a sample
    # For sector flows we'll fetch all standard ETFs upfront
    print("  Fetching sector ETF flows...", flush=True)
    sector_flows_by_etf = {}
    for sector, etf in SECTOR_ETF.items():
        flow = fetch_sector_flow(sector)
        sector_flows_by_etf[etf] = flow
        print(f"    {etf} ({sector}): {flow.get('flow_5d_pct')}%", flush=True)

    # Enrich all tickers with yfinance
    print(f"\n  Fetching live data for {len(stocks)} tickers...", flush=True)
    enriched = fetch_all_tickers(stocks)

    # Map sector flows to tickers via their sector (fetched in yfinance info)
    # Since screener.json has no sector, we attach flows during enrichment
    # Build a per-ticker sector flow map using the sector from yfinance info
    sector_flows_by_ticker = {}
    for d in enriched:
        ticker = d.get("ticker", "")
        # yfinance doesn't return sector in .info for all tickers — skip if missing
        # Claude will see sector_flow as null and score it 0
        sector_flows_by_ticker[ticker] = {"flow_5d_pct": None, "etf": None}

    # Re-enrich sector flows by fetching sector from yfinance info
    print("\n  Mapping sector flows...", flush=True)
    sector_cache = {}

    def get_sector_flow(ticker):
        try:
            info = yf.Ticker(ticker).info
            sector = info.get("sector", "")
            if sector not in sector_cache:
                sector_cache[sector] = fetch_sector_flow(sector)
            sector_flows_by_ticker[ticker] = sector_cache[sector]
        except Exception:
            pass

    sector_threads = [threading.Thread(target=get_sector_flow, args=(d["ticker"],)) for d in enriched]
    for t in sector_threads:
        t.start()
    for t in sector_threads:
        t.join()

    # Attach sector flow to each enriched row
    for d in enriched:
        flow = sector_flows_by_ticker.get(d["ticker"], {})
        d["sector_flow_5d_pct"] = flow.get("flow_5d_pct")
        d["sector_etf"] = flow.get("etf")

    # Call Claude
    print("\n  Sending to Claude Sonnet...", flush=True)
    result = call_claude(enriched, vix, sector_flows_by_ticker)

    # Add metadata
    result["screener_timestamp"] = data.get("timestamp")
    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    SHORTLIST_JSON.write_text(json.dumps(result, indent=2))
    print(f"\n  Saved: {SHORTLIST_JSON}", flush=True)

    top = result.get("top10", [])
    if top:
        print(f"\n  Top 3 picks:", flush=True)
        for pick in top[:3]:
            print(f"    #{pick['rank']} {pick['ticker']} — score {pick['score']} — {pick['setup_type']}", flush=True)

    return result


if __name__ == "__main__":
    run_shortlist()
