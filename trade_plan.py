"""
Trade plan — the executable, PRIVATE side of the daily run.

Takes the screen's BUY candidates and turns each into a concrete, validated,
RISK-GOVERNOR-APPROVED order ticket: the exact thing a broker needs to place,
plus the precise Robinhood MCP params. This is the "executable output."

THE SWITCH
----------
Mode.PAPER (default): tickets are marked paper — informational, nothing real.
Mode.LIVE + live_trading_armed=True: tickets are marked LIVE-ARMED — meaning
they are ready for YOU to place. Arming does NOT place anything. By design, this
module never calls place_equity_order; the final, irreversible placement stays a
human action (see show_tickets.py and poc/execution.py). The switch decides
whether the pipeline PRODUCES a live-ready ticket — not whether a bot pulls the
trigger.

Output is written to data/private/ (git-ignored) so live-armed tickets and any
account context never land in the public repo/site.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date

from .config import AgentConfig, Mode, RiskLimits
from .poc.order import Order, OrderType, Side
from .risk_governor import RiskGovernor
from .screener import Candidate
from .types import AccountState, DailyState, Side as GovSide, TradeProposal

HERE = os.path.dirname(__file__)
PRIVATE_DIR = os.path.join(HERE, "data", "private")


@dataclass
class Ticket:
    symbol: str
    broker_params: dict          # exact kwargs for Robinhood review_/place_equity_order
    describe: str
    reasons: list[str]
    approved: bool               # passed the risk governor
    governor_reason: str
    armed: bool                  # True only when the live switch is on
    status: str                  # "paper" | "live-armed" | "vetoed"


@dataclass
class TradePlan:
    date: str
    mode: str
    armed: bool
    account_value: float
    tickets: list[Ticket] = field(default_factory=list)

    def placeable(self) -> list[Ticket]:
        return [t for t in self.tickets if t.approved]


def _account_from_env(default_value: float = 5000.0) -> AccountState:
    """Account context for sizing/risk checks. Kept deliberately simple and out
    of the broker: set TRADING_ACCOUNT_VALUE / TRADING_ACCOUNT_CASH if you want
    the governor to size against your real numbers. No credentials involved."""
    val = float(os.environ.get("TRADING_ACCOUNT_VALUE", default_value))
    cash = float(os.environ.get("TRADING_ACCOUNT_CASH", val))
    return AccountState(account_value=val, cash=cash, positions={})


def build_plan(
    candidates: list[Candidate],
    config: AgentConfig,
    dollars_per_trade: float = 200.0,
    account: AccountState | None = None,
) -> TradePlan:
    """Turn passing candidates into risk-checked order tickets. Places nothing."""
    account = account or _account_from_env()
    governor = RiskGovernor(config.limits)
    daily = DailyState(day=date.today())
    armed = config.live_enabled()

    tickets: list[Ticket] = []
    for c in candidates:
        if not c.passed or c.price is None:
            continue

        # A market dollar order is the simplest executable ticket for a cash buy.
        order = Order(
            account_number=config.account_number,
            symbol=c.symbol,
            side=Side.BUY,
            type=OrderType.MARKET,
            dollar_amount=round(dollars_per_trade, 2),
        )
        errs = order.validate()
        if errs:
            tickets.append(Ticket(c.symbol, order.to_broker_params(), order.describe(),
                                  c.reasons, False, f"invalid: {errs}", False, "vetoed"))
            continue

        # Risk governor decides against real sizing. qty derived from price.
        qty = dollars_per_trade / c.price
        proposal = TradeProposal(c.symbol, GovSide.BUY, qty, c.price, "; ".join(c.reasons))
        decision = governor.evaluate(proposal, account, daily)

        if not decision.approved:
            status = "vetoed"
        elif armed:
            status = "live-armed"
        else:
            status = "paper"

        tickets.append(Ticket(
            symbol=c.symbol,
            broker_params=order.to_broker_params(),
            describe=order.describe(),
            reasons=c.reasons,
            approved=decision.approved,
            governor_reason=decision.reason,
            armed=armed and decision.approved,
            status=status,
        ))

    return TradePlan(
        date=date.today().isoformat(),
        mode=config.mode.value,
        armed=armed,
        account_value=account.account_value,
        tickets=tickets,
    )


def write_plan(plan: TradePlan) -> str:
    os.makedirs(PRIVATE_DIR, exist_ok=True)
    path = os.path.join(PRIVATE_DIR, f"trade_plan_{plan.date}.json")
    with open(path, "w") as f:
        json.dump({**asdict(plan)}, f, indent=2)
    return path


def render_plan(plan: TradePlan) -> str:
    """Human-readable ticket sheet. PRIVATE — not for the public site."""
    L = []
    switch = "LIVE-ARMED ⚠" if plan.armed else "PAPER (safe)"
    L.append("=" * 60)
    L.append(f"TRADE PLAN — {plan.date}   [switch: {switch}]")
    L.append("=" * 60)
    if not plan.tickets:
        L.append("No tickets today (nothing passed the screen).")
        return "\n".join(L)
    for t in plan.tickets:
        mark = {"live-armed": "▶ READY", "paper": "· paper", "vetoed": "✗ vetoed"}[t.status]
        L.append(f"{mark}  {t.describe}")
        L.append(f"         why: {'; '.join(t.reasons)}")
        L.append(f"         governor: {t.governor_reason}")
        if t.status == "live-armed":
            L.append(f"         params: {t.broker_params}")
    L.append("-" * 60)
    if plan.armed:
        L.append("Tickets marked ▶ READY are risk-approved and live-armed. To place")
        L.append("one, YOU run it in your own Robinhood (see: python -m")
        L.append("trading_agent.show_tickets). This tool never places orders.")
    else:
        L.append("PAPER mode: nothing here is live. Flip the switch (Mode.LIVE +")
        L.append("live_trading_armed) only after a strategy earns it in forward tests.")
    return "\n".join(L)
