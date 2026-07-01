"""
Executor.

Wires the pieces together in the one safe order:

    strategy proposal -> risk governor -> human approval gate -> broker -> audit

Every stage can stop a trade. The broker is only ever reached by a proposal that
passed BOTH the governor and (when required) the human gate.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from .audit import AuditLog
from .broker import Fill, LiveBroker, PaperBroker
from .config import AgentConfig, Mode
from .risk_governor import RiskGovernor
from .types import AccountState, DailyState, Side, TradeProposal

# An approval function takes a proposal and returns True to allow it.
ApprovalFn = Callable[[TradeProposal], bool]


def auto_decline(_: TradeProposal) -> bool:
    """Default gate: deny everything. Safe default; override explicitly."""
    return False


class Executor:
    def __init__(
        self,
        config: AgentConfig,
        governor: RiskGovernor,
        audit: AuditLog,
        approval_fn: Optional[ApprovalFn] = None,
    ):
        self.config = config
        self.governor = governor
        self.audit = audit
        self.approval_fn = approval_fn or auto_decline
        self.paper = PaperBroker()
        self.live = LiveBroker()

    def _broker(self):
        if self.config.live_enabled():
            return self.live
        return self.paper

    def handle(
        self,
        proposal: TradeProposal,
        account: AccountState,
        daily: DailyState,
    ) -> Optional[Fill]:
        self.audit.record("proposal", proposal=proposal, reason=proposal.reason)

        # Stage 1: deterministic risk governor.
        decision = self.governor.evaluate(proposal, account, daily)
        self.audit.record(
            "governor_decision", approved=decision.approved, reason=decision.reason
        )
        if not decision.approved:
            return None

        # Stage 2: human approval gate (skippable only if explicitly configured).
        if self.config.require_human_approval:
            approved = self.approval_fn(proposal)
            self.audit.record("human_gate", approved=approved)
            if not approved:
                return None

        # Stage 3: broker (paper unless live is fully armed).
        broker = self._broker()
        fill = broker.execute(proposal)
        daily.trades_today += 1
        self.audit.record("fill", fill=fill, mode=self.config.mode)
        return fill
