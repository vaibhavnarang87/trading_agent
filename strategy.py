"""
Strategy interface.

THIS is where YOUR rules live (your Section 8 homework). A strategy looks at
market data + account state and returns zero or more TradeProposals with
reasons. It does not execute anything — proposals flow to the governor, then
the human gate, then the broker.

Deliberately deterministic: given the same inputs, a strategy returns the same
proposals. That is what makes it backtestable and auditable. (It is also why
the analytical brain here is rules you author, not an LLM improvising trades on
a live account.)

Replace ExampleThresholdStrategy with your own rules.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .types import AccountState, Side, TradeProposal


class Strategy(ABC):
    @abstractmethod
    def propose(
        self,
        prices: dict[str, float],
        account: AccountState,
    ) -> list[TradeProposal]:
        """Return trade proposals given current prices and account state."""
        raise NotImplementedError


class ExampleThresholdStrategy(Strategy):
    """
    A PLACEHOLDER, not a recommendation. It exists only to show the interface
    and let the paper loop run end-to-end. It buys a fixed dollar amount of a
    symbol when its price dips below a reference, and sells when it rises above
    a target. Replace entirely with your own entry/exit/stop rules.
    """

    def __init__(
        self,
        symbol: str,
        buy_below: float,
        sell_above: float,
        dollars_per_trade: float,
    ):
        self.symbol = symbol
        self.buy_below = buy_below
        self.sell_above = sell_above
        self.dollars_per_trade = dollars_per_trade

    def propose(
        self, prices: dict[str, float], account: AccountState
    ) -> list[TradeProposal]:
        price = prices.get(self.symbol)
        if price is None:
            return []

        held = account.position_qty(self.symbol)

        if price < self.buy_below and account.cash >= self.dollars_per_trade:
            qty = round(self.dollars_per_trade / price, 6)
            return [
                TradeProposal(
                    symbol=self.symbol,
                    side=Side.BUY,
                    quantity=qty,
                    limit_price=price,
                    reason=f"price {price:.2f} < buy_below {self.buy_below:.2f}",
                )
            ]

        if price > self.sell_above and held > 0:
            return [
                TradeProposal(
                    symbol=self.symbol,
                    side=Side.SELL,
                    quantity=held,
                    limit_price=price,
                    reason=f"price {price:.2f} > sell_above {self.sell_above:.2f}",
                )
            ]

        return []
