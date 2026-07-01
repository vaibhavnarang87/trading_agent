"""
Post-Earnings-Announcement Drift (PEAD) backtest — on REAL data.

Thesis (the strategy, fixed in advance): when a company beats EPS estimates by
a meaningful margin, its stock tends to drift in the direction of the surprise
over the following weeks, rather than fully repricing on the announcement day.
This is one of the most-studied anomalies in finance (Ball & Brown 1968;
Bernard & Thomas 1989).

Rules determined by that thesis:
  - SIGNAL: EPS surprise = (actual - estimate) / |estimate|. Enter only on
    beats above SURPRISE_THRESHOLD.
  - ENTRY: buy at the OPEN of the first trading day AFTER the report
    (reports are after-close, so the drift window starts next day; entering at
    next-day open means we do NOT capture the announcement-night gap — that gap
    is not part of the drift and not realistically tradeable by us).
  - EXIT: sell at the CLOSE after HOLD_DAYS trading days (~1 month = 20).
  - No leverage, one position per event, equal dollar sizing.

This file is a BACKTEST, not a live trader. It touches no orders. It exists to
answer one question honestly: did the thesis actually pay on this data?
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

HERE = os.path.dirname(__file__)

SURPRISE_THRESHOLD = 0.025   # require at least a 2.5% EPS beat
HOLD_DAYS = 20               # ~1 trading month, the classic PEAD window
DOLLARS_PER_TRADE = 1000.0   # equal sizing per event


@dataclass
class Trade:
    symbol: str
    earnings_date: str
    surprise: float
    entry_date: str
    entry_px: float
    exit_date: str
    exit_px: float

    @property
    def ret(self) -> float:
        return self.exit_px / self.entry_px - 1.0

    @property
    def pnl(self) -> float:
        return DOLLARS_PER_TRADE * self.ret


def load():
    prices = json.load(open(os.path.join(HERE, "data", "prices.json")))
    earnings = json.load(open(os.path.join(HERE, "data", "earnings.json")))
    earnings.pop("_note", None)
    return prices, earnings


def trading_days(bars):
    return [b["date"] for b in bars]


def run_backtest():
    prices, earnings = load()
    trades: list[Trade] = []

    for symbol, events in earnings.items():
        bars = prices[symbol]
        dates = trading_days(bars)
        by_date = {b["date"]: b for b in bars}

        for ev in events:
            est, act = ev["est"], ev["act"]
            if est == 0:
                continue
            surprise = (act - est) / abs(est)
            if surprise < SURPRISE_THRESHOLD:
                continue  # only trade meaningful beats

            # first trading day strictly after the report date
            after = [d for d in dates if d > ev["date"]]
            if len(after) < 1:
                continue
            entry_date = after[0]
            # exit HOLD_DAYS trading days later
            try:
                entry_idx = dates.index(entry_date)
            except ValueError:
                continue
            exit_idx = entry_idx + HOLD_DAYS
            if exit_idx >= len(dates):
                continue  # not enough forward data; skip rather than fabricate
            exit_date = dates[exit_idx]

            trades.append(
                Trade(
                    symbol=symbol,
                    earnings_date=ev["date"],
                    surprise=surprise,
                    entry_date=entry_date,
                    entry_px=by_date[entry_date]["open"],
                    exit_date=exit_date,
                    exit_px=by_date[exit_date]["close"],
                )
            )
    return trades


def summarize(trades: list[Trade]):
    if not trades:
        print("No trades generated.")
        return
    rets = [t.ret for t in trades]
    n = len(rets)
    wins = [r for r in rets if r > 0]
    avg = sum(rets) / n
    total_pnl = sum(t.pnl for t in trades)
    invested = DOLLARS_PER_TRADE * n
    # std dev (population)
    var = sum((r - avg) ** 2 for r in rets) / n
    std = var ** 0.5

    print(f"\n{'='*68}")
    print("PEAD BACKTEST — REAL DATA (5 names, Oct 2024–Jun 2026)")
    print(f"Rules: beat >= {SURPRISE_THRESHOLD:.1%}, hold {HOLD_DAYS} trading days, "
          f"${DOLLARS_PER_TRADE:.0f}/trade")
    print(f"{'='*68}")
    print(f"{'Symbol':<7}{'Earnings':<12}{'Surprise':>9}{'Entry':>12}{'Exit':>12}{'Return':>9}")
    print("-" * 68)
    for t in sorted(trades, key=lambda x: x.earnings_date):
        print(f"{t.symbol:<7}{t.earnings_date:<12}{t.surprise:>8.1%}"
              f"{t.entry_px:>12.2f}{t.exit_px:>12.2f}{t.ret:>8.1%}")
    print("-" * 68)
    print(f"Trades:            {n}")
    print(f"Win rate:          {len(wins)/n:.1%}  ({len(wins)}/{n})")
    print(f"Avg return/trade:  {avg:+.2%}")
    print(f"Std dev/trade:     {std:.2%}")
    print(f"Best / worst:      {max(rets):+.1%} / {min(rets):+.1%}")
    print(f"Total P&L:         ${total_pnl:+,.2f} on ${invested:,.0f} deployed "
          f"({total_pnl/invested:+.1%})")
    # naive t-stat on mean return (is the edge distinguishable from zero?)
    if std > 0:
        t_stat = avg / (std / (n ** 0.5))
        print(f"t-stat (mean!=0):  {t_stat:.2f}   "
              f"({'not ' if abs(t_stat)<2 else ''}significant at ~95%)")
    print(f"{'='*68}\n")
    return {
        "n": n, "win_rate": len(wins)/n, "avg": avg, "std": std,
        "total_pnl": total_pnl, "invested": invested,
        "t_stat": (avg/(std/(n**0.5))) if std>0 else 0.0,
        "rets": rets,
    }


if __name__ == "__main__":
    trades = run_backtest()
    summarize(trades)
