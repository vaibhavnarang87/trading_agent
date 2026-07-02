"""
Live executor — YOUR Robinhood access, wired to the poc OrderExecutor interface.

This implements the one piece the project deliberately left to you: place().
It talks DIRECTLY to Robinhood with YOUR login via the robin_stocks library.
Nothing routes through an AI assistant, the public site, or anyone else's
session: your credentials live in env vars you set, the login (incl. MFA
prompt) happens in your terminal, and the order fires only when you click
Execute in the localhost console and confirm.

Setup (only if/when you choose to go live):
    pip install robin_stocks
    export RH_USERNAME="you@example.com"
    export RH_PASSWORD="..."            # your machine, your env, never committed
    export TRADING_EXECUTOR=robinhood   # default is "paper" (simulated fills)
    python -m trading_agent.local_app   # login/MFA prompt appears at startup

Notes, honestly:
  - robin_stocks is an UNOFFICIAL library (community wrapper around Robinhood's
    private endpoints). Using it is your call and subject to Robinhood's terms.
  - Default remains the PaperExecutor. Real money requires you to explicitly
    set TRADING_EXECUTOR=robinhood AND arm the switch AND click AND confirm.
"""
from __future__ import annotations

import os

from .poc.execution import OrderExecutor, PaperExecutor
from .poc.order import Order, OrderType


class RobinhoodExecutor(OrderExecutor):
    """Direct-API executor. Credentials come from YOUR env; login happens in
    YOUR terminal at startup. place() maps a validated Order to the matching
    robin_stocks order call and returns the raw broker response."""

    def __init__(self):
        try:
            import robin_stocks.robinhood as rh
        except ImportError as e:
            raise RuntimeError(
                "robin_stocks is not installed. Run: pip install robin_stocks"
            ) from e
        user = os.environ.get("RH_USERNAME")
        pwd = os.environ.get("RH_PASSWORD")
        if not user or not pwd:
            raise RuntimeError(
                "Set RH_USERNAME and RH_PASSWORD env vars in your shell. "
                "They stay on your machine; this code never stores or sends "
                "them anywhere except Robinhood's own login."
            )
        self.rh = rh
        # Interactive: robin_stocks prompts for MFA in the terminal — that
        # prompt is you, personally, authorizing this session.
        self.rh.login(user, pwd)

    def place(self, order: Order) -> dict:
        o = self.rh.orders
        tif = order.time_in_force
        if order.type == OrderType.MARKET and order.dollar_amount is not None:
            if order.side.value == "buy":
                resp = o.order_buy_fractional_by_price(
                    order.symbol, order.dollar_amount, timeInForce=tif)
            else:
                resp = o.order_sell_fractional_by_price(
                    order.symbol, order.dollar_amount, timeInForce=tif)
        elif order.type == OrderType.LIMIT and order.quantity is not None:
            if order.side.value == "buy":
                resp = o.order_buy_limit(
                    order.symbol, order.quantity, order.limit_price, timeInForce=tif)
            else:
                resp = o.order_sell_limit(
                    order.symbol, order.quantity, order.limit_price, timeInForce=tif)
        else:
            raise ValueError(f"Unsupported order shape: {order.describe()}")
        return {"status": "submitted", "real_money": True,
                "order": order.describe(), "ref_id": order.ref_id,
                "broker_response": resp}


def get_executor() -> tuple[OrderExecutor, str]:
    """Executor + human-readable label, chosen by TRADING_EXECUTOR env var.
    Default is paper: simulated fills, no credentials, no real money."""
    kind = os.environ.get("TRADING_EXECUTOR", "paper").strip().lower()
    if kind == "robinhood":
        return RobinhoodExecutor(), "ROBINHOOD — REAL MONEY"
    return PaperExecutor(), "paper (simulated fills)"
