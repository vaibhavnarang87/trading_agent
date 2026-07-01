"""
Market-research component.

Assembles structured, factual market data for a ticker from the Robinhood MCP
tools. Pure information — no recommendations.

Design note: the MCP tool calls are injected as callables (fundamentals_fn,
earnings_fn) so this module is testable offline and has no hidden dependency on
a live connection. At runtime you pass in the real MCP tool functions; in tests
or demos you pass fixtures built from real data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class EarningsPoint:
    date: str
    est: float
    act: Optional[float]

    @property
    def surprise(self) -> Optional[float]:
        if self.act is None or self.est == 0:
            return None
        return (self.act - self.est) / abs(self.est)


@dataclass
class MarketSnapshot:
    symbol: str
    price: Optional[float] = None
    pe: Optional[float] = None
    market_cap: Optional[float] = None
    low_52w: Optional[float] = None
    high_52w: Optional[float] = None
    earnings: list[EarningsPoint] = field(default_factory=list)

    @property
    def range_position(self) -> Optional[float]:
        """0.0 = at 52-week low, 1.0 = at 52-week high."""
        if None in (self.price, self.low_52w, self.high_52w):
            return None
        span = self.high_52w - self.low_52w
        return (self.price - self.low_52w) / span if span else None

    @property
    def next_earnings(self) -> Optional[str]:
        for e in self.earnings:
            if e.act is None:
                return e.date
        return None

    @property
    def beat_rate(self) -> Optional[float]:
        reported = [e for e in self.earnings if e.act is not None]
        if not reported:
            return None
        beats = [e for e in reported if e.surprise and e.surprise > 0]
        return len(beats) / len(reported)


def build_snapshot(
    symbol: str,
    fundamentals_fn: Callable[[list[str]], dict],
    earnings_fn: Callable[[str], dict],
) -> MarketSnapshot:
    """Pull fundamentals + earnings via injected MCP tool callables."""
    snap = MarketSnapshot(symbol=symbol)

    try:
        f = fundamentals_fn([symbol])
        row = f["data"][0] if isinstance(f.get("data"), list) else f["data"]
        snap.price = _f(row.get("last_trade_price"))
        snap.pe = _f(row.get("pe_ratio"))
        snap.market_cap = _f(row.get("market_cap"))
        snap.low_52w = _f(row.get("low_52_weeks"))
        snap.high_52w = _f(row.get("high_52_weeks"))
    except Exception:
        pass  # leave fields None; the card renders what it has

    try:
        e = earnings_fn(symbol)
        for r in e["data"]["results"]:
            snap.earnings.append(
                EarningsPoint(
                    date=r["report"]["date"],
                    est=_f(r["eps"]["estimate"]) or 0.0,
                    act=_f(r["eps"]["actual"]),
                )
            )
    except Exception:
        pass

    return snap


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None
