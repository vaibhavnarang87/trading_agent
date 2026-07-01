"""
POC: the execution boundary.

This is the ONE method the bot does not implement for you: the live order
placement. It's an abstract interface with a deliberate stub. You implement
place() in your own runtime, with your own Robinhood credentials / MCP client.

Why it's yours and not the bot's:
  - Your brokerage credentials should never route through the assistant that
    wrote this code. You own the trigger and the auth.
  - It keeps the irreversible, real-money action under your hand and your
    review, which is the whole design.

The bot builds and validates the Order, runs the risk governor, fetches a live
review preview, and surfaces it for your approval. When you approve, the flow
calls executor.place(order) — and that lands here, in code you wrote.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .order import Order


class OrderExecutor(ABC):
    @abstractmethod
    def place(self, order: Order) -> dict:
        """Place the live order and return the broker response. YOU implement."""
        raise NotImplementedError


class UnimplementedExecutor(OrderExecutor):
    """Default. Refuses to place — forces you to implement execution yourself."""

    def place(self, order: Order) -> dict:
        raise NotImplementedError(
            "Live execution is intentionally not implemented in this POC.\n"
            "Implement place() yourself, e.g. by calling the Robinhood MCP:\n\n"
            "    class MyExecutor(OrderExecutor):\n"
            "        def place(self, order):\n"
            "            return robinhood_client.place_equity_order(\n"
            "                **order.to_broker_params())\n\n"
            f"Order that would have been placed: {order.describe()}\n"
            f"Broker params: {order.to_broker_params()}"
        )


class PaperExecutor(OrderExecutor):
    """Safe alternative: 'places' into a simulated fill so you can run the full
    POC end-to-end without real money. Use until you choose to wire live."""

    def __init__(self, last_price_lookup=None):
        self.last_price_lookup = last_price_lookup or (lambda sym: None)
        self.fills: list[dict] = []

    def place(self, order: Order) -> dict:
        px = order.limit_price or self.last_price_lookup(order.symbol)
        fill = {
            "status": "simulated_fill",
            "order": order.describe(),
            "ref_id": order.ref_id,
            "assumed_price": px,
            "real_money": False,
        }
        self.fills.append(fill)
        return fill
