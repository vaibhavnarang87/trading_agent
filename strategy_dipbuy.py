"""
Dip-Buy-Quality strategy — deterministic, long-only.

Thesis: buy a quality name on price weakness, but only when the weakness is
PRICE (not the business deteriorating). The LLM research signal is the filter
that tells the two apart — you don't buy the dip if earnings missed, guidance
was cut, or a fundamental red flag appeared.

Rules (defaults; all tunable):
  - ENTRY: price is >= DIP_PCT below its HIGH_WINDOW-day high
           AND fundamental_ok (from the LLM signal; earnings-beat proxy in tests)
           AND we hold fewer than MAX_POSITIONS names
  - EXIT:  price >= entry*(1+PROFIT_TARGET)  -> take profit
           price <= entry*(1-STOP_LOSS)      -> cut (hard stop)
           OR fundamental_ok flips to False  -> exit on thesis break

Deterministic: same inputs -> same action. Backtestable. The LLM informs the
fundamental_ok flag; it does not decide the trade — this rule does.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    NONE = "none"


@dataclass(frozen=True)
class DipBuyParams:
    dip_pct: float = 0.15          # 15% off recent high triggers a look
    profit_target: float = 0.10    # +10% take-profit
    stop_loss: float = 0.07        # -7% hard stop
    max_positions: int = 4
    high_window: int = 60          # trading days for the "recent high"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def decide(
    price: float,
    recent_high: float,
    fundamental_ok: bool,
    held: bool,
    entry_price: float | None,
    open_positions: int,
    p: DipBuyParams = DipBuyParams(),
) -> Decision:
    """Pure deterministic decision for one symbol on one day."""
    if held:
        # thesis break first — if the business turned, get out regardless of price
        if not fundamental_ok:
            return Decision(Action.SELL, "fundamental gate flipped -> exit")
        if entry_price:
            if price >= entry_price * (1 + p.profit_target):
                return Decision(Action.SELL, f"hit +{p.profit_target:.0%} target")
            if price <= entry_price * (1 - p.stop_loss):
                return Decision(Action.SELL, f"hit -{p.stop_loss:.0%} stop")
        return Decision(Action.HOLD, "in position, no exit trigger")

    # not held -> look for an entry
    if recent_high <= 0:
        return Decision(Action.NONE, "no reference high")
    dip = price / recent_high - 1
    if dip > -p.dip_pct:
        return Decision(Action.NONE, f"only {dip:.1%} off high (need -{p.dip_pct:.0%})")
    if not fundamental_ok:
        return Decision(Action.NONE, "dip present but fundamentals weak -> skip (falling knife)")
    if open_positions >= p.max_positions:
        return Decision(Action.NONE, f"at max {p.max_positions} positions")
    return Decision(Action.BUY, f"{dip:.1%} off high + fundamentals OK -> buy the dip")
