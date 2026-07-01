"""
Backtest the Dip-Buy-Quality strategy on REAL data.

Universe: AAPL, AMZN, MSFT, GOOGL, NKE (real daily prices, Oct 2024–Jun 2026).
Fundamental gate: real earnings — fundamental_ok = the most recent earnings
report on/before the day was a beat. (Live, the LLM signal sets this flag; here
we use real earnings as the honest, backtestable proxy.)

Simulates a portfolio: max 4 concurrent positions, equal-weight slots, with the
+10%/-7%/thesis-break exits. Reports per-trade and portfolio stats, plus a
buy-and-hold benchmark for the same names/period.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from .strategy_dipbuy import Action, DipBuyParams, decide

HERE = os.path.dirname(__file__)
P = DipBuyParams()


def load():
    prices = json.load(open(os.path.join(HERE, "data", "prices.json")))
    earnings = json.load(open(os.path.join(HERE, "data", "earnings.json")))
    earnings.pop("_note", None)
    return prices, earnings


def fundamental_ok_on(symbol, date_str, earnings):
    """Most recent earnings on/before date: True if it beat, else False. Default True."""
    evs = [e for e in earnings.get(symbol, []) if e["date"] <= date_str and e.get("act") is not None]
    if not evs:
        return True  # no report yet -> no negative signal
    last = max(evs, key=lambda e: e["date"])
    return last["act"] > last["est"]


def run():
    prices, earnings = load()
    symbols = list(prices.keys())
    # align on a common date index
    all_dates = sorted({b["date"] for s in symbols for b in prices[s]})
    px = {s: {b["date"]: b["close"] for b in prices[s]} for s in symbols}
    hist = {s: [] for s in symbols}  # rolling closes

    positions = {}   # symbol -> entry_price
    trades = []      # (symbol, entry_date, entry, exit_date, exit, reason)
    entry_dates = {}

    for d in all_dates:
        # update rolling history
        for s in symbols:
            if d in px[s]:
                hist[s].append(px[s][d])

        # exits first (free up slots), then entries
        for phase in ("exit", "entry"):
            for s in symbols:
                if d not in px[s] or len(hist[s]) < P.high_window:
                    continue
                price = px[s][d]
                recent_high = max(hist[s][-P.high_window:])
                fok = fundamental_ok_on(s, d, earnings)
                held = s in positions
                if phase == "exit" and held:
                    dec = decide(price, recent_high, fok, True, positions[s], len(positions), P)
                    if dec.action == Action.SELL:
                        trades.append((s, entry_dates[s], positions[s], d, price, dec.reason))
                        del positions[s]; del entry_dates[s]
                elif phase == "entry" and not held:
                    dec = decide(price, recent_high, fok, False, None, len(positions), P)
                    if dec.action == Action.BUY:
                        positions[s] = price; entry_dates[s] = d

    # close any open positions at last price
    for s, entry in list(positions.items()):
        last_d = max(px[s])
        trades.append((s, entry_dates[s], entry, last_d, px[s][last_d], "end-of-test close"))

    # stats
    rets = [(ex / en - 1) for _, _, en, _, ex, _ in trades]
    n = len(rets)
    print("=" * 68)
    print("DIP-BUY-QUALITY BACKTEST — REAL DATA (5 names, Oct 2024–Jun 2026)")
    print(f"dip -{P.dip_pct:.0%}, target +{P.profit_target:.0%}, stop -{P.stop_loss:.0%}, "
          f"max {P.max_positions} positions")
    print("=" * 68)
    for s, ed, en, xd, ex, reason in trades:
        print(f"  {s:<6} {ed} @ {en:7.2f} -> {xd} @ {ex:7.2f}  {ex/en-1:+6.1%}  ({reason})")
    print("-" * 68)
    if n:
        import statistics as st
        wins = sum(1 for r in rets if r > 0)
        avg = st.mean(rets)
        sd = st.pstdev(rets)
        t = avg / (sd / n ** 0.5) if sd else 0
        print(f"Trades: {n}   Win rate: {wins/n:.0%}   Avg/trade: {avg:+.2%}   "
              f"t={t:+.2f}")
        # equal-weight compounded (sequential approximation)
        comp = 1.0
        for r in rets:
            comp *= 1 + r / P.max_positions  # each trade ~1/max_positions of book
        print(f"Rough compounded (¼-weighted slots): {comp-1:+.1%}")
    # benchmark: buy-and-hold each name over the window, avg
    bh = []
    for s in symbols:
        ds = sorted(px[s])
        bh.append(px[s][ds[-1]] / px[s][ds[0]] - 1)
    print(f"Buy-and-hold avg of the 5 names: {sum(bh)/len(bh):+.1%}")
    print("=" * 68)
    print("Live, the LLM sets fundamental_ok from news/guidance, not just the last")
    print("earnings beat — a richer filter than this backtest proxy.")


if __name__ == "__main__":
    run()
