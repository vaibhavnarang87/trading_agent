"""
SPY Intraday Momentum — backtest on REAL data.

Papers tested:
  - Gao, Han, Li & Zhou (2018), "Market Intraday Momentum" (J. Financial Econ.):
    the first-30-min return predicts the last-30-min return on SPY. Timing rule:
    at 15:30 go long if the first 30 min was up, short if down; hold to 16:00.
  - Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective Intraday
    Momentum Strategy for SPY" (SSRN 4824172): trade in the direction of early
    intraday imbalance, with volatility-scaled bands, trailing stops, and
    target-volatility position sizing (which permits LEVERAGE). Reported 1,985%
    total / 19.6% annualized / Sharpe 1.33 over 2007–early 2024.

Data: real Robinhood SPY 30-minute bars, regular session, ~170 trading days
(Oct 2025 – Jun 2026). This is the cleanest, most academically-grounded test of
everything we've tried — SPY is liquid (penny spreads), so costs are NOT the
obstacle here.

------------------------------------------------------------------------------
RESULTS on this sample
  Gao core (first 30 -> last 30):
    correlation +0.078, R^2 0.006  (the paper's full-history R^2 is larger;
    here the link is faint and noisy)
    sign(first)*last-30:  +0.0098%/day, t=1.05  — NOT significant
    long-only last-30:    +0.0106%/day, t=1.46  — NOT significant
    always-long last-30:  +0.0115%/day          — i.e. the "signal" adds
                                                   nothing over just being long
  Zarattini-style (trade first-30 direction, hold 10:00->close):
    long/short: -0.0055%/day, t=-0.14  — essentially zero
    long-only:  +0.0032%/day, t=+0.13  — essentially zero
------------------------------------------------------------------------------

Why the paper shows 19.6%/yr and this sample shows ~nothing — all legitimate,
none of it cheating, but none of it available to a $5k cash account:

  1. HORIZON. The paper spans 2007–2024, including 2008 and 2020. The intraday
     momentum effect is known to concentrate on high-volatility / FOMC days
     (Gao et al. report R^2 ~11% on FOMC days vs near-zero otherwise). Our
     window was a calm stretch, so the effect washed out. This isn't a bug —
     it's the effect being conditional on volatility we didn't have.
  2. LEVERAGE. The headline uses target-volatility sizing that can exceed 1x.
     Strip leverage and the unleveraged return is far lower.
  3. SHORT SELLING. Half the strategy is intraday shorts. A cash account can't
     short. The long-only slice (the part you could run) is the weakest.
  4. PDT RULE. It's a day-trade strategy. On $5k (<$25k), FINRA caps you at 3
     day trades / 5 business days. Even this once-per-day version breaches that.

CONCLUSION. This is the most credible strategy we've tested, and on real recent
data it still produced no usable, significant edge for an account like yours.
The paper's returns are real but come from horizon, leverage, and shorting that
a $5k cash account can't reproduce, plus a day-trade frequency the PDT rule
forbids. Honest answer: not runnable as a money-maker here.

A genuinely interesting follow-up (not a money promise): the effect is
conditional on volatility. Restricting to FOMC / high-VIX days is where the
literature finds the signal. That's a research question worth a clean test —
but still bounded by PDT and no leverage.
"""
from __future__ import annotations

import json
import os
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(__file__)


def load_days():
    bars = json.load(open(os.path.join(HERE, "data", "spy_30min.json")))
    days = defaultdict(list)
    for b in bars:
        days[b["t"][:10]].append(b)
    return days


def build_records():
    days = load_days()
    recs = []
    prev_close = None
    for dk in sorted(days):
        db = days[dk]
        if len(db) == 13:  # full regular session
            if prev_close is not None:
                r1 = db[0]["c"] / prev_close - 1
                rlast = db[-1]["c"] / db[-1]["o"] - 1
                rest = db[-1]["c"] / db[0]["c"] - 1
                recs.append((dk, r1, rlast, rest))
            prev_close = db[-1]["c"]
        else:
            prev_close = db[-1]["c"]  # half day: update close, skip signal
    return recs


def stats(x, label):
    m = st.mean(x)
    s = st.pstdev(x)
    t = m / (s / len(x) ** 0.5) if s else 0.0
    wr = sum(1 for v in x if v > 0) / len(x)
    print(f"  {label:<26} mean/day {m:+.4%}  t={t:+.2f}  "
          f"~ann {m*252:+.1%}  win {wr:.0%}")


def run():
    recs = build_records()
    r1 = [x[1] for x in recs]
    rlast = [x[2] for x in recs]
    rest = [x[3] for x in recs]
    n = len(recs)

    mx, my = st.mean(r1), st.mean(rlast)
    cov = sum((a - mx) * (b - my) for a, b in zip(r1, rlast)) / n
    corr = cov / (st.pstdev(r1) * st.pstdev(rlast))

    print("=" * 70)
    print(f"SPY INTRADAY MOMENTUM — REAL DATA, {n} trading days")
    print("=" * 70)
    print(f"Gao core: corr(first30, last30) = {corr:+.3f}  R^2 = {corr**2:.3f}")
    print("\nLast-30-min trade:")
    stats([(1 if a > 0 else -1) * b for a, b in zip(r1, rlast)], "sign(first) long/short")
    stats([b if a > 0 else 0.0 for a, b in zip(r1, rlast)], "long-only (no short)")
    stats(rlast, "always long last-30")
    print("\nFirst-30 direction held to close (Zarattini-style):")
    stats([(1 if a > 0 else -1) * b for a, b in zip(r1, rest)], "first-dir long/short")
    stats([b if a > 0 else 0.0 for a, b in zip(r1, rest)], "long-only (implementable)")
    print("=" * 70)
    print("No statistically significant edge in this sample. See module docstring")
    print("for why the paper's 19.6%/yr (horizon+leverage+shorts) isn't reachable")
    print("on a $5k cash account under the PDT rule.")
    print("=" * 70)


if __name__ == "__main__":
    run()
