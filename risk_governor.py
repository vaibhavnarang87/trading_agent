"""
The risk governor.

This is the safety core: deterministic, plain code, no model in the loop.
It sits ABOVE the strategy. A proposal must pass every check here before it can
reach the human gate or the broker. The strategy cannot argue with it or
override it — there is no path around these functions.

Each check returns a GovernorDecision with a precise reason, so every veto is
explainable and logged.
"""
from __future__ import annotations

from .config import RiskLimits
from .types import (
    AccountState,
    DailyState,
    GovernorDecision,
    Side,
    TradeProposal,
)


class RiskGovernor:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def engage_kill_switch(self, daily: DailyState, why: str) -> None:
        daily.kill_switch_engaged = True

    def evaluate(
        self,
        proposal: TradeProposal,
        account: AccountState,
        daily: DailyState,
    ) -> GovernorDecision:
        L = self.limits

        # 0. Global kill switch. Once engaged, nothing trades for the rest of the day.
        if daily.kill_switch_engaged:
            return GovernorDecision(False, "Kill switch engaged: trading halted for the day.")

        # 1. Daily loss limit. If already breached, halt AND latch the kill switch.
        if daily.realized_pnl_today <= -abs(L.max_daily_loss):
            self.engage_kill_switch(daily, "max_daily_loss breached")
            return GovernorDecision(
                False,
                f"Max daily loss hit (realized {daily.realized_pnl_today:.2f} "
                f"<= -{L.max_daily_loss:.2f}). Kill switch latched.",
            )

        # 2. Trade-count ceiling for the day.
        if daily.trades_today >= L.max_trades_per_day:
            return GovernorDecision(
                False,
                f"Max trades/day reached ({daily.trades_today}/{L.max_trades_per_day}).",
            )

        # 3. Basic sanity on the proposal itself.
        if proposal.quantity <= 0:
            return GovernorDecision(False, "Quantity must be positive.")
        if proposal.limit_price <= 0:
            return GovernorDecision(False, "Limit price must be positive.")

        # Sizing caps below constrain ENTRIES only. Exiting a position (a sell)
        # is never blocked by a notional/pct cap — you must always be able to
        # cut a loser or take profit. Sells are checked separately.
        if proposal.side == Side.SELL:
            return self._check_sell(proposal, account)

        notional = proposal.notional

        # 4. Absolute per-order dollar cap (buys only).
        if notional > L.max_order_value:
            return GovernorDecision(
                False,
                f"Order notional ${notional:.2f} exceeds max_order_value "
                f"${L.max_order_value:.2f}.",
            )

        # 5. Per-order cap as % of account value (buys only).
        max_order_by_pct = L.max_position_pct * account.account_value
        if notional > max_order_by_pct:
            return GovernorDecision(
                False,
                f"Order notional ${notional:.2f} exceeds "
                f"{L.max_position_pct:.0%} of account (${max_order_by_pct:.2f}).",
            )

        return self._check_buy(proposal, account, notional)

    def _check_buy(
        self, proposal: TradeProposal, account: AccountState, notional: float
    ) -> GovernorDecision:
        L = self.limits

        # 6. Cash-only guard: a buy cannot exceed available cash (no margin).
        if L.enforce_cash_only and notional > account.cash:
            return GovernorDecision(
                False,
                f"Buy needs ${notional:.2f} but only ${account.cash:.2f} cash "
                f"available (cash-only, no margin).",
            )

        # 7. Per-symbol concentration cap (existing holding + this order).
        existing_value = account.position_value(proposal.symbol, proposal.limit_price)
        resulting_value = existing_value + notional
        max_symbol_value = L.max_position_per_symbol_pct * account.account_value
        if resulting_value > max_symbol_value:
            return GovernorDecision(
                False,
                f"{proposal.symbol} position would reach ${resulting_value:.2f}, "
                f"over the {L.max_position_per_symbol_pct:.0%} per-symbol cap "
                f"(${max_symbol_value:.2f}).",
            )

        return GovernorDecision(True, "OK")

    def _check_sell(
        self, proposal: TradeProposal, account: AccountState
    ) -> GovernorDecision:
        # 8. Cannot sell more than held (no shorting on a cash account).
        held = account.position_qty(proposal.symbol)
        if proposal.quantity > held + 1e-9:
            return GovernorDecision(
                False,
                f"Cannot sell {proposal.quantity} {proposal.symbol}; only "
                f"{held} held (no shorting).",
            )
        return GovernorDecision(True, "OK")
