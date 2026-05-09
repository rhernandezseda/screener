"""
Fetch price outcomes for picks_log.json entries.

Run manually or from a cron job. For each logged pick run that has picks
without outcomes, fetches closing prices at +5, +10, +20 trading days
and computes the return %.

Usage:
    python3 outcome_tracker.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

PICKS_LOG = Path(__file__).parent.parent / "output" / "data" / "picks_log.json"
TRADING_DAYS_TARGETS = [5, 10, 20]


def trading_days_after(start_iso: str, n: int) -> list[str]:
    """Return the nth trading day date after start_iso using SPY history as calendar."""
    spy = yf.Ticker("SPY")
    hist = spy.history(period="60d")
    dates = [d.strftime("%Y-%m-%d") for d in hist.index]
    start_date = start_iso[:10]
    try:
        idx = next(i for i, d in enumerate(dates) if d >= start_date)
    except StopIteration:
        return []
    targets = []
    for offset in TRADING_DAYS_TARGETS:
        target_idx = idx + offset
        if target_idx < len(dates):
            targets.append(dates[target_idx])
        else:
            targets.append(None)
    return targets


def fetch_close_on(ticker: str, date_str: str) -> float | None:
    if not date_str:
        return None
    try:
        hist = yf.Ticker(ticker).history(start=date_str, end=None, period="5d")
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[0]), 4)
    except Exception:
        return None


def update_outcomes():
    if not PICKS_LOG.exists():
        print("picks_log.json not found — nothing to update.")
        return

    log = json.loads(PICKS_LOG.read_text())
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    changed = 0

    spy_dates = None  # computed lazily once

    for run in log:
        run_date = run["run_id"][:10]
        incomplete = [p for p in run["picks"] if len(p.get("outcomes", {})) < len(TRADING_DAYS_TARGETS)]
        if not incomplete:
            continue

        if spy_dates is None:
            spy_dates = trading_days_after(run_date, max(TRADING_DAYS_TARGETS))

        target_dates = trading_days_after(run_date, max(TRADING_DAYS_TARGETS))

        for pick in incomplete:
            ticker = pick["ticker"]
            entry_price = pick.get("price_at_pick")
            if not entry_price:
                continue

            for i, days in enumerate(TRADING_DAYS_TARGETS):
                key = f"{days}d"
                if key in pick.get("outcomes", {}):
                    continue
                target_date = target_dates[i] if i < len(target_dates) else None
                if not target_date or target_date > today:
                    continue
                close = fetch_close_on(ticker, target_date)
                if close is None:
                    continue
                ret = round((close - entry_price) / entry_price * 100, 2)
                pick.setdefault("outcomes", {})[key] = {
                    "date": target_date,
                    "close": close,
                    "return_pct": ret,
                }
                print(f"  {ticker} +{days}d ({target_date}): {close:.2f} → {ret:+.2f}%")
                changed += 1

    if changed:
        PICKS_LOG.write_text(json.dumps(log, indent=2))
        print(f"\nUpdated {changed} outcome(s) in {PICKS_LOG}")
    else:
        print("No new outcomes to update.")


if __name__ == "__main__":
    update_outcomes()
