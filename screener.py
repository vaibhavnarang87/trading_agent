"""
Deterministic candidate screen.

Turns the daily StockCards (price/range, earnings beat rate, sentiment) into a
ranked list of *candidates* with the reasoning shown. This is a rule-based scan,
not an LLM picking trades and not personalized advice — it flags names that meet
explicit, backtestable criteria so YOU have a shortlist to review.

The screen here is a "quality-dip" filter, consistent with the project's dip-buy
theme: a name is flagged BUY-CANDIDATE when it is beaten down in its own range
but has a solid earnings track record and the crowd is NOT euphoric on it
(euphoria is treated as a risk flag, per the briefing's own rule).

Edit THRESHOLDS to change the screen. Every criterion is transparent and every
flagged name carries the exact reasons it passed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .daily_briefing import StockCard


@dataclass(frozen=True)
class ScreenThresholds:
    max_range_pos: float = 0.30       # beaten down: in the bottom 30% of its range
    min_beat_rate: float = 0.75       # quality: beats estimates >= 75% of the time
    block_if_euphoric: bool = True    # crowd spike => skip (risk flag, not a buy)


@dataclass
class Candidate:
    symbol: str
    price: float | None
    passed: bool
    reasons: list[str] = field(default_factory=list)   # why it passed
    blocks: list[str] = field(default_factory=list)     # why it did not

    def summary(self) -> str:
        if self.passed:
            return f"{self.symbol}: CANDIDATE — " + "; ".join(self.reasons)
        return f"{self.symbol}: no — " + "; ".join(self.blocks or ["criteria not met"])


def _is_euphoric(card: StockCard) -> bool:
    if not card.social:
        return False
    flag = card.social.crowd_flag()
    return "SPIKING" in flag or "euphoria" in flag.lower()


def screen_card(card: StockCard, t: ScreenThresholds) -> Candidate:
    """Apply the deterministic screen to one card. Pure function, no I/O."""
    reasons: list[str] = []
    blocks: list[str] = []

    if card.price is None or card.range_pos is None:
        blocks.append("no price/range data")
        return Candidate(card.symbol, card.price, False, reasons, blocks)

    if card.range_pos <= t.max_range_pos:
        reasons.append(f"beaten down ({card.range_pos:.0%} of 52w range "
                       f"<= {t.max_range_pos:.0%})")
    else:
        blocks.append(f"not in lower range ({card.range_pos:.0%} > {t.max_range_pos:.0%})")

    if card.beat_rate is not None and card.beat_rate >= t.min_beat_rate:
        reasons.append(f"quality earnings (beat rate {card.beat_rate:.0%} "
                       f">= {t.min_beat_rate:.0%})")
    elif card.beat_rate is None:
        blocks.append("no earnings history")
    else:
        blocks.append(f"weak beat rate ({card.beat_rate:.0%} < {t.min_beat_rate:.0%})")

    if t.block_if_euphoric and _is_euphoric(card):
        blocks.append("crowd euphoria (risk flag)")

    passed = not blocks
    return Candidate(card.symbol, card.price, passed, reasons, blocks)


def run_screen(cards: list[StockCard],
               thresholds: ScreenThresholds | None = None) -> list[Candidate]:
    """Screen every card; return candidates (passes first, then the rest)."""
    t = thresholds or ScreenThresholds()
    out = [screen_card(c, t) for c in cards]
    out.sort(key=lambda c: (not c.passed, c.symbol))
    return out


def render_screen_section(candidates: list[Candidate]) -> str:
    """Public-safe research block for the briefing. Information, not a buy call."""
    passes = [c for c in candidates if c.passed]
    L = []
    L.append("SCREEN — quality-dip candidates flagged for review (research, not advice):")
    L.append("-" * 60)
    if not passes:
        L.append("  No names passed the screen today.")
    else:
        for c in passes:
            price = f"${c.price:,.2f}" if c.price else "n/a"
            L.append(f"{c.symbol:<6} {price:<11} {'; '.join(c.reasons)}")
    L.append("-" * 60)
    L.append("A screen hit is a prompt to do your own research, not a recommendation")
    L.append("to buy. Criteria are fixed and shown above; you decide and place trades.")
    return "\n".join(L)
