"""
POC: the Order object.

A fully-formed, validated equity order — everything the broker needs to place
it. Building and validating this is the bot's job. Placing it is yours (see
execution.py). The Order mirrors the Robinhood review/place tool parameters so
your execute() implementation is a direct pass-through.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Order:
    account_number: str
    symbol: str
    side: Side
    type: OrderType
    quantity: Optional[float] = None        # provide quantity OR dollar_amount
    dollar_amount: Optional[float] = None
    limit_price: Optional[float] = None     # required for LIMIT
    time_in_force: str = "gfd"
    ref_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def validate(self) -> list[str]:
        errs = []
        if not self.account_number:
            errs.append("missing account_number")
        if (self.quantity is None) == (self.dollar_amount is None):
            errs.append("provide exactly one of quantity or dollar_amount")
        if self.dollar_amount is not None and self.type != OrderType.MARKET:
            errs.append("dollar_amount requires a market order")
        if self.type == OrderType.LIMIT and not self.limit_price:
            errs.append("limit order requires limit_price")
        if self.quantity is not None and self.quantity <= 0:
            errs.append("quantity must be positive")
        if self.dollar_amount is not None and self.dollar_amount <= 0:
            errs.append("dollar_amount must be positive")
        return errs

    def to_broker_params(self) -> dict:
        """Exact kwargs for Robinhood review_/place_equity_order."""
        p = {
            "account_number": self.account_number,
            "symbol": self.symbol,
            "side": self.side.value,
            "type": self.type.value,
            "time_in_force": self.time_in_force,
            "ref_id": self.ref_id,
        }
        if self.quantity is not None:
            p["quantity"] = str(self.quantity)
        if self.dollar_amount is not None:
            p["dollar_amount"] = f"{self.dollar_amount:.2f}"
        if self.limit_price is not None:
            p["limit_price"] = f"{self.limit_price:.2f}"
        return p

    def describe(self) -> str:
        size = (f"${self.dollar_amount:.2f}" if self.dollar_amount is not None
                else f"{self.quantity} sh")
        px = f" @ ${self.limit_price:.2f}" if self.limit_price else ""
        return f"{self.side.value.upper()} {size} {self.symbol} ({self.type.value}{px})"
