"""
Show today's ready-to-place tickets — the human placement handoff.

This reads the latest PRIVATE trade plan and prints each risk-approved,
live-armed ticket together with the exact Robinhood MCP call you would make. It
is deliberately READ-ONLY: it prints what to place, it does not place anything.
Pulling the trigger is your step, in your own Robinhood session, by design.

    python -m trading_agent.show_tickets

To actually place a ticket, you run the Robinhood call yourself, e.g. in your
own MCP-enabled client:

    place_equity_order(**params)   # params printed below — you run this, not the bot
"""
from __future__ import annotations

import glob
import json
import os

from .trade_plan import PRIVATE_DIR


def _latest_plan_path() -> str | None:
    files = sorted(glob.glob(os.path.join(PRIVATE_DIR, "trade_plan_*.json")))
    return files[-1] if files else None


def main() -> None:
    path = _latest_plan_path()
    if not path:
        print("No trade plan found. Run: python -m trading_agent.briefing_daily")
        return

    plan = json.load(open(path))
    armed = plan.get("armed", False)
    ready = [t for t in plan.get("tickets", []) if t.get("status") == "live-armed"]

    print(f"Trade plan {plan['date']}  |  switch: "
          f"{'LIVE-ARMED' if armed else 'PAPER (safe)'}")
    print("=" * 60)

    if not armed:
        print("Switch is in PAPER mode — no live-armed tickets. Nothing to place.")
        print("Nothing here touches real money.")
        return

    if not ready:
        print("No risk-approved tickets today. Nothing to place.")
        return

    print("These tickets are risk-approved and READY. To place one, YOU run the")
    print("Robinhood call below in your own session. This tool does not place it.\n")
    for i, t in enumerate(ready, 1):
        print(f"[{i}] {t['describe']}")
        print(f"     why:    {'; '.join(t['reasons'])}")
        print(f"     YOU run: place_equity_order(**{t['broker_params']})")
        print()
    print("-" * 60)
    print("Reminder: placing is your action. Review each order in Robinhood before")
    print("you confirm. The bot builds and checks the ticket; you own the trigger.")


if __name__ == "__main__":
    main()
