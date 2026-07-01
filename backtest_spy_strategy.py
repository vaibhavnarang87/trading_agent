"""
Backtest the deterministic SPY intraday strategy on REAL data.

Compares: base rule vs. base + 20-day trend filter. The sentiment gate is NOT
here — it can't be backtested (no historical Reddit data); it's a forward-only
live gate.

Honest expectation going in: the underlying signal showed no significant edge,
so we're testing whether a trend filter rescues it. Spoiler in the output.
"""
from __future__ import annotations

import json
import os
import statistics as st
from collections import defaultdict

from .strategy_spy_intraday import IntradayContext, decide

HERE = os.path.dirname(__file__)
MA_WINDOW = 20  # days


def build_days():
    bars = json.load(open(os.path.join(HERE, "data", "spy_30min.json")))
    days = defaultdict(list)
    for b in bars:
        days[b["t"][:10]].append(b)
    out = []
    for dk in sorted(days):
        db = days[dk]
        if len(db) == 13:
            out.append((dk, db))
    return out


def run():
    days = build_days()
    daily_close = [db[-1]["c"] for _, db in days]

    def evaluate(use_trend):
        rets = []
        prev_close = None
        for i, (dk, db) in enumerate(days):
            if prev_close is None or i < MA_WINDOW:
                prev_close = db[-1]["c"]
                continue
            first30 = db[0]["c"] / prev_close - 1
            last30 = db[-1]["c"] / db[-1]["o"] - 1
            ma = sum(daily_close[i - MA_WINDOW:i]) / MA_WINDOW
            in_uptrend = prev_close > ma
            ctx = IntradayContext(
                symbol="SPY", first30_return=first30,
                last_price=db[-1]["o"], in_uptrend=in_uptrend,
            )
            d = decide(ctx, use_trend_filter=use_trend)
            if d.go_long:
                rets.append(last30)   # captured last-30-min return
            prev_close = db[-1]["c"]
        return rets

    print("=" * 64)
    print("SPY intraday strategy — REAL data backtest (last-30-min returns)")
    print("=" * 64)
    for label, use_trend in [("Base rule", False), ("Base + 20d trend filter", True)]:
        rets = evaluate(use_trend)
        if not rets:
            print(f"{label}: no trades")
            continue
        n = len(rets)
        m = st.mean(rets)
        s = st.pstdev(rets)
        t = m / (s / n ** 0.5) if s else 0
        wr = sum(1 for r in rets if r > 0) / n
        tot = 1.0
        for r in rets:
            tot *= 1 + r
        print(f"\n{label}")
        print(f"  trades {n}   win {wr:.0%}   mean/trade {m:+.4%}   t={t:+.2f}")
        print(f"  compounded over period: {tot-1:+.2%}")
    print("\n" + "=" * 64)
    print("If neither column shows a significant t-stat (|t|>2), the filter did")
    print("not rescue the signal. No edge -> stays in PAPER. We do not promote a")
    print("failed backtest to live, no matter how much we'd like it to work.")
    print("=" * 64)


if __name__ == "__main__":
    run()
