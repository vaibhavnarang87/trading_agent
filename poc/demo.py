"""
POC demo — runs the full approval flow end-to-end.

Uses a recorded REAL review response (captured live from the Robinhood MCP for a
$100 SPY market buy) so it runs offline. On your machine you pass the actual MCP
review_equity_order as review_fn, and your own OrderExecutor as executor.

Run: python -m trading_agent.poc.demo
"""
from __future__ import annotations

from datetime import date

from ..audit import AuditLog
from ..config import RiskLimits
from ..types import AccountState, DailyState
from .approval_flow import ApprovalFlow
from .execution import PaperExecutor
from .order import Order, OrderType, Side


# Recorded REAL review response (live MCP, SPY $100 market buy, 2026-06-25)
REAL_REVIEW = {
    "data": {
        "symbol": "SPY", "side": "buy", "type": "market", "dollar_amount": "100.00",
        "order_checks": {},
        "quote_data": {
            "symbol": "SPY", "last_trade_price": "735.480000",
            "previous_close": "733.240000",
            "bid_price": "735.480000", "ask_price": "735.510000", "state": "active",
        },
        "market_data_disclosure":
            "Bid $735.44 × 80 Q · Ask $735.47 × 80 Q · Last $735.44 × 40 P. Updated 12:20 PM ET.",
    }
}


def fake_review_fn(**params) -> dict:
    """Stand-in for Robinhood MCP review_equity_order. Returns the recorded real
    response. On your machine, replace with the actual MCP call."""
    return REAL_REVIEW


def cli_approval(preview) -> bool:
    print("\n" + preview.render())
    ans = input("\nApprove and place? [y/N] ").strip().lower()
    return ans == "y"


def auto_yes(preview) -> bool:
    print("\n" + preview.render())
    print("[demo] auto-approving to show the placement hand-off")
    return True


def main():
    account = AccountState(account_value=5000.0, cash=5000.0, positions={})
    daily = DailyState(day=date.today())
    limits = RiskLimits(max_order_value=250.0, max_position_pct=0.10)

    order = Order(
        account_number="PAPER-ACCOUNT", symbol="SPY",
        side=Side.BUY, type=OrderType.MARKET, dollar_amount=100.0,
    )

    flow = ApprovalFlow(
        limits=limits,
        review_fn=fake_review_fn,         # -> your MCP review_equity_order
        approval_fn=auto_yes,             # -> your UI button (use cli_approval to type)
        executor=PaperExecutor(lambda s: 735.48),  # -> your live OrderExecutor
        audit=AuditLog(),
    )

    print("=== POC approval flow: build -> review -> govern -> approve -> place ===")
    result = flow.submit(order, account, daily)
    print(f"\nRESULT: {result['status']}")
    if result["status"] == "placed":
        print(f"  fill: {result['result']}")
    print("\nNote: this demo used PaperExecutor. Wire your own OrderExecutor.place()")
    print("to the Robinhood MCP to make the final step real — that part is yours.")


if __name__ == "__main__":
    main()
