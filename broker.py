"""
Broker layer.

PaperBroker simulates fills with no real money — this is the default and where
all early work happens.

LiveBroker is intentionally a STUB. It is not wired to real order placement in
this skeleton. Connecting it to the Robinhood MCP place_equity_order tool is a
deliberate, separate step that should only happen after paper + backtest +
governor are all proven, and it stays behind the human approval gate.
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import Side, TradeProposal


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    quantity: float
    price: float
    simulated: bool


class PaperBroker:
    """Simulated fills. Assumes the order fills at the proposal's limit price."""

    def execute(self, proposal: TradeProposal) -> Fill:
        return Fill(
            symbol=proposal.symbol,
            side=proposal.side,
            quantity=proposal.quantity,
            price=proposal.limit_price,
            simulated=True,
        )


class LiveBroker:
    """
    Placeholder for real order placement via the Robinhood MCP.

    Deliberately raises. Wiring this to place_equity_order is a separate,
    explicit task — not something this skeleton does on its own.
    """

    def execute(self, proposal: TradeProposal) -> Fill:
        raise NotImplementedError(
            "Live trading is not wired up. This is intentional. Real order "
            "placement must be added deliberately, behind the governor and the "
            "human approval gate."
        )
