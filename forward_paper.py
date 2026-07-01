"""
Forward paper-testing harness for the SPY intraday strategy.

Purpose: build a REAL, live track record without risking money. Run once per
trading day after the close. Each run:
  1. pulls today's SPY 30-min bars (first-30-min and last-30-min returns),
  2. pulls current WSB sentiment (for the contrarian euphoria gate),
  3. evaluates the deterministic strategy decision,
  4. appends one immutable record to a JSONL ledger.

Over weeks this accumulates an honest forward record — the only legitimate way
to evaluate the sentiment overlay, which can't be backtested.

NOTHING here touches real money. There is no live broker path in this harness
by design. Promotion to live is a separate, deliberate decision that should
require the forward record to show a real, significant edge first.

Runtime wiring (you inject these so the harness has no hidden dependencies):
  - fetch_intraday_fn(): returns today's SPY 30-min bars via the Robinhood MCP
    get_equity_historicals tool -> list of {'o','c'} dicts (regular session).
  - fetch_sentiment_fn(): returns WSB aggregator rows (ApeWisdom-style) via a
    web fetch -> list of dicts for market_euphoria_extreme().

Usage:
  python -m trading_agent.forward_paper demo     # backfill from real history
  python -m trading_agent.forward_paper report    # show the running record
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

from .strategy_spy_intraday import IntradayContext, decide
from .research.social_sentiment import market_euphoria_extreme

HERE = os.path.dirname(__file__)
LEDGER = os.path.join(HERE, "data", "forward_paper_ledger.jsonl")
NOTIONAL = 1000.0   # paper dollars per signal
MA_WINDOW = 20


def _append(rec: dict, ledger_path: str = LEDGER) -> None:
    with open(ledger_path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def record_day(
    day_str: str,
    bars: list[dict],          # today's SPY 30-min bars (13 for a full day)
    prev_close: float,
    ma20: float | None,
    sentiment_rows: list[dict],
    use_trend_filter: bool = False,
    use_sentiment_gate: bool = True,
    ledger_path: str = LEDGER,
) -> dict:
    """Evaluate one day and append a ledger record. Returns the record."""
    if len(bars) != 13:
        rec = {"date": day_str, "skipped": "not a full session"}
        _append(rec, ledger_path)
        return rec

    first30 = bars[0]["c"] / prev_close - 1
    last30 = bars[-1]["c"] / bars[-1]["o"] - 1   # realized, recorded either way
    in_uptrend = (prev_close > ma20) if ma20 is not None else None
    euphoria = market_euphoria_extreme(sentiment_rows) if sentiment_rows else False

    ctx = IntradayContext(
        symbol="SPY", first30_return=first30, last_price=bars[-1]["o"],
        in_uptrend=in_uptrend, retail_euphoria_extreme=euphoria,
    )
    d = decide(ctx, use_trend_filter=use_trend_filter, use_sentiment_gate=use_sentiment_gate)

    traded = d.go_long
    pnl_pct = last30 if traded else 0.0
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "date": day_str,
        "first30_return": round(first30, 6),
        "last30_return": round(last30, 6),
        "in_uptrend": in_uptrend,
        "sentiment_euphoria": euphoria,
        "decision": "long" if traded else "flat",
        "reason": d.reason,
        "traded": traded,
        "notional": NOTIONAL if traded else 0.0,
        "pnl_pct": round(pnl_pct, 6),
        "pnl_dollars": round(NOTIONAL * pnl_pct, 4) if traded else 0.0,
        "mode": "paper",
    }
    _append(rec, ledger_path)
    return rec


def report(ledger_path: str = LEDGER) -> None:
    if not os.path.exists(ledger_path):
        print("No ledger yet. Run the harness daily to build a record.")
        return
    recs = [json.loads(l) for l in open(ledger_path) if l.strip()]
    recs = [r for r in recs if "decision" in r]
    traded = [r for r in recs if r["traded"]]
    print("=" * 60)
    print("FORWARD PAPER RECORD — SPY intraday strategy (no real money)")
    print("=" * 60)
    print(f"Days evaluated:   {len(recs)}")
    print(f"Days traded:      {len(traded)}  (flat on {len(recs)-len(traded)})")
    if traded:
        pnls = [r["pnl_pct"] for r in traded]
        n = len(pnls)
        mean = sum(pnls) / n
        wins = sum(1 for p in pnls if p > 0)
        total_dollars = sum(r["pnl_dollars"] for r in traded)
        import statistics as st
        sd = st.pstdev(pnls) if n > 1 else 0.0
        t = mean / (sd / n ** 0.5) if sd else 0.0
        print(f"Win rate:         {wins/n:.0%}")
        print(f"Mean/trade:       {mean:+.4%}")
        print(f"Cumulative paper: ${total_dollars:+,.2f} on ${NOTIONAL:.0f}/trade")
        print(f"t-stat:           {t:+.2f}  "
              f"({'NOT ' if abs(t)<2 else ''}significant — need |t|>2 and a")
        print("                  meaningful sample before considering live)")
    print("=" * 60)
    print("Promotion rule: live only if this forward record shows a real,")
    print("significant edge over a meaningful sample. Until then: paper.")
    print("=" * 60)


def _demo():
    """
    Backfill the ledger from REAL historical SPY data so you can see the harness
    and ledger work end-to-end. Sentiment is unavailable historically, so the
    gate is off in backfill (documented). Live runs will include it.
    """
    from collections import defaultdict
    if os.path.exists(LEDGER):
        os.remove(LEDGER)
    bars = json.load(open(os.path.join(HERE, "data", "spy_30min.json")))
    days = defaultdict(list)
    for b in bars:
        days[b["t"][:10]].append(b)
    keys = sorted(days)
    daily_close = [days[k][-1]["c"] for k in keys if len(days[k]) == 13]
    full = [k for k in keys if len(days[k]) == 13]
    # backfill the last 15 full days
    for k in full[-15:]:
        i = full.index(k)
        if i < MA_WINDOW:
            continue
        prev_close = days[full[i - 1]][-1]["c"]
        ma20 = sum(daily_close[i - MA_WINDOW:i]) / MA_WINDOW
        record_day(k, days[k], prev_close, ma20, sentiment_rows=[],
                   use_sentiment_gate=False)
    print(f"Backfilled ledger from real data: {LEDGER}\n")
    report()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "demo":
        _demo()
    else:
        report()
