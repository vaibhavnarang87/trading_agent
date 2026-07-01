"""
POC: the live review (preview) path.

review_order calls an injected review_fn — in your runtime, the Robinhood MCP
review_equity_order tool, which simulates without placing. It returns the live
quote and any pre-trade alerts (buying power, PDT, halts). This is the data the
approval step shows you.

The assistant CAN run this (it places nothing). Only place() is yours.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .order import Order

# review_fn(**broker_params) -> raw broker review dict
ReviewFn = Callable[..., dict]


@dataclass
class Preview:
    order: Order
    last_price: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    alerts: dict
    disclosure: Optional[str]      # MUST be shown verbatim for compliance
    est_cost: Optional[float]

    def render(self) -> str:
        lines = [f"REVIEW: {self.order.describe()}"]
        if self.last_price is not None:
            lines.append(f"  last ${self.last_price:,.2f}"
                         + (f"  bid ${self.bid:,.2f}" if self.bid else "")
                         + (f"  ask ${self.ask:,.2f}" if self.ask else ""))
        if self.est_cost is not None:
            lines.append(f"  est. cost ~${self.est_cost:,.2f}")
        if self.alerts:
            lines.append(f"  ALERTS: {self.alerts}")
        else:
            lines.append("  alerts: none")
        if self.disclosure:
            lines.append(f"  [compliance] {self.disclosure}")
        return "\n".join(lines)


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def review_order(order: Order, review_fn: ReviewFn) -> Preview:
    raw = review_fn(**order.to_broker_params())
    data = raw.get("data", raw)
    q = data.get("quote_data", {})
    last = _f(q.get("last_trade_price"))
    bid = _f(q.get("bid_price"))
    ask = _f(q.get("ask_price"))
    est = None
    if order.dollar_amount is not None:
        est = order.dollar_amount
    elif order.quantity is not None and last is not None:
        ref = ask if order.side.value == "buy" and ask else last
        est = order.quantity * ref
    return Preview(
        order=order, last_price=last, bid=bid, ask=ask,
        alerts=data.get("order_checks", {}) or {},
        disclosure=data.get("market_data_disclosure"),
        est_cost=est,
    )
