"""
Add manual tickets to today's trade plan.

Lets you drop specific symbols (e.g. a copied portfolio) into the console's
plan as risk-checked order tickets, without waiting for the deterministic
screen. Each symbol becomes a $-sized market buy, runs through the SAME risk
governor as every other ticket, and is appended to the latest plan file the
console reads. Placing is still your click in the console — this only builds
the tickets.

    python -m trading_agent.add_tickets AAPL AXP KO BAC CVX
    python -m trading_agent.add_tickets --dollars 150 AAPL MSFT

Symbols already in the plan are skipped (no duplicates).
"""
from __future__ import annotations

import glob
import json
import os
import sys
from dataclasses import asdict

from .briefing_daily import TRADE_CONFIG, DOLLARS_PER_TRADE
from .screener import Candidate
from .trade_plan import PRIVATE_DIR, build_plan, write_plan


def _price(symbol: str) -> float | None:
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period="5d", interval="1d")
        return float(h["Close"].dropna().iloc[-1]) if not h.empty else None
    except Exception:
        return None


def _latest_plan_path() -> str | None:
    files = sorted(glob.glob(os.path.join(PRIVATE_DIR, "trade_plan_*.json")))
    return files[-1] if files else None


def add(symbols: list[str], dollars: float, reason: str) -> None:
    path = _latest_plan_path()
    if not path:
        raise SystemExit("No plan on disk yet — run: python -m trading_agent.briefing_daily")
    plan = json.load(open(path))
    existing = {t["symbol"] for t in plan.get("tickets", [])}

    candidates = []
    for sym in symbols:
        sym = sym.upper().strip()
        if sym in existing:
            print(f"  {sym}: already in plan, skipping")
            continue
        px = _price(sym)
        if px is None:
            print(f"  {sym}: no price, skipping")
            continue
        candidates.append(Candidate(sym, px, True, [reason], []))

    if not candidates:
        print("Nothing to add.")
        return

    # Reuse the exact same governor-backed ticket builder as the daily plan.
    built = build_plan(candidates, TRADE_CONFIG, dollars_per_trade=dollars)
    for t in built.tickets:
        plan["tickets"].append(asdict(t))
        print(f"  + {t.describe}  [{t.status}]  governor: {t.governor_reason}")

    with open(path, "w") as f:
        json.dump(plan, f, indent=2)
    ready = sum(1 for t in plan["tickets"] if t.get("status") == "live-armed")
    print(f"\nUpdated {os.path.basename(path)} — {ready} live-armed ticket(s) total.")
    print("Refresh the console to see the new EXECUTE buttons.")


def main() -> None:
    args = sys.argv[1:]
    dollars = DOLLARS_PER_TRADE
    reason = "manually added"
    syms = []
    i = 0
    while i < len(args):
        if args[i] == "--dollars" and i + 1 < len(args):
            dollars = float(args[i + 1]); i += 2
        elif args[i] == "--reason" and i + 1 < len(args):
            reason = args[i + 1]; i += 2
        else:
            syms.append(args[i]); i += 1
    if not syms:
        raise SystemExit("Usage: python -m trading_agent.add_tickets [--dollars N] SYM ...")
    add(syms, dollars, reason)


if __name__ == "__main__":
    main()
