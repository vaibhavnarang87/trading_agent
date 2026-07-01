"""
Daily entrypoint for the forward paper harness. Schedule once per trading day,
~15 min after the close (4:15 PM ET). Records one day into the ledger. No real
money, no brokerage access — read-only price data only.

Feeds:
  - Sentiment: ApeWisdom public API (no key).
  - SPY intraday: yfinance (no key, read-only). Your forward test needs prices,
    not your Robinhood account, so we deliberately keep credentials out of cron.

Setup:
    pip install yfinance
Schedule (cron, weekdays 4:15 PM ET):
    15 16 * * 1-5  cd /path/to/project && /usr/bin/python3 -m trading_agent.run_daily >> cron.log 2>&1
Check the record any time:
    python -m trading_agent.forward_paper report
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import date

from .forward_paper import record_day, LEDGER


def fetch_sentiment_rows() -> list[dict]:
    """Live WSB rows from ApeWisdom's public API. [] on any failure."""
    url = "https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode()).get("results", [])
    except Exception:
        return []


def _parse(daily_df, intraday_df):
    """
    Pure transform: (daily DataFrame, intraday DataFrame) -> (session_date_str,
    bars, prev_close, ma20). Factored out so it can be unit-tested without
    network. Returns None if there isn't enough data.

    Assumes run after today's close: daily_df's last row is today; the previous
    session's close is the second-to-last row; MA20 is the 20 sessions before
    today (consistent with the backtest's trailing MA).
    """
    if daily_df is None or len(daily_df) < 21 or intraday_df is None or intraday_df.empty:
        return None
    closes = [float(c) for c in daily_df["Close"].tolist()]
    prev_close = closes[-2]
    ma20 = sum(closes[-21:-1]) / 20.0

    session_date = intraday_df.index[-1].date()
    same_day = intraday_df[[ts.date() == session_date for ts in intraday_df.index]]
    bars = [{"o": float(o), "c": float(c)}
            for o, c in zip(same_day["Open"].tolist(), same_day["Close"].tolist())]
    return session_date.isoformat(), bars, prev_close, ma20


def fetch_spy_today():
    """Live SPY data via yfinance. Returns (session_date, bars, prev_close, ma20),
    or None on any failure (so a transient network error logs cleanly instead of
    crashing the scheduled job)."""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        daily = spy.history(period="45d", interval="1d")
        intraday = spy.history(period="1d", interval="30m")
        return _parse(daily, intraday)
    except Exception as e:
        print(f"  (SPY fetch failed: {type(e).__name__}: {e})")
        return None


def _already_recorded(session_date: str) -> bool:
    if not os.path.exists(LEDGER):
        return False
    for line in open(LEDGER):
        if line.strip() and json.loads(line).get("date") == session_date:
            return True
    return False


def main() -> None:
    res = fetch_spy_today()
    if res is None:
        print(f"[{date.today()}] SPY feed returned no usable data (market closed "
              f"for the week, or not enough history). Nothing recorded.")
        return
    session_date, bars, prev_close, ma20 = res

    if len(bars) != 13:
        print(f"[{session_date}] session incomplete ({len(bars)} bars) — likely "
              f"pre-close or a half day. Nothing recorded; will catch it after close.")
        return
    if _already_recorded(session_date):
        print(f"[{session_date}] already in the ledger. Skipping (no duplicate).")
        return

    rows = fetch_sentiment_rows()
    rec = record_day(session_date, bars, prev_close, ma20, rows,
                     use_trend_filter=False, use_sentiment_gate=True)
    print(f"[{session_date}] recorded -> {rec['decision']}  "
          f"(first30 {rec['first30_return']:+.3%}, euphoria={rec['sentiment_euphoria']}, "
          f"reason: {rec['reason']})")


if __name__ == "__main__":
    main()
