"""
Autonomous runner — the "full autonomy" loop, for a DETERMINISTIC strategy.

What this proves: a bot CAN run unattended — pull data, evaluate the strategy,
clear the risk governor, place orders, log — with no per-trade human click. That
is genuine autonomy.

The non-negotiable that makes it safe: the thing being evaluated each cycle is a
deterministic Strategy object whose rules YOU authored and backtested. The loop
executes your pre-committed decisions on schedule. There is no LLM in this loop
inventing trades — by design. (That's the one piece that stays human-authored.)

Two safety facts baked in:
  - mode defaults to PAPER. Live requires manually arming config.live_trading_armed
    AND implementing LiveBroker (a deliberate stub that raises). This build does
    NOT wire real-money placement into an unattended loop.
  - the RiskGovernor runs every cycle and can veto/halt regardless of strategy.

require_human_approval is set False here on purpose: for a deterministic,
pre-committed, backtested strategy, a per-trade click adds nothing (the decision
was made when you wrote and tested the rule). That is exactly why this autonomy
is acceptable — and exactly why it would NOT be acceptable if the decider were a
model improvising each cycle.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Callable

from .audit import AuditLog
from .config import AgentConfig, Mode
from .executor import Executor
from .risk_governor import RiskGovernor
from .strategy import Strategy
from .types import AccountState, DailyState


# A data feed returns {symbol: price} for this cycle. At runtime you inject one
# backed by the Robinhood MCP get_equity_quotes tool. For paper/demo, inject a
# simulated feed.
PriceFeed = Callable[[], dict[str, float]]
AccountFeed = Callable[[], AccountState]


def auto_execute(_proposal) -> bool:
    """
    Autonomy gate: approve automatically. This is ONLY sound because the
    proposals come from a deterministic, pre-authored, backtested strategy —
    not from a model deciding in the moment.
    """
    return True


class AutonomousRunner:
    def __init__(
        self,
        config: AgentConfig,
        strategy: Strategy,
        price_feed: PriceFeed,
        account_feed: AccountFeed,
        audit: AuditLog | None = None,
    ):
        self.config = config
        self.strategy = strategy
        self.price_feed = price_feed
        self.account_feed = account_feed
        self.audit = audit or AuditLog()
        self.governor = RiskGovernor(config.limits)
        # autonomy => no per-trade human gate, sound only for deterministic rules
        self.executor = Executor(
            config, self.governor, self.audit, approval_fn=auto_execute
        )
        self.daily = DailyState(day=date.today())

    def _roll_day(self):
        today = date.today()
        if self.daily.day != today:
            self.daily.reset_for(today)

    def run_cycle(self) -> None:
        """One autonomous decision cycle."""
        self._roll_day()
        prices = self.price_feed()
        account = self.account_feed()
        self.audit.record("cycle", prices=prices, kill=self.daily.kill_switch_engaged)
        for proposal in self.strategy.propose(prices, account):
            self.executor.handle(proposal, account, self.daily)

    def run_forever(self, interval_seconds: int, max_cycles: int | None = None):
        """Scheduled loop. Paper mode unless live is manually armed."""
        if self.config.live_enabled():
            raise RuntimeError(
                "Refusing to run live unattended from this build. LiveBroker is "
                "a stub; wiring real-money placement into an autonomous loop must "
                "be a deliberate, separate, reviewed step after a strategy proves "
                "out in paper + backtest."
            )
        n = 0
        while max_cycles is None or n < max_cycles:
            self.run_cycle()
            n += 1
            if max_cycles is None or n < max_cycles:
                time.sleep(interval_seconds)


# ---- paper demo: full autonomy, simulated feed, deterministic strategy ----
if __name__ == "__main__":
    from .config import RiskLimits
    from .strategy import ExampleThresholdStrategy
    from .types import AccountState

    cfg = AgentConfig(
        account_number="PAPER-ACCOUNT",
        mode=Mode.PAPER,
        limits=RiskLimits(),
        require_human_approval=False,   # autonomous
        live_trading_armed=False,       # paper only
    )

    cash = {"v": 5000.0}
    positions: dict = {}

    def account_feed() -> AccountState:
        return AccountState(account_value=5000.0, cash=cash["v"], positions=dict(positions))

    ticks = iter([{"AAPL": 195.0}, {"AAPL": 198.0}, {"AAPL": 235.0}, {"AAPL": 240.0}])

    def price_feed() -> dict:
        try:
            return next(ticks)
        except StopIteration:
            return {"AAPL": 240.0}

    runner = AutonomousRunner(
        cfg, ExampleThresholdStrategy("AAPL", 200.0, 230.0, 150.0),
        price_feed, account_feed,
    )
    print("--- AUTONOMOUS RUNNER (paper, no human gate, deterministic strategy) ---")
    runner.run_forever(interval_seconds=0, max_cycles=4)
    print("--- done: bot ran 4 cycles unattended, governor active throughout ---")
