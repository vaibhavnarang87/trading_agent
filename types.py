"""
Core domain types shared across the agent.

Plain dataclasses, no behavior beyond simple derived values. Keeping these
small and explicit makes the whole system replayable and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class TradeProposal:
    """A trade the strategy WANTS to make. Not yet approved, not yet executed."""
    symbol: str
    side: Side
    quantity: float
    limit_price: float        # the price the strategy expects / will cap at
    reason: str               # human-readable justification (for the audit log)

    @property
    def notional(self) -> float:
        return self.quantity * self.limit_price


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    average_cost: float


@dataclass
class AccountState:
    """Snapshot of the account at decision time."""
    account_value: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)

    def position_qty(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.quantity if p else 0.0

    def position_value(self, symbol: str, price: float) -> float:
        return self.position_qty(symbol) * price


@dataclass
class DailyState:
    """Per-day counters the governor reads. Reset at the start of each session day."""
    day: date
    trades_today: int = 0
    realized_pnl_today: float = 0.0
    kill_switch_engaged: bool = False

    def reset_for(self, d: date) -> None:
        self.day = d
        self.trades_today = 0
        self.realized_pnl_today = 0.0
        self.kill_switch_engaged = False


@dataclass(frozen=True)
class GovernorDecision:
    approved: bool
    reason: str
