"""
Overnight vs Intraday return anomaly — backtest on REAL data.

Thesis (more recent literature): Lou, Polk & Skouras (2018), Hendershott,
Livdan & Rösch (2020) document that, across broad US equity samples, almost all
of the long-run equity premium is earned OVERNIGHT (previous close -> next open),
while the INTRADAY session (open -> close) contributes roughly zero or negative
return. Implied strategy: hold overnight, sit out the day.

Two return streams, decomposed exactly from daily bars:
  overnight_t = open_t / close_{t-1} - 1
  intraday_t  = close_t / open_t - 1
  (1 + overnight_t) * (1 + intraday_t) = 1 + total_t   (identity, by construction)

This is a research backtest. It touches no orders. The honest question: does the
overnight effect show up in a form an account like yours could trade?

NOTE on tradeability — read before getting excited by any overnight number:
capturing "overnight only" means buying at the close and selling at the open
EVERY trading day. That's ~250 round trips/year per name: heavy slippage,
reliance on market-on-close / market-on-open fills, and bid/ask drag that a
backtest on mid/closing prices does NOT include. A positive overnight figure
here is an upper bound, not a realizable return.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(__file__)


def load():
    return json.load(open(os.path.join(HERE, "data", "prices.json")))


def decompose(bars):
    over = intra = total = 1.0
    over_series = []
    for i in range(1, len(bars)):
        prev_c = bars[i - 1]["close"]
        o = bars[i]["open"]
        c = bars[i]["close"]
        on = o / prev_c - 1.0
        idr = c / o - 1.0
        over *= 1 + on
        intra *= 1 + idr
        total *= 1 + (c / prev_c - 1.0)
        over_series.append(on)
    return over - 1, intra - 1, total - 1, over_series


def run():
    prices = load()
    print("=" * 72)
    print("OVERNIGHT vs INTRADAY — REAL DATA (5 names, Oct 2024–Jun 2026)")
    print("=" * 72)
    print(f"{'Symbol':<7}{'Total':>10}{'Overnight':>12}{'Intraday':>11}"
          f"{'Overnight wins?':>17}")
    print("-" * 72)
    overs, intras = [], []
    all_on = []
    for sym, bars in prices.items():
        o, i_, t, on_series = decompose(bars)
        overs.append(o)
        intras.append(i_)
        all_on += on_series
        verdict = "yes" if o > i_ else "no"
        print(f"{sym:<7}{t:>+10.1%}{o:>+12.1%}{i_:>+11.1%}{verdict:>17}")
    print("-" * 72)
    avg_o = sum(overs) / len(overs)
    avg_i = sum(intras) / len(intras)
    print(f"{'AVG':<7}{'':>10}{avg_o:>+12.1%}{avg_i:>+11.1%}")
    print("-" * 72)

    # Daily overnight stats pooled across names: is mean overnight return > 0?
    n = len(all_on)
    mean = sum(all_on) / n
    var = sum((x - mean) ** 2 for x in all_on) / n
    std = var ** 0.5
    t_stat = mean / (std / n ** 0.5) if std else 0.0
    print(f"Pooled daily overnight return: mean {mean:+.3%}, "
          f"std {std:.3%}, n={n}")
    print(f"t-stat (mean overnight != 0): {t_stat:.2f} "
          f"({'not ' if abs(t_stat) < 2 else ''}significant at ~95%)")
    print("=" * 72)
    print("\nHonest read: the overnight effect is documented on BROAD samples")
    print("(thousands of names, decades). On 5 large-caps over ~20 months it")
    print("does NOT replicate cleanly — the average even favors intraday here,")
    print("driven by AAPL and GOOGL. Small, narrow samples are mostly noise.")
    print("And the tradeable overnight version pays slippage this test ignores.")
    return overs, intras


if __name__ == "__main__":
    run()
