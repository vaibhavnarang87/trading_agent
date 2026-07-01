"""
Social-sentiment component (Reddit / WallStreetBets).

Pulls public WSB mention-volume and sentiment from aggregators (ApeWisdom,
SwaggyStocks, YOLOStocks, QuiverQuant). At runtime, fetch_fn performs the web
fetch and returns parsed rows; here we define the data model and — critically —
the INTERPRETATION layer.

Interpretation rule (deliberate, evidence-based): a sharp rise in retail
attention is treated as a RISK / CROWDING flag, not a buy signal. See
research_layer_note.md and Barber & Odean (2008). This module will never output
"buy"; it outputs a crowd-state read for the human to weigh.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SocialSnapshot:
    symbol: str
    mentions_24h: int
    mentions_prev_24h: int
    sentiment: str  # 'positive' | 'neutral' | 'negative' (aggregator-reported)
    rank: Optional[int] = None

    @property
    def mention_change(self) -> Optional[float]:
        if self.mentions_prev_24h == 0:
            return None
        return self.mentions_24h / self.mentions_prev_24h - 1

    def crowd_flag(self) -> str:
        """
        Translate attention into a RISK read, never a buy signal.
        """
        chg = self.mention_change
        if chg is None:
            level = "unknown"
        elif chg > 1.0:        # mentions more than doubled
            level = "SPIKING — crowded/late-stage attention, elevated risk"
        elif chg > 0.3:
            level = "rising — watch for crowding"
        elif chg < -0.3:
            level = "fading — attention cooling"
        else:
            level = "steady"
        return level

    def caution_note(self) -> str:
        chg = self.mention_change
        if chg is not None and chg > 1.0 and self.sentiment == "positive":
            return ("Strong bullish hype + a mention spike is the classic "
                    "buy-the-top setup. Higher risk, not a green light.")
        if self.sentiment == "negative":
            return "Crowd is bearish — informative, but the crowd is often late both ways."
        return "Attention data is context, not a signal. Decide on fundamentals."


def market_euphoria_extreme(rows: list[dict], spike_ratio: float = 2.0) -> bool:
    """
    FORWARD-ONLY contrarian risk gate (cannot be backtested — no historical
    Reddit data). Returns True when broad retail attention looks euphoric:
    aggregate WSB mentions spiked vs. the prior day AND tone skews bullish.

    Used by the SPY strategy's sentiment_gate to SKIP trades during euphoria.
    This trims risk; it never adds conviction and never triggers a buy.
    """
    snaps = parse_apewisdom(rows)
    if not snaps:
        return False
    total_now = sum(s.mentions_24h for s in snaps)
    total_prev = sum(s.mentions_prev_24h for s in snaps)
    bullish = sum(1 for s in snaps if s.sentiment == "positive")
    if total_prev == 0:
        return False
    spiking = total_now / total_prev >= spike_ratio
    mostly_bullish = bullish / len(snaps) > 0.6
    return spiking and mostly_bullish


def parse_apewisdom(rows: list[dict]) -> list[SocialSnapshot]:
    """Parse ApeWisdom-style rows into SocialSnapshots."""
    out = []
    sent_map = {"1": "negative", "2": "neutral", "3": "positive"}
    for r in rows:
        sval = r.get("sentiment")
        sentiment = (
            sent_map.get(str(sval))
            if str(sval) in sent_map
            else (sval if isinstance(sval, str) else "neutral")
        )
        out.append(
            SocialSnapshot(
                symbol=r.get("ticker", "").upper(),
                mentions_24h=int(r.get("mentions", 0) or 0),
                mentions_prev_24h=int(r.get("mentions_24h_ago", 0) or 0),
                sentiment=sentiment or "neutral",
                rank=int(r["rank"]) if r.get("rank") else None,
            )
        )
    return out
