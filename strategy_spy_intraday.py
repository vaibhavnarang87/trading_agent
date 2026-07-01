"""
SPY Intraday Momentum strategy — deterministic, long-only (cash account).

Core rule (Gao et al. 2018, long-only variant): if SPY's first-30-min return is
positive, buy for the last 30 minutes of the session; otherwise stay flat. (No
short leg — cash account can't short.)

Two optional INPUT gates, both deterministic:
  - trend_filter: only take the signal when SPY is in an uptrend (prev close
    above its N-day moving average). Testable on historical data.
  - sentiment_risk_gate: a CONTRARIAN risk gate. When broad retail euphoria is
    extreme, skip the trade. This is NOT a buy signal and CANNOT be backtested
    (no historical Reddit data) — it runs forward in paper only. Per the
    research-layer design, sentiment trims risk; it never adds conviction.

This module makes the DECISION deterministic and explicit. Whether it has an
edge is a separate question the backtest answers — and so far the answer is "no
significant edge," which means it stays in paper.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IntradayContext:
    """Everything the rule needs for one decision, computed upstream."""
    symbol: str
    first30_return: float          # 9:30->10:00 vs prev close
    last_price: float              # current price (for sizing)
    in_uptrend: Optional[bool] = None      # prev close > N-day MA (trend filter)
    retail_euphoria_extreme: bool = False  # live sentiment risk gate (forward only)


@dataclass(frozen=True)
class IntradayDecision:
    go_long: bool
    reason: str


def decide(
    ctx: IntradayContext,
    use_trend_filter: bool = False,
    use_sentiment_gate: bool = False,
) -> IntradayDecision:
    """Pure, deterministic decision. Same inputs -> same output. Backtestable."""
    if ctx.first30_return <= 0:
        return IntradayDecision(False, "first-30-min not positive -> flat")

    if use_trend_filter and ctx.in_uptrend is False:
        return IntradayDecision(False, "trend filter: SPY below MA -> flat")

    if use_sentiment_gate and ctx.retail_euphoria_extreme:
        return IntradayDecision(
            False,
            "sentiment risk gate: extreme retail euphoria -> skip (contrarian)",
        )

    return IntradayDecision(True, f"first-30 +{ctx.first30_return:.2%} -> long last-30")
