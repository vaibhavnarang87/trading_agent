"""
Intraday pattern tournament — 15-minute bars, realistic costs.

Tests the classic intraday patterns the literature names, on real 15m data,
with costs included (the sources are explicit that untested/uncosted intraday
strategies "collapse once slippage is added"):

  ORB          : break above the first 30-min high -> long, exit EOD or stop
  VWAP-revert  : price >= VWAP_DEV below intraday VWAP -> long, exit at VWAP
  RSI2-bounce  : RSI(2) < 10 on 15m -> long, exit when RSI(2) > 50
  BB-bounce    : close below lower Bollinger(20,2) -> long, exit at mid band
  EMA-cross    : 9EMA crosses above 21EMA -> long, exit on cross down

Every pattern gets: a hard stop, an end-of-day exit (no overnight risk), and
COST_BPS round-trip costs. Benchmark = buy & hold the same names over the same
window. A pattern must clear buy-and-hold NET of costs to be worth deploying.

    python -m trading_agent.backtest_patterns
"""
from __future__ import annotations

import statistics as st
from collections import defaultdict

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AMD", "INTC", "TSLA",
    "JPM", "BAC", "XOM", "CVX", "KO", "PEP", "WMT", "HD", "DIS", "CSCO",
    "TXN", "QCOM", "CAT", "GE", "JNJ", "MRK", "PFE", "TMO", "COST", "SPY",
]
DAYS = "59d"          # yfinance 15m cap is 60 days
INTERVAL = "15m"
COST_BPS = 10 / 10000  # 10 bps round trip (spread + slippage), liquid names
STOP = -0.01           # 1% hard stop, intraday
VWAP_DEV = 0.010       # 1% below VWAP to trigger reversion
BB_PERIOD, BB_STD = 20, 2.0


def rsi(vals: list[float], period: int = 2) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    ag = al = None
    for i in range(1, len(vals)):
        ch = vals[i] - vals[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i == period:
            gs = [max(vals[k] - vals[k - 1], 0.0) for k in range(1, period + 1)]
            ls = [max(vals[k - 1] - vals[k], 0.0) for k in range(1, period + 1)]
            ag, al = sum(gs) / period, sum(ls) / period
        elif i > period:
            ag = (ag * (period - 1) + g) / period
            al = (al * (period - 1) + l) / period
        if ag is not None:
            out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ema(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    k = 2 / (n + 1)
    e = None
    for i, v in enumerate(vals):
        e = v if e is None else v * k + e * (1 - k)
        if i >= n - 1:
            out[i] = e
    return out


def _trade(entry: float, bars: list[dict], i: int, day_end: int,
           exit_test) -> float:
    """Walk forward from bar i+1 to day_end; return net % after costs."""
    for j in range(i + 1, day_end + 1):
        c = bars[j]["c"]
        r = c / entry - 1
        if r <= STOP:
            return r - COST_BPS
        if exit_test(j, r):
            return r - COST_BPS
    return bars[day_end]["c"] / entry - 1 - COST_BPS


def run() -> None:
    import numpy as np
    import pandas as pd
    import yfinance as yf

    print(f"Downloading {len(UNIVERSE)} symbols, {INTERVAL} bars, {DAYS}...")
    data = yf.download(UNIVERSE, period=DAYS, interval=INTERVAL,
                       group_by="ticker", progress=False, threads=True)

    results: dict[str, list[float]] = defaultdict(list)
    bh: list[float] = []
    total_bars = 0

    for sym in UNIVERSE:
        try:
            df = data[sym][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            continue
        if len(df) < 200:
            continue
        bars = [{"t": t, "o": float(o), "h": float(h), "l": float(l),
                 "c": float(c), "v": float(v)}
                for t, o, h, l, c, v in zip(df.index, df["Open"], df["High"],
                                            df["Low"], df["Close"], df["Volume"])]
        total_bars += len(bars)
        closes = [b["c"] for b in bars]
        r2 = rsi(closes, 2)
        e9, e21 = ema(closes, 9), ema(closes, 21)

        # group bar indices by session date
        sessions: dict = defaultdict(list)
        for i, b in enumerate(bars):
            sessions[b["t"].date()].append(i)

        for day, idxs in sessions.items():
            if len(idxs) < 8:
                continue
            first, last = idxs[0], idxs[-1]
            or_high = max(bars[i]["h"] for i in idxs[:2])   # first 30 min
            # intraday VWAP + rolling bands
            cum_pv = cum_v = 0.0
            vwap = {}
            for i in idxs:
                cum_pv += bars[i]["c"] * bars[i]["v"]
                cum_v += bars[i]["v"]
                vwap[i] = cum_pv / cum_v if cum_v else bars[i]["c"]

            fired = set()
            for pos, i in enumerate(idxs):
                if i <= first + 1 or i >= last:
                    continue
                c = bars[i]["c"]

                # --- ORB: first break of opening-range high
                if "ORB" not in fired and c > or_high:
                    fired.add("ORB")
                    results["ORB"].append(
                        _trade(c, bars, i, last, lambda j, r: r >= 0.02))

                # --- VWAP reversion
                if "VWAP" not in fired and vwap[i] and c <= vwap[i] * (1 - VWAP_DEV):
                    fired.add("VWAP")
                    results["VWAP-revert"].append(
                        _trade(c, bars, i, last,
                               lambda j, r, tgt=vwap[i]: bars[j]["c"] >= tgt))

                # --- RSI(2) bounce
                if "RSI" not in fired and r2[i] is not None and r2[i] < 10:
                    fired.add("RSI")
                    results["RSI2-bounce"].append(
                        _trade(c, bars, i, last,
                               lambda j, r: r2[j] is not None and r2[j] > 50))

                # --- Bollinger lower-band bounce
                if "BB" not in fired and i >= BB_PERIOD:
                    w = closes[i - BB_PERIOD:i]
                    mid = sum(w) / BB_PERIOD
                    sd = st.pstdev(w)
                    if sd and c < mid - BB_STD * sd:
                        fired.add("BB")
                        results["BB-bounce"].append(
                            _trade(c, bars, i, last,
                                   lambda j, r, m=mid: bars[j]["c"] >= m))

                # --- EMA 9/21 cross up
                if (e9[i] and e21[i] and e9[i - 1] and e21[i - 1]
                        and e9[i - 1] <= e21[i - 1] and e9[i] > e21[i]
                        and "EMA" not in fired):
                    fired.add("EMA")
                    results["EMA-cross"].append(
                        _trade(c, bars, i, last,
                               lambda j, r: e9[j] and e21[j] and e9[j] < e21[j]))

        bh.append(closes[-1] / closes[0] - 1)

    print("=" * 76)
    print(f"INTRADAY PATTERN TOURNAMENT — {INTERVAL} bars, {DAYS}, "
          f"{COST_BPS*10000:.0f}bps round-trip costs")
    print("=" * 76)
    print(f"{'pattern':<16}{'trades':>8}{'win%':>7}{'avg/trade':>11}"
          f"{'total':>9}{'t-stat':>8}")
    print("-" * 76)
    ranked = sorted(results.items(), key=lambda kv: -st.mean(kv[1]) if kv[1] else 0)
    for name, rets in ranked:
        n = len(rets)
        if not n:
            continue
        wins = sum(1 for r in rets if r > 0) / n
        avg = st.mean(rets)
        sd = st.pstdev(rets)
        t = avg / (sd / n ** 0.5) if sd else 0
        # simple compounding at 1 position at a time
        comp = 1.0
        for r in rets:
            comp *= (1 + r)
        print(f"{name:<16}{n:>8}{wins:>7.0%}{avg:>+11.3%}{comp-1:>+9.1%}{t:>8.2f}")
    print("-" * 76)
    print(f"Buy & hold same names, same window: {sum(bh)/len(bh):>+8.1%}")
    print("=" * 76)
    print("Every trade above is INTRADAY (entry and exit the same session) —")
    print("i.e. a DAY TRADE. avg/trade must clearly exceed 0 after costs, and")
    print("total must beat buy-and-hold, for the pattern to be worth deploying.")


if __name__ == "__main__":
    run()
