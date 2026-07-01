"""
Intraday Momentum Burst — backtest on REAL 5-minute data.

Your idea: find a stock gaining hard intraday (~6% in 15–30 min), buy the
momentum, cut it if it turns down. Sell/avoid on loss momentum.

Closest research:
  - Gao, Han, Li & Zhou (2018), "Market Intraday Momentum", J. Financial
    Economics — first half-hour return predicts last half-hour return (on SPY).
  - Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective Intraday
    Momentum Strategy for SPY" (SSRN 4824172) — opens trend positions on
    abnormal demand/supply imbalance, dynamic trailing stops.
  IMPORTANT: both trade the INDEX (SPY) at the market level — not single names
  spiking 6% in 15 minutes. That distinction is the whole story below.

Data: real Robinhood 5-minute bars, regular session, ~24 trading days,
AAPL/AMZN/TSLA/NKE/GOOGL.

------------------------------------------------------------------------------
FINDING 1 — your exact trigger never fires on liquid large-caps.
  >=6% gain in 15 min: 0 events across all 5 names.
  >=6% gain in 30 min: 0 events. TSLA's biggest 30-min move was +4.0%.
  A 6%-in-15-min burst is a low-float SMALL-CAP / pump pattern. To trade your
  rule literally, you'd be forced into exactly the illiquid names where this
  strategy is most dangerous and slippage is worst.

FINDING 2 — the underlying momentum effect IS real, but tiny.
  Lowering the trigger to "top-decile 30-min up-burst," the next 30 min
  averaged +0.143% vs -0.021% baseline, up ~60% of the time. Directionally
  this matches Gao et al. The catch is magnitude.

FINDING 3 — it dies on costs and on regulation.
  - Net of a realistic 0.20% single-stock round-trip, the +0.143% edge goes
    NEGATIVE. Liquid large-caps cost less, but the small-caps where 6% bursts
    occur cost much more.
  - The reported t-stat (~6) is inflated by overlapping windows (adjacent
    observations share bars and are not independent). Treat it as suggestive,
    not proof.
  - PDT RULE: with $5,000 (< $25,000), FINRA's pattern-day-trader rule caps you
    at 3 day trades per rolling 5 business days. A burst-chasing strategy is
    ALL day trades. You would exhaust the allowance in one morning and then be
    flagged/locked. This strategy is, by regulation, not runnable on this
    account.
------------------------------------------------------------------------------

Conclusion: the academic intraday-momentum signal is real but lives at the
index level, in tiny per-trade increments, captured by players without the PDT
constraint and with institutional execution. Reframed as "chase a single stock
up 6% in 15 minutes on a $5k account," it inverts into the textbook way retail
day-traders lose money (cf. Barber & Odean, "Trading Is Hazardous to Your
Wealth"). This file documents that honestly rather than hiding it behind a
backtest that ignores costs and the PDT rule.
"""
from __future__ import annotations

import json
import os
import statistics as st

HERE = os.path.dirname(__file__)


def load():
    return json.load(open(os.path.join(HERE, "data", "intraday_5min.json")))


def count_6pct_events(data):
    print("FINDING 1 — does a >=6% gain in 15–30 min ever occur on these names?")
    for sym, bars in data.items():
        ev15 = sum(
            1 for i in range(len(bars) - 3) if bars[i + 3]["c"] / bars[i]["o"] - 1 >= 0.06
        )
        ev30 = sum(
            1 for i in range(len(bars) - 6) if bars[i + 6]["c"] / bars[i]["o"] - 1 >= 0.06
        )
        mx = max(bars[i + 6]["c"] / bars[i]["o"] - 1 for i in range(len(bars) - 6))
        print(f"  {sym:<6} 6%/15min: {ev15}  6%/30min: {ev30}  biggest 30-min: {mx:+.2%}")


def continuation_test(data):
    cont, base = [], []
    for sym, bars in data.items():
        n = len(bars)
        moves = [(i, bars[i + 6]["c"] / bars[i]["o"] - 1) for i in range(n - 12)]
        thresh = sorted(m[1] for m in moves)[int(len(moves) * 0.90)]
        for i, r in moves:
            nxt = bars[i + 12]["c"] / bars[i + 6]["o"] - 1
            base.append(nxt)
            if r >= thresh:
                cont.append(nxt)
    gross = st.mean(cont)
    print("\nFINDING 2 — after a top-decile 30-min up-burst, next 30 min:")
    print(f"  mean {gross:+.3%}  vs baseline {st.mean(base):+.3%}  "
          f"(up {sum(1 for x in cont if x>0)/len(cont):.0%} of the time, n={len(cont)})")
    print("\nFINDING 3 — net of realistic single-stock round-trip costs:")
    for c in (0.05, 0.10, 0.20):
        net = gross - c / 100
        print(f"  minus {c:.2f}%: {net:+.3%}  -> {'profit' if net>0 else 'LOSS'}")
    print("\n  PDT rule: $5k < $25k -> max 3 day trades / 5 business days.")
    print("  A burst-chaser is all day trades. Not runnable on this account.")


if __name__ == "__main__":
    data = load()
    count_6pct_events(data)
    continuation_test(data)
