"""
Runnable paper-mode demo.

Shows the full path end-to-end with simulated prices and a fake price feed.
No real money, no MCP calls. Run: python -m trading_agent.main
"""
from __future__ import annotations

from datetime import date

from .audit import AuditLog
from .config import AgentConfig, Mode, RiskLimits
from .executor import Executor
from .risk_governor import RiskGovernor
from .strategy import ExampleThresholdStrategy
from .types import AccountState, DailyState


def always_approve(proposal) -> bool:
    """Stand-in for the human gate in this demo. In real use, this is YOU."""
    return True


def main() -> None:
    config = AgentConfig(
        account_number="PAPER-ACCOUNT",  # placeholder; set your own out of band
        mode=Mode.PAPER,                  # default; nothing real happens
        limits=RiskLimits(
            max_order_value=250.0,
            max_position_pct=0.10,
            max_position_per_symbol_pct=0.20,
            max_trades_per_day=5,
            max_daily_loss=150.0,
            enforce_cash_only=True,
        ),
        require_human_approval=True,
    )

    audit = AuditLog()
    governor = RiskGovernor(config.limits)
    executor = Executor(config, governor, audit, approval_fn=always_approve)

    # Placeholder strategy — replace with your Section 8 rules.
    strategy = ExampleThresholdStrategy(
        symbol="AAPL", buy_below=200.0, sell_above=230.0, dollars_per_trade=150.0
    )

    account = AccountState(account_value=5000.0, cash=5000.0, positions={})
    daily = DailyState(day=date.today())

    # Simulated price ticks. In a real run these come from get_equity_quotes.
    price_ticks = [195.0, 198.0, 235.0, 240.0]

    print(f"--- PAPER MODE demo | live_enabled={config.live_enabled()} ---")
    for i, px in enumerate(price_ticks):
        prices = {"AAPL": px}
        for proposal in strategy.propose(prices, account):
            fill = executor.handle(proposal, account, daily)
            if fill:
                # Update the simulated account so later ticks see the position.
                if fill.side.value == "buy":
                    account.cash -= fill.quantity * fill.price
                    held = account.positions.get("AAPL")
                    new_qty = (held.quantity if held else 0) + fill.quantity
                    from .types import Position
                    account.positions["AAPL"] = Position("AAPL", new_qty, fill.price)
                else:
                    account.cash += fill.quantity * fill.price
                    account.positions.pop("AAPL", None)

    print(f"--- end | cash={account.cash:.2f} positions={list(account.positions)} "
          f"trades_today={daily.trades_today} ---")


if __name__ == "__main__":
    main()
