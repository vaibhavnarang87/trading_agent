"""
Portfolio report — tax-aware performance + volatility-adjusted sizing.

Built after Robinhood exposed richer agentic data (tax lots, server-side
technical indicators). Those endpoints live in the MCP layer, which this
standalone bot cannot call — so this module computes the same two things
LOCALLY from data the bot already has:

  1. TAX-AWARE P&L. Realized (FIFO-matched from the execution ledger) and
     unrealized P&L, with each lot's holding period and short/long-term
     status. Matters because a monthly-rebalanced momentum strategy realizes
     almost everything SHORT-term, taxed as ordinary income — a drag the
     backtest's pre-tax CAGR does not show.

  2. VOLATILITY-ADJUSTED SIZING. True-range ATR per holding, and the position
     size that would equalize dollar-risk across names. Flat $200 tickets put
     ~10x more risk into a 9%-ATR name (INTC) than a 1%-ATR name.

    python -m trading_agent.portfolio_report
    python -m trading_agent.portfolio_report --tax-rate 0.32

Report only — places no orders and changes no config.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict, deque
from datetime import date, datetime

from .env_file import load_env_file

load_env_file()

HERE = os.path.dirname(__file__)
PRIVATE = os.path.join(HERE, "data", "private")
EXECUTIONS = os.path.join(PRIVATE, "executions.jsonl")
ACCOUNT = os.environ.get("TRADING_ACCOUNT_NUMBER", "")
RISK_PER_POSITION = float(os.environ.get("RISK_PER_POSITION", "20"))  # $ of ATR risk


def _fills() -> list[dict]:
    """Successful fills from the ledger, oldest first."""
    if not os.path.exists(EXECUTIONS):
        return []
    out = []
    for line in open(EXECUTIONS):
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("result_status") in ("rejected", None):
            continue
        order = e.get("order", "")
        parts = order.split()
        if not parts:
            continue
        side = parts[0].lower()
        sym = e.get("symbol") or (parts[2] if side == "buy" else parts[2])
        out.append({"ts": e["ts"], "side": side, "symbol": sym,
                    "order": order, "strategy": e.get("strategy", ""),
                    "reason": e.get("exit_reason", "")})
    out.sort(key=lambda r: r["ts"])
    return out


def realized_pnl(rh) -> tuple[list[dict], float]:
    """FIFO-match sells against buys using broker order history (authoritative
    prices), falling back to the ledger for which symbols to inspect."""
    symbols = {f["symbol"] for f in _fills() if f["symbol"]}
    closed, total = [], 0.0
    for sym in sorted(symbols):
        try:
            orders = rh.orders.find_stock_orders(symbol=sym) or []
        except Exception:
            continue
        rows = []
        for o in orders:
            if o.get("state") != "filled":
                continue
            qty = float(o.get("cumulative_quantity") or 0)
            px = float(o.get("average_price") or 0)
            if qty <= 0 or px <= 0:
                continue
            rows.append({"ts": o.get("last_transaction_at") or o.get("created_at"),
                         "side": o["side"], "qty": qty, "px": px})
        rows.sort(key=lambda r: r["ts"])
        lots: deque = deque()
        for r in rows:
            if r["side"] == "buy":
                lots.append([r["qty"], r["px"], r["ts"]])
            else:
                remaining = r["qty"]
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    take = min(remaining, lot[0])
                    pnl = take * (r["px"] - lot[1])
                    held_days = (datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).date()
                                 - datetime.fromisoformat(lot[2].replace("Z", "+00:00")).date()).days
                    closed.append({"symbol": sym, "qty": take, "buy": lot[1],
                                   "sell": r["px"], "pnl": pnl,
                                   "days": held_days,
                                   "term": "long" if held_days > 365 else "short"})
                    total += pnl
                    lot[0] -= take
                    remaining -= take
                    if lot[0] <= 1e-9:
                        lots.popleft()
    return closed, total


def atr_pct(rh, symbol: str, period: int = 14) -> float | None:
    """True-range ATR as % of price, computed locally from daily bars."""
    try:
        bars = rh.stocks.get_stock_historicals(symbol, interval="day",
                                               span="3month") or []
        bars = [b for b in bars if b and b.get("high_price")]
        if len(bars) < period + 1:
            return None
        trs = []
        for i in range(1, len(bars)):
            h = float(bars[i]["high_price"]); l = float(bars[i]["low_price"])
            pc = float(bars[i - 1]["close_price"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = sum(trs[-period:]) / period
        last = float(bars[-1]["close_price"])
        return atr / last if last else None
    except Exception:
        return None


def main() -> None:
    tax_rate = 0.30
    if "--tax-rate" in sys.argv:
        tax_rate = float(sys.argv[sys.argv.index("--tax-rate") + 1])

    from .live_executor import RobinhoodExecutor
    try:
        ex = RobinhoodExecutor()
    except Exception as e:
        print(f"Cannot reach Robinhood: {e}")
        return
    rh = ex.rh

    print("=" * 72)
    print(f"PORTFOLIO REPORT — {date.today()}")
    print("=" * 72)

    # ---- realized ----
    closed, total_realized = realized_pnl(rh)
    st = sum(c["pnl"] for c in closed if c["term"] == "short")
    lt = sum(c["pnl"] for c in closed if c["term"] == "long")
    print(f"\nREALIZED ({len(closed)} closed lots)")
    for c in sorted(closed, key=lambda c: c["pnl"]):
        print(f"  {c['symbol']:<6} {c['qty']:>8.4f} sh  ${c['buy']:>8.2f} -> "
              f"${c['sell']:>8.2f}  {c['pnl']:>+8.2f}  {c['days']:>3}d {c['term']}")
    print(f"  {'TOTAL':<6} {'':>8}  {'':>8}    {'':>8}  {total_realized:>+8.2f}")
    print(f"  short-term {st:+.2f} | long-term {lt:+.2f}")
    if st > 0:
        print(f"  est. tax on short-term gains @ {tax_rate:.0%}: "
              f"-${st * tax_rate:.2f}  (ordinary income, not 15% LTCG)")

    # ---- unrealized + volatility sizing ----
    positions = rh.account.get_open_stock_positions(account_number=ACCOUNT) or []
    print(f"\nOPEN POSITIONS ({len(positions)})")
    print(f"  {'sym':<6}{'qty':>9}{'cost':>10}{'now':>10}{'P&L':>9}"
          f"{'held':>6}{'ATR%':>7}{'sized$':>8}")
    tot_cost = tot_val = 0.0
    today = date.today()
    for p in positions:
        q = float(p.get("quantity") or 0)
        if q <= 0:
            continue
        sym = rh.stocks.get_symbol_by_url(p["instrument"])
        avg = float(p.get("average_buy_price") or 0)
        try:
            px = float(rh.stocks.get_latest_price(sym)[0])
        except Exception:
            continue
        created = (p.get("created_at") or "")[:10]
        held = (today - date.fromisoformat(created)).days if created else 0
        a = atr_pct(rh, sym)
        # size that risks RISK_PER_POSITION dollars per 1 ATR move
        sized = (RISK_PER_POSITION / a) if a else None
        tot_cost += q * avg
        tot_val += q * px
        print(f"  {sym:<6}{q:>9.4f}{avg:>10.2f}{px:>10.2f}"
              f"{(px/avg-1):>+8.1%}{held:>5}d"
              f"{(a*100 if a else 0):>7.1f}{(sized if sized else 0):>8.0f}")
    unreal = tot_val - tot_cost
    print(f"  cost ${tot_cost:,.2f} -> value ${tot_val:,.2f}   "
          f"unrealized {unreal:+,.2f} ({(tot_val/tot_cost-1) if tot_cost else 0:+.1%})")

    print(f"\nTOTAL P&L (realized + unrealized): {total_realized + unreal:+,.2f}")
    print("\nNotes")
    print(f"  * 'sized$' = position size that risks ~${RISK_PER_POSITION:.0f} per 1-ATR")
    print("    move, i.e. equal RISK per name instead of equal dollars. Flat $200")
    print("    tickets put far more risk in high-ATR names.")
    print("  * Monthly-turnover strategies realize gains SHORT-term (ordinary")
    print("    income). Compare after-tax, not just the backtest's pre-tax CAGR.")
    print("  * Informational only — not tax or investment advice.")


if __name__ == "__main__":
    main()
