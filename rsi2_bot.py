"""
RSI2-Momo20 swing bot — the best-performing pattern strategy from the tests.

Rule (deterministic, daily bars, from backtest_swing.py):
  ENTRY  : RSI(2) < 10  AND  price > 200-day MA  AND  name in the momentum
           top-20 (quality gate). All three required — the trend/quality
           filters are what separate this from the intraday patterns that lost.
  EXIT   : RSI(2) > 70  OR  held >= 10 trading days  OR  P&L <= -5%
  SIZING : up to MAX_POS concurrent equal-weight positions.

Backtested 8y: +12.1% CAGR, Sharpe 1.14, maxDD -13%.
Honest context: that TRAILS buy-and-hold SPY on return (+16.1%) while beating
it badly on risk (SPY: Sharpe 0.85, maxDD -34%). It is a high-activity,
risk-managed sleeve — not a higher-return one.

Safety, identical to the other engines:
  * RSI2_LIVE=1 arms real execution (the user's act); otherwise dry-run only.
  * Owns its symbols exclusively — skips anything held by the Berkshire clone
    or the momentum sleeve, and records its own so their exit rules skip it.
  * Shared executor, risk governor, daily BUY cap, uncapped sells, PDT-safe.

    python -m trading_agent.rsi2_bot              # dry-run
    python -m trading_agent.rsi2_bot --rebalance  # execute (needs RSI2_LIVE=1)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

from .env_file import load_env_file

load_env_file()

RSI2_LIVE = os.environ.get("RSI2_LIVE") == "1"
MAX_POS = int(os.environ.get("RSI2_MAX_POS", "3"))
DOLLARS = float(os.environ.get("RSI2_DOLLARS", "300"))
ENTRY_RSI = float(os.environ.get("RSI2_ENTRY_RSI", "10"))
EXIT_RSI = float(os.environ.get("RSI2_EXIT_RSI", "70"))
TIME_STOP = int(os.environ.get("RSI2_TIME_STOP", "10"))
STOP_LOSS = -abs(float(os.environ.get("RSI2_STOP_PCT", "5"))) / 100
MOMO_TOP = int(os.environ.get("RSI2_MOMO_TOP", "20"))
ACCOUNT = os.environ.get("TRADING_ACCOUNT_NUMBER", "PAPER-ACCOUNT")

HERE = os.path.dirname(__file__)
PRIVATE = os.path.join(HERE, "data", "private")
STATE = os.path.join(PRIVATE, "rsi2_state.json")
EXECUTIONS = os.path.join(PRIVATE, "executions.jsonl")


def rsi2_symbols() -> set[str]:
    """Symbols this bot manages. Other engines' exit rules must SKIP these —
    RSI2 exits on its own signal, not their stops."""
    try:
        return set(json.load(open(STATE)).get("positions", {}))
    except Exception:
        return set()


def _load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {"positions": {}}


def _save_state(state: dict) -> None:
    os.makedirs(PRIVATE, exist_ok=True)
    state["updated"] = datetime.now(timezone.utc).isoformat()
    json.dump(state, open(STATE, "w"), indent=2)


def _rsi(closes: list[float], period: int = 2) -> float | None:
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


def _excluded() -> set[str]:
    """Symbols owned by other strategies — never touch them."""
    out = {os.environ.get("DCA_SYMBOL", "VOO")}
    for mod, fn in (("berkshire_clone", "clone_symbols"),
                    ("momentum_bot", "momentum_symbols")):
        try:
            m = __import__(f"trading_agent.{mod}", fromlist=[fn])
            out |= getattr(m, fn)()
        except Exception:
            pass
    return out


def scan() -> tuple[list[dict], dict[str, list[float]]]:
    """Evaluate the universe. Returns (candidate rows, closes)."""
    import yfinance as yf
    from .momentum_bot import UNIVERSE
    df = yf.download(UNIVERSE, period="2y", interval="1d",
                     group_by="ticker", progress=False, threads=True)
    closes, momo = {}, []
    for s in UNIVERSE:
        try:
            c = [float(x) for x in df[s]["Close"].dropna().tolist()]
            if len(c) < 260:
                continue
            closes[s] = c
            momo.append((c[-21] / c[-252] - 1, s))
        except Exception:
            continue
    momo.sort(reverse=True)
    top = {s for _, s in momo[:MOMO_TOP]}

    rows = []
    for s, c in closes.items():
        ma200 = sum(c[-200:]) / 200 if len(c) >= 200 else None
        r = _rsi(c)
        rows.append({
            "symbol": s, "price": c[-1], "rsi2": r, "ma200": ma200,
            "in_top": s in top,
            "uptrend": ma200 is not None and c[-1] > ma200,
            "oversold": r is not None and r < ENTRY_RSI,
        })
    return rows, closes


def rebalance(execute: bool) -> None:
    rows, closes = scan()
    state = _load_state()
    positions = state.get("positions", {})
    excluded = _excluded()
    today = date.today()

    print(f"RSI2-Momo20 — entry: RSI(2)<{ENTRY_RSI:.0f} + above 200dMA + "
          f"momentum top-{MOMO_TOP} | exit: RSI(2)>{EXIT_RSI:.0f} / "
          f"{TIME_STOP}d / {STOP_LOSS:.0%}")
    print(f"  max {MAX_POS} positions x ${DOLLARS:.0f}, "
          f"{'ARMED' if RSI2_LIVE else 'not armed (RSI2_LIVE unset)'}")

    by_sym = {r["symbol"]: r for r in rows}

    # ---- exits ----
    to_sell = []
    for sym, info in list(positions.items()):
        r = by_sym.get(sym)
        if not r:
            continue
        pnl = r["price"] / info["entry_price"] - 1
        held_days = (today - date.fromisoformat(info["entry_date"])).days
        why = None
        if r["rsi2"] is not None and r["rsi2"] > EXIT_RSI:
            why = f"RSI(2) {r['rsi2']:.0f} > {EXIT_RSI:.0f}"
        elif held_days >= TIME_STOP:
            why = f"time stop {held_days}d"
        elif pnl <= STOP_LOSS:
            why = f"stop {pnl:.1%}"
        if why:
            to_sell.append((sym, info["quantity"], why, pnl, held_days))

    # ---- entries ----
    slots = MAX_POS - (len(positions) - len(to_sell))
    cands = sorted(
        (r for r in rows
         if r["oversold"] and r["uptrend"] and r["in_top"]
         and r["symbol"] not in positions and r["symbol"] not in excluded),
        key=lambda r: r["rsi2"])
    to_buy = cands[:max(slots, 0)]

    print(f"\n  HELD ({len(positions)}): "
          f"{sorted(positions) if positions else 'none'}")
    print(f"  SELL: " + (", ".join(f"{s} ({w}, {p:+.1%})"
                                   for s, _, w, p, _ in to_sell) or "none"))
    print(f"  BUY : " + (", ".join(f"{r['symbol']} (RSI(2) {r['rsi2']:.0f})"
                                   for r in to_buy) or "none"))
    if not to_buy and slots > 0:
        near = sorted((r for r in rows if r["uptrend"] and r["in_top"]
                       and r["rsi2"] is not None),
                      key=lambda r: r["rsi2"])[:3]
        print("  (closest to triggering: "
              + ", ".join(f"{r['symbol']} RSI(2) {r['rsi2']:.0f}" for r in near) + ")")

    if not execute:
        print("\nDRY-RUN (no orders placed). Arm with RSI2_LIVE=1 and run "
              "--rebalance to execute.")
        return
    if not RSI2_LIVE:
        print("\nRefusing to execute: RSI2_LIVE is not set.")
        return

    import trading_agent.live_scanner as sc
    from .add_tickets import add as add_ticket
    from .poc.order import Order, OrderType, Side

    ex, label = sc._get_executor()
    if ex is None:
        print(f"  cannot execute: {label}")
        return

    def _rec(order, status, real, extra=""):
        with open(EXECUTIONS, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "ref_id": order.ref_id, "symbol": order.symbol,
                "order": order.describe(), "executor": label,
                "result_status": status, "real_money": real,
                "strategy": "rsi2-momo20", "detail": extra}) + "\n")

    # sells first (uncapped, frees cash)
    for sym, qty, why, pnl, held_days in to_sell:
        if held_days < 1:
            print(f"  {sym}: skip sell (same-day, PDT-safe)")
            continue
        order = Order(account_number=ACCOUNT, symbol=sym, side=Side.SELL,
                      type=OrderType.MARKET, quantity=round(float(qty), 6))
        try:
            res = ex.place(order)
            _rec(order, res.get("status"), res.get("real_money", False), why)
            positions.pop(sym, None)
            print(f"  SOLD {sym} ({why}, {pnl:+.1%}) -> {res.get('status')}")
        except Exception as e:
            _rec(order, "rejected", False, str(e))
            print(f"  SELL {sym} rejected: {e}")

    # buys through the shared gate chain (skipped entirely in sloth mode)
    from .mode import buys_paused
    if buys_paused() and to_buy:
        print("  BUYS PAUSED (sloth mode) — entries skipped, exits done above.")
        to_buy = []
    for r in to_buy:
        sym = r["symbol"]
        try:
            add_ticket([sym], DOLLARS, f"RSI2-Momo20 entry RSI(2) {r['rsi2']:.0f}",
                       allow_existing=True)
        except SystemExit:
            pass
        outcome = sc._auto_execute(sym)
        print(f"  BUY {sym} -> {outcome}")
        if "EXECUTED" in outcome:
            positions[sym] = {"entry_price": r["price"],
                              "entry_date": today.isoformat(),
                              "quantity": round(DOLLARS / r["price"], 6)}
        elif "cap reached" in outcome:
            break

    state["positions"] = positions
    _save_state(state)
    print(f"\n  state saved: {len(positions)} position(s) under RSI2 management")


def main() -> None:
    # Market-hours guard lives here, not in the plist — see momentum_bot.main.
    if "--rebalance" in sys.argv and "--force" not in sys.argv:
        from .dca_bot import market_open
        if not market_open():
            print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                  f"market closed — no action")
            return
    rebalance(execute="--rebalance" in sys.argv)


if __name__ == "__main__":
    main()
