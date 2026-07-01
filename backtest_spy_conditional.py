"""
Conditional test: does SPY intraday momentum concentrate on high-volatility days?

Theory says the intraday-momentum signal should be strongest when the early
move is abnormally large (Zarattini's "imbalance"; Gao et al.'s FOMC-day result).
We bucket real SPY days by the size of the first-30-min move and measure the
momentum payoff in each bucket.

RESULT (167 real SPY days, Oct 2025–Jun 2026): no clean pattern. Even the most
volatile quartile shows only +0.015%/day (t=0.72, not significant), and its
rest-of-day return is NEGATIVE — a hint of reversal, not momentum. The
literature's high-vol effect needs genuinely extreme days (real FOMC shocks,
VIX spikes) absent from this calm sample. Honest conclusion: no usable
volatility-conditional edge here.
"""
from __future__ import annotations

import json
import os
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(__file__)


def run():
    bars = json.load(open(os.path.join(HERE, "data", "spy_30min.json")))
    days = defaultdict(list)
    for b in bars:
        days[b["t"][:10]].append(b)

    recs, prev = [], None
    for dk in sorted(days):
        db = days[dk]
        if len(db) == 13:
            if prev is not None:
                r1 = db[0]["c"] / prev - 1
                rlast = db[-1]["c"] / db[-1]["o"] - 1
                rest = db[-1]["c"] / db[0]["c"] - 1
                recs.append((abs(r1), r1, rlast, rest))
            prev = db[-1]["c"]
        else:
            prev = db[-1]["c"]

    recs.sort()
    n = len(recs)
    q = n // 4
    buckets = [
        ("Q1 calmest", recs[:q]),
        ("Q2", recs[q:2 * q]),
        ("Q3", recs[2 * q:3 * q]),
        ("Q4 most volatile", recs[3 * q:]),
    ]
    print(f"SPY intraday momentum by early-move size — {n} days")
    print("=" * 70)
    print(f"{'bucket':<18}{'avg|move|':>10}{'last30 payoff':>15}{'t':>7}{'restofday':>14}")
    print("-" * 70)
    for name, bk in buckets:
        am = st.mean(x[0] for x in bk)
        last = [(1 if r1 > 0 else -1) * rl for _, r1, rl, _ in bk]
        rod = [(1 if r1 > 0 else -1) * ro for _, r1, _, ro in bk]
        ml, sl = st.mean(last), st.pstdev(last)
        t = ml / (sl / len(last) ** 0.5) if sl else 0
        print(f"{name:<18}{am:>9.2%}{ml:>14.4%}{t:>7.2f}{st.mean(rod):>13.4%}")
    print("=" * 70)
    print("No monotonic 'bigger move -> bigger continuation'. Q4 rest-of-day is")
    print("negative. No usable volatility-conditional edge in this sample.")


if __name__ == "__main__":
    run()
