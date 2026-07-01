"""
POC: the approval flow — the spine of the bot.

    build Order -> validate -> RISK GOVERNOR -> live REVIEW preview
                -> human APPROVAL -> executor.place()  (your code)

Every stage can stop the order. The governor is deterministic and can veto.
The review shows real numbers. Approval is an explicit yes. Only after all
three does it reach place() — which is the stub you implement.
"""
from __future__ import annotations

from typing import Callable

from ..audit import AuditLog
from ..config import RiskLimits
from ..risk_governor import RiskGovernor
from ..types import AccountState, DailyState, Side as GovSide, TradeProposal
from .execution import OrderExecutor
from .order import Order
from .review import Preview, ReviewFn, review_order

# approval_fn(preview) -> True to place, False to skip. In a UI this is a button.
ApprovalFn = Callable[[Preview], bool]


def _to_proposal(order: Order, ref_price: float) -> TradeProposal:
    qty = (order.quantity if order.quantity is not None
           else (order.dollar_amount or 0) / ref_price)
    return TradeProposal(
        symbol=order.symbol,
        side=GovSide.BUY if order.side.value == "buy" else GovSide.SELL,
        quantity=qty,
        limit_price=order.limit_price or ref_price,
        reason="poc",
    )


class ApprovalFlow:
    def __init__(
        self,
        limits: RiskLimits,
        review_fn: ReviewFn,
        approval_fn: ApprovalFn,
        executor: OrderExecutor,
        audit: AuditLog | None = None,
    ):
        self.governor = RiskGovernor(limits)
        self.review_fn = review_fn
        self.approval_fn = approval_fn
        self.executor = executor
        self.audit = audit or AuditLog()

    def submit(self, order: Order, account: AccountState, daily: DailyState) -> dict:
        errs = order.validate()
        if errs:
            self.audit.record("rejected_validation", order=order.describe(), errors=errs)
            return {"status": "rejected", "stage": "validation", "errors": errs}

        # 1) live review preview (no order placed)
        preview = review_order(order, self.review_fn)
        ref_price = preview.last_price or order.limit_price or 0.0
        self.audit.record("review", preview=preview.render())

        # 2) deterministic risk governor
        proposal = _to_proposal(order, ref_price or 1.0)
        decision = self.governor.evaluate(proposal, account, daily)
        self.audit.record("governor", approved=decision.approved, reason=decision.reason)
        if not decision.approved:
            return {"status": "rejected", "stage": "governor",
                    "reason": decision.reason, "preview": preview}

        # 3) human approval gate
        approved = self.approval_fn(preview)
        self.audit.record("approval", approved=approved)
        if not approved:
            return {"status": "declined", "stage": "approval", "preview": preview}

        # 4) execution — YOUR place() implementation
        result = self.executor.place(order)
        daily.trades_today += 1
        self.audit.record("placed", result=result)
        return {"status": "placed", "result": result, "preview": preview}
