"""
Always-on DCA bot — dollar-cost averaging, the evidence-backed autonomy.

Buys a fixed dollar amount of one symbol on a fixed interval, unattended. This
is the rule the backtest actually supports: instead of timing dips (which lost
to buy-and-hold), it just keeps buying the market on a schedule — "time in the
market beats timing the market."

Deterministic and pre-committed: the only decision is "has INTERVAL passed and
is the market open? then buy $AMOUNT of SYMBOL." No LLM in the loop, no
discretion. The risk governor and the paper/real executor are the same ones the
console uses.

    python -m trading_agent.dca_bot --demo     # 3 instant simulated buys
    python -m trading_agent.dca_bot            # always-on loop (paper unless armed)

Config below (or env): DCA_SYMBOL, DCA_DOLLARS, DCA_INTERVAL_DAYS.
Live requires the same gates as the console (TRADING_EXECUTOR=robinhood, armed,
and Robinhood compliance cleared). Default is paper — nothing real.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

from .env_file import load_env_file
from .poc.order import Order, OrderType, Side
from .trade_plan import PRIVATE_DIR

load_env_file()

DCA_SYMBOL = os.environ.get("DCA_SYMBOL", "VOO")
DCA_DOLLARS = float(os.environ.get("DCA_DOLLARS", "100"))
DCA_INTERVAL_DAYS = float(os.environ.get("DCA_INTERVAL_DAYS", "7"))
ACCOUNT = os.environ.get("TRADING_ACCOUNT_NUMBER", "PAPER-ACCOUNT")
LEDGER = os.path.join(PRIVATE_DIR, "dca_ledger.jsonl")
CHECK_SECONDS = int(os.environ.get("DCA_CHECK_SECONDS", "3600"))   # how often to wake


def _buys() -> list[dict]:
    if not os.path.exists(LEDGER):
        return []
    return [json.loads(l) for l in open(LEDGER) if l.strip()]


def _last_buy_time() -> datetime | None:
    b = _buys()
    return datetime.fromisoformat(b[-1]["ts"]) if b else None


def _record(result: dict) -> None:
    os.makedirs(PRIVATE_DIR, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": DCA_SYMBOL, "dollars": DCA_DOLLARS,
                            **result}) + "\n")


def market_open(now: datetime | None = None) -> bool:
    """Rough US regular-hours check in ET (no holiday calendar). Mon-Fri
    9:30-16:00 ET. UTC-4 assumed (EDT); good enough to avoid off-hours buys."""
    now = (now or datetime.now(timezone.utc)) - timedelta(hours=4)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins <= 16 * 60


def due() -> bool:
    last = _last_buy_time()
    if last is None:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(days=DCA_INTERVAL_DAYS)


def buy_once(executor, label: str) -> dict | None:
    """Place one DCA buy through the executor. Returns the result or None."""
    order = Order(account_number=ACCOUNT, symbol=DCA_SYMBOL, side=Side.BUY,
                  type=OrderType.MARKET, dollar_amount=round(DCA_DOLLARS, 2))
    errs = order.validate()
    if errs:
        print(f"  invalid order: {errs}")
        return None
    try:
        result = executor.place(order)
    except Exception as e:
        print(f"  order rejected: {type(e).__name__}: {e}")
        _record({"status": "rejected", "detail": str(e), "executor": label})
        return None
    _record({"status": result.get("status", "?"),
             "real_money": result.get("real_money", False), "executor": label})
    print(f"  BOUGHT ${DCA_DOLLARS:.0f} {DCA_SYMBOL}  [{label}]  -> {result.get('status')}")
    return result


def _dca_executor():
    """DCA live-ness is SEPARATE from the console's arming: an always-on
    background bot must never inherit real-money mode by accident. It stays
    paper unless you deliberately set DCA_LIVE=1 (and the robinhood executor
    is configured)."""
    from .live_executor import PaperExecutor
    if os.environ.get("DCA_LIVE") == "1" and \
       os.environ.get("TRADING_EXECUTOR", "").lower() == "robinhood":
        try:
            from .live_executor import get_executor
            return get_executor()
        except Exception as e:
            print(f"NOTE: {e}\n      running paper.")
            return PaperExecutor(), "paper (live setup incomplete)"
    return PaperExecutor(), "paper (DCA_LIVE not set)"


def run_forever() -> None:
    executor, label = _dca_executor()
    real = "REAL MONEY" in label
    print(f"DCA bot: ${DCA_DOLLARS:.0f} {DCA_SYMBOL} every {DCA_INTERVAL_DAYS:g}d "
          f"| executor: {label}")
    if real:
        print("!!! REAL-MONEY DCA ACTIVE — buys place real orders on schedule.")
    print("Ctrl-C to stop.\n")
    while True:
        if due() and market_open():
            print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] due + market open")
            buy_once(executor, label)
        time.sleep(CHECK_SECONDS)


def demo() -> None:
    """3 instant simulated buys so you can see it work with zero risk."""
    from .live_executor import PaperExecutor
    ex = PaperExecutor()
    print(f"DEMO: 3 simulated ${DCA_DOLLARS:.0f} {DCA_SYMBOL} buys (no real money)\n")
    for k in range(3):
        buy_once(ex, "paper (demo)")
    print(f"\nLedger now has {len(_buys())} buy(s) at {LEDGER}")
    print("In real always-on mode these would be spaced "
          f"{DCA_INTERVAL_DAYS:g} days apart during market hours.")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        run_forever()
