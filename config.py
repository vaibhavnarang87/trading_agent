"""
Configuration for the trading agent.

Everything safety-relevant lives here as plain data so it can be reviewed,
version-controlled, and audited. No strategy logic in this file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    PAPER = "paper"   # simulated fills, no real money. DEFAULT.
    LIVE = "live"     # real broker. Must be enabled manually + intentionally.


@dataclass(frozen=True)
class RiskLimits:
    """
    Hard limits enforced by the risk governor. These are deterministic ceilings,
    not suggestions. The strategy cannot override them.

    All percentages are fractions of current account value (0.10 == 10%).
    """
    # Per-order sizing
    max_order_value: float = 250.0           # absolute $ cap on any single order
    max_position_pct: float = 0.10           # max % of account in one order
    max_position_per_symbol_pct: float = 0.20  # max % of account held in one symbol

    # Activity ceilings
    max_trades_per_day: int = 5

    # Loss controls (absolute $ — realized P&L for the day)
    max_daily_loss: float = 150.0            # halts trading for the day if hit

    # Cash-account guard: orders may not exceed settled cash (no margin).
    enforce_cash_only: bool = True


@dataclass
class AgentConfig:
    account_number: str
    mode: Mode = Mode.PAPER                  # PAPER is the default, on purpose
    limits: RiskLimits = field(default_factory=RiskLimits)

    # Live trading requires BOTH this flag AND mode==LIVE. Belt and suspenders.
    live_trading_armed: bool = False

    # Human approval gate. When True, every proposal that passes the governor
    # still requires an explicit yes before it reaches the broker.
    require_human_approval: bool = True

    def live_enabled(self) -> bool:
        return self.mode == Mode.LIVE and self.live_trading_armed
