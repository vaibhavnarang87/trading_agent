"""
Monthly momentum bot — the tournament + crash-stress winner, deployed.

Rule (deterministic, monthly): hold the TOP_N stocks by trailing 12-1 month
return (12-month return skipping the most recent month), equal weight. Each
run rebalances: SELL held names that fell out of the top-N, BUY new top-N
names. That's the whole strategy — momentum's exit IS "dropped out of the
ranking", not a stop-loss.

Backtested: +12.3% CAGR over 18y (vs SPY +10.4%), beat SPY through both the
2008 and 2022 crashes with shallower drawdowns.

Safety, same as the other engines:
  - MOMENTUM_LIVE=1 arms real execution (the user's act); else dry-run only.
  - Berkshire-clone symbols and the DCA symbol are EXCLUDED (managed by their
    own strategies — momentum never touches them).
  - Shared executor + governor + daily BUY cap; sells are uncapped; PDT-safe
    (never sells a position bought the same day).

    python -m trading_agent.momentum_bot            # dry-run: show the rebalance
    python -m trading_agent.momentum_bot --rebalance  # execute (needs MOMENTUM_LIVE=1)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

from .env_file import load_env_file

load_env_file()

MOMENTUM_LIVE = os.environ.get("MOMENTUM_LIVE") == "1"
# Mom5-Pullback (hybrid tournament winner, 8y +39.5% CAGR / Sharpe 1.16;
# 18y +15.1% CAGR vs +12.2% for the old Mom10, and beat SPY through 2008/2022).
# Concentration = 5 names. Pullback timing = only ENTER a top-ranked name on a
# short-term dip (RSI(2)<10 or a -2% day), instead of buying at any price.
TOP_N = int(os.environ.get("MOMENTUM_TOP_N", "5"))
PULLBACK = os.environ.get("MOMENTUM_PULLBACK", "1") == "1"
PULLBACK_RSI = float(os.environ.get("MOMENTUM_PULLBACK_RSI", "10"))
PULLBACK_DROP = float(os.environ.get("MOMENTUM_PULLBACK_DROP", "2")) / 100
# Hysteresis: a held name is only SOLD once it falls past this rank, not the
# instant it leaves the top-N. Without this, running intraday makes a stock
# hovering at rank 10/11 churn (sell/rebuy) — which on a cash account causes
# good-faith violations. Buy at rank<=TOP_N, sell at rank>SELL_RANK.
SELL_RANK = int(os.environ.get("MOMENTUM_SELL_RANK", str(TOP_N + 5)))
ACCOUNT = os.environ.get("TRADING_ACCOUNT_NUMBER", "PAPER-ACCOUNT")
DCA_SYMBOL = os.environ.get("DCA_SYMBOL", "VOO")
HERE = os.path.dirname(__file__)
PRIVATE = os.path.join(HERE, "data", "private")
EXECUTIONS = os.path.join(PRIVATE, "executions.jsonl")

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "JNJ", "PG", "KO",
    "PEP", "WMT", "HD", "CVX", "XOM", "BAC", "AXP", "MCD", "NKE", "DIS",
    "CSCO", "INTC", "VZ", "T", "CMCSA", "ADBE", "CRM", "ACN", "MRK", "PFE",
    "ABBV", "TMO", "COST", "AMD", "QCOM", "TXN", "CAT", "GE", "BA", "LLY",
]


def _rsi2(closes: list[float], period: int = 2) -> float | None:
    """Wilder RSI(2) on the latest bar — the pullback trigger."""
    if len(closes) < period + 2:
        return None
    ag = al = None
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i == period:
            gs = [max(closes[k] - closes[k - 1], 0.0) for k in range(1, period + 1)]
            ls = [max(closes[k - 1] - closes[k], 0.0) for k in range(1, period + 1)]
            ag, al = sum(gs) / period, sum(ls) / period
        elif i > period:
            ag = (ag * (period - 1) + g) / period
            al = (al * (period - 1) + l) / period
    if ag is None:
        return None
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def pullback_ready(closes: list[float]) -> tuple[bool, str]:
    """Entry timing: is this name currently dipping? (RSI(2) oversold or a
    sharp down day). Applied ONLY to names momentum already selected — the
    tournament showed this timing adds ~7 pts of CAGR there, while the same
    patterns applied to all stocks lose money."""
    if not PULLBACK:
        return True, "no timing filter"
    r = _rsi2(closes)
    day = closes[-1] / closes[-2] - 1 if len(closes) >= 2 else 0.0
    if r is not None and r < PULLBACK_RSI:
        return True, f"RSI(2) {r:.0f} < {PULLBACK_RSI:.0f}"
    if day <= -PULLBACK_DROP:
        return True, f"down {day:.1%} today"
    return False, (f"no pullback (RSI(2) {r:.0f}, today {day:+.1%})"
                   if r is not None else "no pullback")


def rank_momentum() -> tuple[list[tuple[str, float]], dict[str, list[float]]]:
    """FULL ranking by trailing 12-1 month return (best first), plus the close
    series per symbol so callers can evaluate pullback timing."""
    import yfinance as yf
    df = yf.download(UNIVERSE, period="2y", interval="1d",
                     group_by="ticker", progress=False, threads=True)
    scored, closes = [], {}
    for s in UNIVERSE:
        try:
            c = [float(x) for x in df[s]["Close"].dropna().tolist()]
            if len(c) < 260:
                continue
            # 12-1 momentum: price 21 trading days ago vs ~252 days ago
            scored.append((s, c[-21] / c[-252] - 1))
            closes[s] = c
        except Exception:
            continue
    scored.sort(key=lambda kv: -kv[1])
    return scored, closes


STATE = os.path.join(PRIVATE, "momentum_state.json")


def momentum_symbols() -> set[str]:
    """Symbols under momentum management. The dip-scanner's exit engine must
    SKIP these — momentum's exit is 'fell past rank N', not a -5% stop. Two
    engines with different exit rules on one position causes sell/rebuy churn."""
    try:
        return set(json.load(open(STATE)).get("symbols", []))
    except Exception:
        return set()


def _save_state(symbols: list[str]) -> None:
    os.makedirs(PRIVATE, exist_ok=True)
    json.dump({"symbols": sorted(symbols),
               "updated": datetime.now(timezone.utc).isoformat()},
              open(STATE, "w"), indent=2)


def _excluded() -> set[str]:
    """Symbols momentum must NOT touch: Berkshire clone + DCA target."""
    ex = {DCA_SYMBOL}
    try:
        from .berkshire_clone import clone_symbols
        ex |= clone_symbols()
    except Exception:
        pass
    return ex


def _record(order, status, real, extra=""):
    os.makedirs(PRIVATE, exist_ok=True)
    with open(EXECUTIONS, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "ref_id": order.ref_id, "symbol": order.symbol,
            "order": order.describe(), "executor": "momentum",
            "result_status": status, "real_money": real,
            "strategy": "momentum-12-1", "detail": extra}) + "\n")


def rebalance(execute: bool) -> None:
    ranking, closes = rank_momentum()
    top = ranking[:TOP_N]
    target = {s for s, _ in top}
    rank_of = {s: i + 1 for i, (s, _) in enumerate(ranking)}
    excluded = _excluded()

    mode = "Mom%d-Pullback" % TOP_N if PULLBACK else "Mom%d" % TOP_N
    print(f"{mode} — target top-{TOP_N} by 12-1 momentum (as of {date.today()}), "
          f"sell past rank {SELL_RANK}:")
    for s, mom in top:
        ok, why = pullback_ready(closes.get(s, []))
        mark = "ENTRY OK" if ok else "wait"
        print(f"  {s:<6} {mom:+7.1%} trailing 12-1   [{mark}: {why}]")
    if target & excluded:
        skip = target & excluded
        print(f"  (note: {', '.join(sorted(skip))} also held by clone/DCA — "
              f"momentum will not double-manage; treated as already held)")

    # current holdings via the executor's session
    from .live_executor import get_executor
    try:
        ex, label = get_executor()
    except Exception as e:
        print(f"\nCannot reach Robinhood ({e}). Showing target only.")
        return
    positions = ex.rh.account.get_open_stock_positions(account_number=ACCOUNT) or []
    held = {}
    for p in positions:
        q = float(p.get("quantity") or 0)
        if q <= 0:
            continue
        sym = ex.rh.stocks.get_symbol_by_url(p["instrument"])
        held[sym] = (q, p.get("created_at", "")[:10])

    momentum_held = {s for s in held if s not in excluded}
    # Hysteresis: sell only once a name has fallen PAST SELL_RANK (or vanished
    # from the ranking), not merely out of the top-N. Prevents intraday churn.
    to_sell = sorted(s for s in momentum_held if rank_of.get(s, 10**6) > SELL_RANK)
    # Entry timing: a target name is only bought while it is dipping. Names
    # that are not dipping stay on the list and get bought on a later run —
    # which is exactly what the 4x/day schedule is for.
    candidates = sorted(target - set(held) - excluded)
    to_buy, waiting = [], []
    for s in candidates:
        ok, why = pullback_ready(closes.get(s, []))
        (to_buy if ok else waiting).append((s, why))
    to_buy = [s for s, _ in to_buy]

    # Record what momentum owns/wants so the dip-scanner's exit engine skips it.
    _save_state(sorted((momentum_held | target) - excluded))

    print(f"\nRebalance plan:")
    print(f"  SELL (fell past rank {SELL_RANK}): {to_sell or 'none'}")
    print(f"  BUY  (dipping now): {to_buy or 'none'}")
    if waiting:
        print(f"  WAIT (in target, no pullback yet): "
              + ", ".join(f"{s} ({w})" for s, w in waiting))
    print(f"  HELD (still top-{TOP_N}): {sorted(momentum_held & target) or 'none'}")

    if not execute:
        print("\nDRY-RUN (no orders placed). Arm with MOMENTUM_LIVE=1 and run "
              "--rebalance to execute.")
        return
    if not MOMENTUM_LIVE:
        print("\nRefusing to execute: MOMENTUM_LIVE is not set.")
        return

    import trading_agent.live_scanner as sc
    from .poc.order import Order, OrderType, Side

    # SELLS first (uncapped) — free cash, exit dropped names
    today = date.today()
    for sym in to_sell:
        q, created = held[sym]
        if created and (today - date.fromisoformat(created)).days < 1:
            print(f"  {sym}: skip sell (bought today, PDT-safe)")
            continue
        order = Order(account_number=ACCOUNT, symbol=sym, side=Side.SELL,
                      type=OrderType.MARKET, quantity=round(q, 6))
        try:
            r = ex.place(order)
            _record(order, r.get("status"), r.get("real_money", False))
            print(f"  SOLD {sym} -> {r.get('status')}")
        except Exception as e:
            _record(order, "rejected", False, str(e))
            print(f"  SELL {sym} rejected: {e}")

    # BUYS via the shared gate chain (daily cap, dedupe, rejection halt)
    from .add_tickets import add as add_ticket
    dollars = float(os.environ.get("MOMENTUM_DOLLARS", "600"))
    for sym in to_buy:
        try:
            add_ticket([sym], dollars, f"momentum 12-1 rebalance {today}")
        except SystemExit:
            pass
        outcome = sc._auto_execute(sym)
        print(f"  BUY {sym} -> {outcome}")
        if "cap reached" in outcome:
            print("  (daily BUY cap hit — remaining buys go on the next run)")
            break


def main() -> None:
    rebalance(execute="--rebalance" in sys.argv)


if __name__ == "__main__":
    main()
