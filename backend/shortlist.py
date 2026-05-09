"""
shortlist.py — Momentum ranking agent for screened stocks.

Reads screener.json, enriches each ticker with live yfinance data,
sends a Claude Sonnet call to score and rank them, then fetches chart
images for the top 10 and runs a second Claude call (with extended
thinking) for chart pattern recognition. Saves results to
output/data/shortlist.json.

Usage:
    python shortlist.py
"""

import base64
import json
import os
import sys
import threading
import warnings
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore")

import anthropic
import httpx
import numpy as np
import yfinance as yf

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False

OUTPUT_DIR = Path(__file__).parent.parent / "output"
DATA_DIR = OUTPUT_DIR / "data"
SCREENER_JSON = DATA_DIR / "screener.json"
SHORTLIST_JSON = DATA_DIR / "shortlist.json"

CHART_IMG_API_KEY = os.environ.get("CHART_IMG_API_KEY")

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

def fetch_market_regime() -> dict:
    """
    Compute market regime from VIX + SPY SMA50/SMA200.
    Returns a structured dict used as context in the scoring prompt.
    """
    result = {
        "vix": None,
        "vix_regime": "unknown",
        "spy_trend": "unknown",
        "regime_label": "Unknown",
        "score_multiplier": 1.0,
        "notes": "",
    }
    try:
        result["vix"] = yf.Ticker("^VIX").info.get("regularMarketPrice")
    except Exception:
        pass

    try:
        spy_hist = yf.Ticker("SPY").history(period="200d", interval="1d")["Close"]
        spy_price = float(spy_hist.iloc[-1])
        sma50 = float(spy_hist.rolling(50).mean().iloc[-1])
        sma200 = float(spy_hist.rolling(200).mean().iloc[-1])
        if spy_price > sma50 > sma200:
            result["spy_trend"] = "bull_trending"
        elif spy_price > sma200:
            result["spy_trend"] = "bull_choppy"
        elif spy_price > sma50:
            result["spy_trend"] = "bear_rally"
        else:
            result["spy_trend"] = "bear"
    except Exception:
        pass

    vix = result["vix"]
    if vix is not None:
        if vix < 15:
            result["vix_regime"] = "low"
        elif vix < 20:
            result["vix_regime"] = "normal"
        elif vix < 25:
            result["vix_regime"] = "elevated"
        elif vix < 35:
            result["vix_regime"] = "high"
        else:
            result["vix_regime"] = "crisis"

    # Composite label + score multiplier
    trend = result["spy_trend"]
    vix_r = result["vix_regime"]
    if trend == "bull_trending" and vix_r in ("low", "normal"):
        result["regime_label"] = "Bull market, normal volatility — full scoring"
        result["score_multiplier"] = 1.0
        result["notes"] = "Standard thresholds apply. Breakout setups reliable."
    elif trend == "bull_trending" and vix_r == "elevated":
        result["regime_label"] = "Bull trend, elevated volatility — slightly cautious"
        result["score_multiplier"] = 0.9
        result["notes"] = "Require volume confirmation on breakouts. Tighten stops."
    elif trend == "bull_choppy":
        result["regime_label"] = "Choppy bull — be selective"
        result["score_multiplier"] = 0.85
        result["notes"] = "Require 2+ confirming signals. Prefer squeeze setups over pure breakouts."
    elif trend == "bear_rally":
        result["regime_label"] = "Bear market rally — high caution"
        result["score_multiplier"] = 0.7
        result["notes"] = "Only highest-conviction setups. Rallies in bear markets fail often."
    elif trend == "bear" or vix_r in ("high", "crisis"):
        result["regime_label"] = "Bear market / high volatility — defensive"
        result["score_multiplier"] = 0.6
        result["notes"] = "Disqualify pure breakout setups. Only catalyst-driven squeeze plays if any."
    else:
        result["regime_label"] = "Uncertain regime"
        result["score_multiplier"] = 0.85
        result["notes"] = "Regime data incomplete — apply moderate caution."

    return result


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
        day_high  = today.get("high", 0)
        day_low   = today.get("low", 0)
        day_close = today.get("close", 0)
        range_position = None
        if day_high and day_low and day_high != day_low:
            range_position = round((day_close - day_low) / (day_high - day_low) * 100, 1)

        # Volume ratio vs 20-day avg
        avg_vol   = info.get("averageVolume") or info.get("averageVolume10days")
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
            "range_position_pct": range_position,
            "volume_today":      today_vol,
            "avg_volume_20d":    avg_vol,
            "volume_ratio":      vol_ratio,
            "volume_expanding":  vol_expanding,
            "gap_pct":           gap_pct,
            "high_52w":          hi52,
            "low_52w":           lo52,
            "range_52w_pct":     range_52w_pct,
            "short_pct_float":   round(info.get("shortPercentOfFloat", 0) * 100, 2) if info.get("shortPercentOfFloat") else None,
            "days_to_cover":     info.get("shortRatio"),
            "float_shares":      info.get("floatShares"),
            "bid_ask_spread_pct": spread_pct,
            "beta":              info.get("beta"),
            "forward_pe":        info.get("forwardPE"),
            "hist_5d":           hist5,
            "ta_indicators":     compute_ta_indicators_from_history(ticker),
        })

        # Earnings date — separate try so a failure here doesn't drop all yfinance data
        try:
            cal = t.calendar
            earnings_dates = cal.get("Earnings Date", []) if cal else []
            next_earnings = earnings_dates[0] if earnings_dates else None
            if next_earnings:
                if isinstance(next_earnings, datetime):
                    next_earnings = next_earnings.date()
                today = date.today()
                days_to_earnings = (next_earnings - today).days
                base["next_earnings_date"] = str(next_earnings)
                base["days_to_earnings"] = days_to_earnings
            else:
                base["next_earnings_date"] = None
                base["days_to_earnings"] = None
        except Exception:
            base["next_earnings_date"] = None
            base["days_to_earnings"] = None

    except Exception as e:
        base["fetch_error"] = str(e)

    return base


def compute_ta_indicators_from_history(ticker: str) -> dict:
    """
    Fetch 60-day OHLCV history and compute TA indicators.
    Returns a dict of indicator values, or empty dict if talib unavailable.
    """
    if not _TALIB_AVAILABLE:
        return {}
    try:
        hist = yf.Ticker(ticker).history(period="60d", interval="1d")
        if len(hist) < 20:
            return {}

        o = hist["Open"].values.astype(float)
        h = hist["High"].values.astype(float)
        lo = hist["Low"].values.astype(float)
        c = hist["Close"].values.astype(float)

        result = {}

        # RSI(14)
        rsi = talib.RSI(c, timeperiod=14)
        result["rsi_14"] = round(float(rsi[-1]), 1) if not np.isnan(rsi[-1]) else None

        # MACD(12,26,9)
        macd, signal, hist_macd = talib.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)
        if not np.isnan(hist_macd[-1]):
            result["macd_hist"] = round(float(hist_macd[-1]), 4)
            result["macd_bullish"] = bool(hist_macd[-1] > 0)
            result["macd_crossover"] = (
                not np.isnan(hist_macd[-2]) and
                ((hist_macd[-2] < 0 and hist_macd[-1] > 0) or
                 (hist_macd[-2] > 0 and hist_macd[-1] < 0))
            )
        else:
            result["macd_hist"] = None
            result["macd_bullish"] = None
            result["macd_crossover"] = None

        # Bollinger Band squeeze
        upper, middle, lower = talib.BBANDS(c, timeperiod=20)
        if not np.isnan(upper[-1]) and middle[-1] > 0:
            bb_width = round(float((upper[-1] - lower[-1]) / middle[-1] * 100), 2)
            result["bb_width_pct"] = bb_width
            widths = (upper - lower) / middle * 100
            valid_widths = widths[~np.isnan(widths)]
            if len(valid_widths) >= 10:
                result["bb_squeeze"] = bool(bb_width <= float(np.percentile(valid_widths, 20)))
            else:
                result["bb_squeeze"] = None
        else:
            result["bb_width_pct"] = None
            result["bb_squeeze"] = None

        # ATR(14) as % of close
        atr = talib.ATR(h, lo, c, timeperiod=14)
        if not np.isnan(atr[-1]) and c[-1] > 0:
            result["atr_pct"] = round(float(atr[-1] / c[-1] * 100), 2)
        else:
            result["atr_pct"] = None

        # Candlestick patterns (last candle)
        patterns_bullish = []
        patterns_bearish = []
        candle_checks = [
            ("Hammer",         talib.CDLHAMMER,          "bullish"),
            ("Inv Hammer",     talib.CDLINVERTEDHAMMER,   "bullish"),
            ("Engulfing",      talib.CDLENGULFING,        None),
            ("Morning Star",   talib.CDLMORNINGSTAR,      "bullish"),
            ("Evening Star",   talib.CDLEVENINGSTAR,      "bearish"),
            ("Doji",           talib.CDLDOJI,             None),
            ("Harami",         talib.CDLHARAMI,           None),
            ("Shooting Star",  talib.CDLSHOOTINGSTAR,     "bearish"),
            ("3 White Soldiers",talib.CDL3WHITESOLDIERS,  "bullish"),
            ("3 Black Crows",  talib.CDL3BLACKCROWS,      "bearish"),
        ]
        for label, fn, direction in candle_checks:
            try:
                val = int(fn(o, h, lo, c)[-1])
                if val == 0:
                    continue
                d = direction if direction else ("bullish" if val > 0 else "bearish")
                if d == "bullish":
                    patterns_bullish.append(label)
                else:
                    patterns_bearish.append(label)
            except Exception:
                continue

        result["candle_patterns_bullish"] = patterns_bullish
        result["candle_patterns_bearish"] = patterns_bearish

        return result

    except Exception as e:
        return {"ta_error": str(e)}


def fetch_all_tickers(stocks: list) -> list:
    results = [None] * len(stocks)

    def worker(i, row):
        try:
            results[i] = fetch_ticker_data(row)
            print(f"  ✓ {row['ticker']}", flush=True)
        except Exception as e:
            results[i] = {"ticker": row["ticker"], "fetch_error": str(e)}
            print(f"  ✗ {row['ticker']}: {e}", flush=True)

    threads = [threading.Thread(target=worker, args=(i, row)) for i, row in enumerate(stocks)]
    batch_size = 20
    for i in range(0, len(threads), batch_size):
        batch = threads[i:i + batch_size]
        for t in batch:
            t.start()
        for t in batch:
            t.join()

    return [r for r in results if r is not None]


# ── Chart image fetching ──────────────────────────────────────────────────────

def fetch_chart_image(ticker: str, exchange: str) -> Optional[str]:
    """
    Fetch a 60-day daily candlestick chart PNG from chart-img.com.
    Returns base64-encoded PNG string, or None on failure.
    """
    if not CHART_IMG_API_KEY:
        print(f"  [chart] No CHART_IMG_API_KEY — skipping {ticker}", flush=True)
        return None

    # Normalise exchange prefix
    exch = (exchange or "").upper().strip()
    if exch not in ("NYSE", "NASDAQ", "AMEX"):
        exch = "NASDAQ"

    symbol = f"{exch}:{ticker}"
    url = (
        "https://api.chart-img.com/v1/tradingview/advanced-chart"
        f"?symbol={symbol}"
        "&interval=1D"
        "&studies=[]"
        "&width=800"
        "&height=500"
    )
    try:
        resp = httpx.get(
            url,
            headers={"x-api-key": CHART_IMG_API_KEY},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [chart] {ticker}: HTTP {resp.status_code}", flush=True)
            return None
        b64 = base64.standard_b64encode(resp.content).decode("utf-8")
        print(f"  [chart] ✓ {ticker}", flush=True)
        return b64
    except Exception as e:
        print(f"  [chart] ✗ {ticker}: {e}", flush=True)
        return None


def fetch_charts_for_top10(top10: list) -> dict:
    """Fetch chart images for top 10 picks. Returns {ticker: base64_png}."""
    results = {}
    for pick in top10:
        ticker   = pick["ticker"]
        exchange = pick.get("exchange", "")
        b64      = fetch_chart_image(ticker, exchange)
        if b64:
            results[ticker] = b64
    return results


# ── Claude scoring ────────────────────────────────────────────────────────────

SCORING_SYSTEM_PROMPT = """You are a momentum trading research agent specializing in US equities.
Your job is to evaluate a list of screened companies and rank them by their quality as SHORT-TERM
trading opportunities — meaning setups that are actionable TODAY, not next week or next quarter.

## MARKET REGIME CONTEXT
The user message includes a `market_regime` object. Read it FIRST and calibrate your scoring:
- `regime_label`: overall market state (bull_trending | bull_choppy | bear_rally | bear)
- `score_multiplier`: apply this to ALL final scores before returning (0.6 = crisis, 1.0 = ideal)
- `vix_regime`: current VIX bucket (low | normal | elevated | high | crisis)
- `notes`: regime reasoning — use this to set your qualitative risk threshold

In bear or high-VIX regimes: raise the bar for every signal, upgrade risk flags one level, shrink top10 if few setups qualify.
In bull_trending low-VIX: you can be more generous with borderline setups.
Always state the regime you applied in the `notes` field of your output.

Each ticker includes a `ta_indicators` object with pre-computed technical signals:
- `rsi_14`: RSI(14). Overbought >70, oversold <30. Best breakout zone: 55–70 (momentum, not extended).
- `macd_bullish`: true if MACD histogram is positive (bullish momentum).
- `macd_crossover`: true if histogram crossed zero in the last bar (fresh signal — high weight).
- `bb_squeeze`: true if Bollinger Bands are in their tightest 20% of recent range (coiled spring).
- `bb_width_pct`: absolute BB width as % of price. Lower = tighter.
- `atr_pct`: ATR(14) as % of price. Use to assess volatility risk.
- `candle_patterns_bullish`: list of bullish candlestick patterns on latest candle (e.g. ["Hammer", "Engulfing"]).
- `candle_patterns_bearish`: list of bearish candlestick patterns on latest candle.

Use these to improve scoring precision:
- A bb_squeeze=true + volume_ratio>1.5 is a very high-conviction breakout setup — weight heavily.
- macd_crossover=true adds +5 bonus points to breakout score (fresh momentum confirmation).
- rsi_14 > 75 signals extension risk — upgrade risk_flag to at least Medium.
- Bearish candle patterns should upgrade risk_flag by one level.
- Bullish candle patterns (especially Engulfing or 3 White Soldiers) add +3 to breakout score.

## SCORING MODEL (100 points total)

### A. Breakout readiness (max 40 points)
| Signal | Points |
|---|---|
| Price within 3% of key resistance (use high_52w as proxy) | +12 |
| Price within 3–7% of key resistance | +6 |
| volume_ratio > 2.0 (today > 2x 20-day avg) | +12 |
| volume_ratio 1.5–2.0 | +6 |
| volume_expanding = true (each of last 3 days > prior) | +8 |
| range_position_pct > 75 (close in top 25% of day's range) | +5 |
| price above VWAP (use range_position_pct > 50 as proxy) | +3 |
| macd_crossover = true (bonus) | +5 |
| bullish candle pattern present (bonus) | +3 |

### B. Squeeze potential (max 30 points)
| Signal | Points |
|---|---|
| short_pct_float > 20% | +15 |
| short_pct_float 10–20% | +8 |
| days_to_cover > 5 | +10 |
| float_shares < 20,000,000 | +5 |
| bb_squeeze = true (bonus: coiled setup) | +5 |

### C. Momentum quality (max 20 points)
| Signal | Points |
|---|---|
| range_52w_pct > 70 (price near 52W highs) | +8 |
| revenue_growth > 30% (already validated by screener) | +5 |
| sector_flow_5d_pct > 0 | +7 |

### D. Risk adjustment (start +10, subtract for risks)
| Risk factor | Deduction |
|---|---|
| days_to_earnings 5–10 (earnings warning zone) | −8 |
| bid_ask_spread_pct > 0.5% | −4 |
| VIX > 25 | −3 |
| change_pct_today < −5% | −5 |
| rsi_14 > 75 (extended, overheated) | −3 |
| bearish candle pattern present | −3 |

## AUTO-DISQUALIFICATION (remove entirely before scoring)
- avg_volume_20d < 500,000
- bid_ask_spread_pct > 1%
- price_live < 5
- days_to_earnings < 5 (earnings too close — use the actual `days_to_earnings` field, not a guess)

## IMPORTANT RULES
- Use ONLY the data provided. Do not invent or assume any value not present.
- If a field is null/missing, score that signal as 0 and note it.
- If ta_indicators is empty or missing, score TA signals as 0.
- Be skeptical: if confidence in a data point is low, upgrade risk flag to Medium.
- Do not recommend — you surface setups, the human decides.
- When in doubt on disqualification, disqualify.

## OUTPUT
Return a single valid JSON object with this exact structure:
{{
  "generated_at": "<ISO timestamp>",
  "vix": <number or null>,
  "top10": [
    {{
      "rank": 1,
      "ticker": "...",
      "exchange": "...",
      "score": <0-100>,
      "setup_type": "Breakout + squeeze | Breakout only | Squeeze only | Momentum only",
      "breakout_pts": <0-45>,
      "squeeze_pts": <0-35>,
      "momentum_pts": <0-20>,
      "risk_pts": <0-10>,
      "si_pct": <number or null>,
      "days_to_cover": <number or null>,
      "dist_to_52w_high_pct": <number or null>,
      "volume_ratio": <number or null>,
      "change_pct_today": <number or null>,
      "risk_flag": "Low | Medium | High",
      "thesis": "<1-2 sentence thesis using specific data points including any TA signals>"
    }}
  ],
  "top_pick_rationale": "<3-5 sentences on #1 pick with specific data including TA context. State what would invalidate the thesis.>",
  "disqualified": ["TICK1", "TICK2"],
  "notes": "<any caveats about missing data, low-confidence signals, or TA library status>"
}}"""


PATTERN_SYSTEM_PROMPT = """You are a technical analysis specialist combining chart image analysis with pre-computed indicator data.

For each stock you will receive:
1. A candlestick chart image (60-day daily)
2. Pre-computed TA indicators: RSI, MACD, Bollinger Band squeeze status, ATR, and candlestick patterns detected algorithmically

Your job is to give a COMBINED assessment — use the image to validate and enrich what the indicators say.
The indicators anchor your analysis; the image reveals visual context indicators cannot capture:
trend line quality, support/resistance confluence, volume profile shape, and whether a setup looks
clean or messy.

## HOW TO COMBINE IMAGE + INDICATORS

- If `bb_squeeze=true` AND the chart shows a tight sideways consolidation with visible volume decline → strong squeeze confirmation, high confidence.
- If `macd_crossover=true` AND the chart shows a fresh move off support → flag as "Triggered" rather than "Formed".
- If `candle_patterns_bullish` lists a pattern AND you see it clearly in the image → confirm it; otherwise ignore the indicator.
- If the chart looks bearish/choppy but indicators are bullish → note the discrepancy in your assessment and downgrade confidence.
- The chart image overrules indicator data when there is a clear conflict — indicators can lag, images do not lie.

## CORE PRINCIPLE: DEFAULT TO "NONE"
You are looking for unambiguous, textbook-quality patterns ONLY. Your strong default is "none".
A pattern must meet ALL of its criteria — partial resemblance does not qualify.
Ask yourself: "Would an experienced technical trader immediately recognize this without me pointing it out?"
If the answer is anything other than a clear yes, report "none".
It is far better to miss a pattern than to report a false positive.

## BULLISH PATTERNS — report only if ALL criteria are met

**Cup & handle** — Smooth rounded U-shape ≥4 weeks; handle drifts ≤50% of cup depth; volume dries up through base and handle.
**Bull flag** — Near-vertical surge ≥10% forming flagpole; tight parallel consolidation drifting slightly downward ≤3 weeks; volume lower during flag.
**Ascending triangle** — Flat resistance tested ≥3 times; ≥3 higher lows converging toward ceiling; ≥3 weeks.
**Double bottom** — Two distinct troughs within 2% of each other separated by a visible peak; ≥3 weeks.
**Volatility contraction (VCP)** — ≥3 clearly visible contractions, each smaller; volume visibly declining.

## BEARISH PATTERNS — report only if ALL criteria are met

**Head & shoulders** — Three peaks with center visibly higher; shoulders within 3%; clear neckline; ≥4 weeks.
**Bear flag** — Near-vertical drop ≥10%; tight upward consolidation; volume lower during flag.
**Descending triangle** — Flat support tested ≥3 times; ≥3 lower highs converging; ≥3 weeks.
**Double top** — Two peaks within 2% of each other separated by visible trough; ≥3 weeks.
**Rising wedge** — Two converging upward trendlines each touched ≥3 times; volume declining; ≥3 weeks.

## CONFIDENCE LEVELS
- `Developing` — structural criteria met but pattern not yet complete
- `Formed` — complete, awaiting breakout/breakdown confirmation
- `Triggered` — broken out or broken down with volume confirmation

## OUTPUT FORMAT
Return a single valid JSON object:
{{
  "patterns": [
    {{
      "ticker": "...",
      "bullish_pattern": "Cup & handle (Formed)" or "none",
      "bearish_pattern": "Head & shoulders (Developing)" or "none",
      "ta_chart_notes": "<one sentence: did the chart confirm, contradict, or add context to the indicator data? Keep it tight.>"
    }}
  ]
}}

One entry per ticker. At most one bullish and one bearish pattern per ticker.
If multiple partially qualify, report only the strongest and clearest one.
Never add caveats or qualifications inline in pattern fields — clean pattern name + confidence only.
The ta_chart_notes field is the place for any synthesis."""


def call_claude_scoring(enriched: list, regime: dict, sector_flows: dict) -> dict:
    """Send all enriched ticker data to Claude Sonnet for scoring and ranking."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    tickers_payload = []
    for d in enriched:
        flow = sector_flows.get(d.get("ticker", ""), {})
        tickers_payload.append({
            "ticker":              d.get("ticker"),
            "name":                d.get("name"),
            "exchange":            d.get("exchange"),
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
            "ta_indicators":       d.get("ta_indicators", {}),
            "days_to_earnings":    d.get("days_to_earnings"),
            "next_earnings_date":  d.get("next_earnings_date"),
        })

    user_msg = json.dumps({
        "market_regime": regime,
        "vix": regime.get("vix"),
        "ticker_count": len(tickers_payload),
        "tickers": tickers_payload,
    }, indent=2)

    print(f"  Calling Claude Sonnet for scoring ({len(tickers_payload)} tickers)...", flush=True)
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=SCORING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def call_claude_patterns(top10: list, chart_images: dict) -> list:
    """
    Send chart images for top 10 tickers to Claude with extended thinking
    for pattern recognition. Returns list of pattern dicts.
    """
    if not chart_images:
        print("  [patterns] No chart images available — skipping pattern analysis.", flush=True)
        return [{"ticker": p["ticker"], "bullish_pattern": "none", "bearish_pattern": "none"} for p in top10]

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # Build content blocks: one image + label per ticker
    content = []
    tickers_with_images = []
    tickers_without_images = []

    for pick in top10:
        ticker = pick["ticker"]
        b64 = chart_images.get(ticker)
        if b64:
            ta = pick.get("ta_indicators", {})
            ta_summary = []
            if ta.get("rsi_14") is not None:
                ta_summary.append(f"RSI={ta['rsi_14']}")
            if ta.get("macd_bullish") is not None:
                ta_summary.append(f"MACD={'bullish' if ta['macd_bullish'] else 'bearish'}")
            if ta.get("macd_crossover"):
                ta_summary.append("MACD_crossover=true")
            if ta.get("bb_squeeze") is not None:
                ta_summary.append(f"BB_squeeze={'YES' if ta['bb_squeeze'] else 'no'}")
            if ta.get("bb_width_pct") is not None:
                ta_summary.append(f"BB_width={ta['bb_width_pct']}%")
            if ta.get("candle_patterns_bullish"):
                ta_summary.append(f"Candle_bullish={','.join(ta['candle_patterns_bullish'])}")
            if ta.get("candle_patterns_bearish"):
                ta_summary.append(f"Candle_bearish={','.join(ta['candle_patterns_bearish'])}")
            ta_text = f" | TA: {' | '.join(ta_summary)}" if ta_summary else " | TA: unavailable"

            content.append({
                "type": "text",
                "text": f"Chart for {ticker} (Rank #{pick['rank']}, {pick.get('exchange', '')}){ta_text}:"
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                }
            })
            tickers_with_images.append(ticker)
        else:
            tickers_without_images.append(ticker)

    content.append({
        "type": "text",
        "text": (
            f"Analyze the {len(tickers_with_images)} charts above for the following tickers in order: "
            f"{', '.join(tickers_with_images)}. "
            "For each, cross-reference the chart image with the TA indicator summary provided above it. "
            "Apply the strict pattern criteria from your instructions. "
            "Return the JSON output as specified."
        )
    })

    print(f"  Calling Claude Sonnet (extended thinking) for pattern analysis ({len(tickers_with_images)} charts)...", flush=True)

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=16000,
        thinking={
            "type": "enabled",
            "budget_tokens": 10000,
        },
        system=PATTERN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    # Extract text block (skip thinking blocks)
    raw = ""
    for block in response.content:
        if block.type == "text":
            raw = block.text.strip()
            break

    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        result = json.loads(raw)
        patterns = result.get("patterns", [])
    except Exception as e:
        print(f"  [patterns] Failed to parse response: {e}", flush=True)
        patterns = []

    # Fill in any tickers that had no chart image
    found_tickers = {p["ticker"] for p in patterns}
    for ticker in tickers_without_images:
        if ticker not in found_tickers:
            patterns.append({"ticker": ticker, "bullish_pattern": "none", "bearish_pattern": "none", "ta_chart_notes": ""})

    return patterns


# ── Main ──────────────────────────────────────────────────────────────────────

def run_shortlist():
    print("\n=== Shortlist Agent ===", flush=True)

    if not SCREENER_JSON.exists():
        print("  screener.json not found — run screener first.", flush=True)
        sys.exit(1)

    data = json.loads(SCREENER_JSON.read_text())
    stocks = data.get("stocks", [])
    print(f"  {len(stocks)} tickers from screener.", flush=True)

    # Fetch market regime (VIX + SPY trend)
    print("  Fetching market regime...", flush=True)
    regime = fetch_market_regime()
    print(f"  Regime: {regime.get('regime_label')} | VIX={regime.get('vix')} ({regime.get('vix_regime')}) | multiplier={regime.get('score_multiplier')}", flush=True)

    # Fetch all standard sector ETF flows upfront
    print("  Fetching sector ETF flows...", flush=True)
    sector_flows_by_etf = {}
    for sector, etf in SECTOR_ETF.items():
        flow = fetch_sector_flow(sector)
        sector_flows_by_etf[etf] = flow
        print(f"    {etf} ({sector}): {flow.get('flow_5d_pct')}%", flush=True)

    # Enrich all tickers with yfinance
    print(f"\n  Fetching live data for {len(stocks)} tickers...", flush=True)
    enriched = fetch_all_tickers(stocks)

    # Map sector flows to tickers via yfinance sector info
    print("\n  Mapping sector flows...", flush=True)
    sector_flows_by_ticker = {d.get("ticker", ""): {"flow_5d_pct": None, "etf": None} for d in enriched}
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

    for d in enriched:
        flow = sector_flows_by_ticker.get(d["ticker"], {})
        d["sector_flow_5d_pct"] = flow.get("flow_5d_pct")
        d["sector_etf"] = flow.get("etf")

    # ── Phase 1: Score and rank all tickers ───────────────────────────────────
    print("\n  Sending to Claude Sonnet for scoring...", flush=True)
    result = call_claude_scoring(enriched, regime, sector_flows_by_ticker)

    top10 = result.get("top10", [])

    # Carry exchange field into top10 entries (needed for chart-img symbol prefix)
    exchange_map = {d["ticker"]: d.get("exchange", "") for d in enriched}
    for pick in top10:
        if not pick.get("exchange"):
            pick["exchange"] = exchange_map.get(pick["ticker"], "")

    # ── Phase 2: Fetch charts and run pattern analysis on top 10 only ─────────
    if top10:
        print(f"\n  Fetching chart images for top {len(top10)} picks...", flush=True)
        chart_images = fetch_charts_for_top10(top10)

        print(f"\n  Running chart pattern analysis...", flush=True)
        patterns = call_claude_patterns(top10, chart_images)

        # Build lookup and merge into top10 entries
        pattern_map = {p["ticker"]: p for p in patterns}
        for pick in top10:
            pm = pattern_map.get(pick["ticker"], {})
            pick["chart_bullish"] = pm.get("bullish_pattern", "none")
            pick["chart_bearish"] = pm.get("bearish_pattern", "none")
            pick["ta_chart_notes"] = pm.get("ta_chart_notes", "")

        # Build the chart pattern review section (ordered by rank)
        chart_pattern_review = []
        for pick in sorted(top10, key=lambda x: x.get("rank", 99)):
            bullish = pick.get("chart_bullish", "none")
            bearish = pick.get("chart_bearish", "none")
            warn = " ⚠️" if bearish != "none" and any(
                kw in bearish for kw in ("Formed", "Triggered")
            ) else ""
            chart_pattern_review.append(
                f"#{pick['rank']} {pick['ticker']} — "
                f"Bullish: {bullish} | Bearish: {bearish}{warn}"
            )
        result["chart_pattern_review"] = chart_pattern_review
    else:
        result["chart_pattern_review"] = []

    # Add metadata
    result["screener_timestamp"] = data.get("timestamp")
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["market_regime"] = regime

    SHORTLIST_JSON.write_text(json.dumps(result, indent=2))
    print(f"\n  Saved: {SHORTLIST_JSON}", flush=True)

    # ── Picks log: append top10 for outcome tracking ──────────────────────────
    if top10:
        picks_log_path = SHORTLIST_JSON.parent / "picks_log.json"
        try:
            existing_log = json.loads(picks_log_path.read_text()) if picks_log_path.exists() else []
        except Exception:
            existing_log = []

        run_entry = {
            "run_id": result["generated_at"],
            "regime_label": regime.get("regime_label"),
            "picks": [
                {
                    "ticker":     p["ticker"],
                    "rank":       p["rank"],
                    "score":      p["score"],
                    "setup_type": p.get("setup_type"),
                    "price_at_pick": None,  # populated by outcome_tracker.py
                    "outcomes": {},          # {5d: ..., 10d: ..., 20d: ...}
                }
                for p in top10
            ],
        }

        # Fetch prices at pick time
        price_map = {d["ticker"]: (d.get("price_live") or d.get("price_screener")) for d in enriched}
        for p in run_entry["picks"]:
            p["price_at_pick"] = price_map.get(p["ticker"])

        existing_log.append(run_entry)
        picks_log_path.write_text(json.dumps(existing_log, indent=2))
        print(f"  Appended to picks log: {picks_log_path}", flush=True)

    if top10:
        print(f"\n  Top 3 picks:", flush=True)
        for pick in top10[:3]:
            bullish = pick.get("chart_bullish", "none")
            bearish = pick.get("chart_bearish", "none")
            pattern_str = f" | Chart: B:{bullish} / ⬇:{bearish}" if bullish != "none" or bearish != "none" else ""
            print(f"    #{pick['rank']} {pick['ticker']} — score {pick['score']} — {pick['setup_type']}{pattern_str}", flush=True)

    return result


if __name__ == "__main__":
    import traceback
    try:
        run_shortlist()
    except Exception:
        traceback.print_exc()
        # Write error to a file so it can be retrieved via /data/
        try:
            err_path = DATA_DIR / "shortlist_error.txt"
            err_path.write_text(traceback.format_exc())
        except Exception:
            pass
        sys.exit(1)
