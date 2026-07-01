"""
The structured signal the LLM emits — and the line that keeps this safe.

The LLM's job is to read messy research (news, earnings, filings) and output
STRUCTURED FACTS AND SENTIMENT. That's it. Notice what this schema does NOT
contain: no buy/sell/hold field, no price target, no position size, no
"recommendation". By construction, the model cannot hand you a trade decision —
it can only describe what it read in a structured way.

Your deterministic rules consume this object and make the actual call. That
split is the whole design: the LLM analyzes, your code decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ResearchSignal:
    symbol: str

    # Factual extractions from the research
    earnings_beat: Optional[bool] = None          # did the last report beat estimates?
    guidance_direction: Optional[str] = None      # 'raised'|'lowered'|'inline'|'unknown'

    # Sentiment (descriptive, not directive)
    news_sentiment: str = "neutral"               # positive|negative|neutral|mixed
    sentiment_score: float = 0.0                  # -1.0 .. +1.0

    # Structured context for YOUR rules to weigh
    key_themes: list[str] = field(default_factory=list)     # ['AI demand', 'new GPU']
    catalysts: list[str] = field(default_factory=list)      # upcoming dated events
    risk_flags: list[str] = field(default_factory=list)     # ['valuation stretched']

    summary: str = ""                             # 1-2 sentence neutral synthesis

    # Provenance so you can audit what the analysis was based on
    sources: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errs = []
        if self.news_sentiment not in {"positive", "negative", "neutral", "mixed"}:
            errs.append(f"bad news_sentiment: {self.news_sentiment}")
        if not (-1.0 <= self.sentiment_score <= 1.0):
            errs.append(f"sentiment_score out of range: {self.sentiment_score}")
        if self.guidance_direction not in {None, "raised", "lowered", "inline", "unknown"}:
            errs.append(f"bad guidance_direction: {self.guidance_direction}")
        return errs

    def render(self) -> str:
        L = [f"SIGNAL: {self.symbol}  (analysis only — not a recommendation)"]
        if self.earnings_beat is not None:
            L.append(f"  earnings beat: {self.earnings_beat}")
        if self.guidance_direction:
            L.append(f"  guidance: {self.guidance_direction}")
        L.append(f"  sentiment: {self.news_sentiment} ({self.sentiment_score:+.2f})")
        if self.key_themes:
            L.append(f"  themes: {', '.join(self.key_themes)}")
        if self.catalysts:
            L.append(f"  catalysts: {', '.join(self.catalysts)}")
        if self.risk_flags:
            L.append(f"  risk flags: {', '.join(self.risk_flags)}")
        if self.summary:
            L.append(f"  summary: {self.summary}")
        return "\n".join(L)
