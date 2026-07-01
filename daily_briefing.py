"""
Daily Briefing Bot — the most autonomous honest version.

Runs unattended once a day. For each name on your watchlist it gathers:
  - price + position in its ~52-week range          (market context)
  - next earnings date + recent beat rate           (catalyst context)
  - WSB mention volume, trend, and a crowd/risk flag (sentiment context)
And it runs the one tested strategy signal (SPY intraday momentum).

It produces a ranked, ready-to-read briefing. It does NOT place orders and does
NOT tell you what to buy — every line is information or a your-rule signal. You
read it over coffee and place whatever you decide. That's the autonomy that's
real: the bot does all the watching, gathering, and ranking; you keep the click.

The builders take injected fetchers so this runs offline in a demo and live on
your machine (yfinance for prices, ApeWisdom for sentiment — see run_daily.py).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime

from .research.social_sentiment import SocialSnapshot

HERE = os.path.dirname(__file__)


@dataclass
class StockCard:
    symbol: str
    price: float | None
    range_pos: float | None          # 0=low, 1=high of trailing window
    next_earnings: str | None
    days_to_earnings: int | None
    beat_rate: float | None
    social: SocialSnapshot | None

    def flags(self) -> list[str]:
        out = []
        if self.range_pos is not None:
            if self.range_pos > 0.85:
                out.append("near 52w HIGH")
            elif self.range_pos < 0.15:
                out.append("near 52w LOW")
        if self.days_to_earnings is not None and 0 <= self.days_to_earnings <= 7:
            out.append(f"EARNINGS in {self.days_to_earnings}d")
        if self.social:
            flag = self.social.crowd_flag()
            if "SPIKING" in flag or "rising" in flag:
                out.append(f"WSB {flag.split(' —')[0].split(' (')[0]}")
        return out

    def notability(self) -> float:
        """Crude rank score: more notable = surfaced higher. Not a buy score."""
        s = 0.0
        if self.social and self.social.mention_change:
            s += min(self.social.mention_change, 3.0)
        if self.days_to_earnings is not None and 0 <= self.days_to_earnings <= 7:
            s += 1.0
        if self.range_pos is not None and (self.range_pos > 0.9 or self.range_pos < 0.1):
            s += 0.5
        return s


def compute_range_pos(closes: list[float], price: float, window: int = 252):
    w = closes[-window:] if len(closes) > window else closes
    lo, hi = min(w), max(w)
    return (price - lo) / (hi - lo) if hi > lo else None


def days_between(d_from: str, d_to: str) -> int:
    a = datetime.fromisoformat(d_from).date()
    b = datetime.fromisoformat(d_to).date()
    return (b - a).days


def build_card(symbol, prices, earnings_events, social_row, today_str) -> StockCard:
    closes = [b["close"] for b in prices] if prices else []
    price = closes[-1] if closes else None
    range_pos = compute_range_pos(closes, price) if price else None

    next_e, days_e, beats = None, None, None
    if earnings_events:
        reported = [e for e in earnings_events if e.get("act") is not None]
        if reported:
            b = [e for e in reported if e["act"] > e["est"]]
            beats = len(b) / len(reported)
        upcoming = [e for e in earnings_events if e.get("act") is None]
        if upcoming:
            next_e = upcoming[0]["date"]
            days_e = days_between(today_str, next_e)

    social = None
    if social_row:
        social = SocialSnapshot(
            symbol=symbol,
            mentions_24h=social_row["mentions"],
            mentions_prev_24h=social_row["prev"],
            sentiment=social_row.get("sentiment", "neutral"),
            rank=social_row.get("rank"),
        )
    return StockCard(symbol, price, range_pos, next_e, days_e, beats, social)


def render_briefing(cards: list[StockCard], spy_signal: str, today_str: str) -> str:
    cards = sorted(cards, key=lambda c: c.notability(), reverse=True)
    L = []
    L.append("#" * 60)
    L.append(f"DAILY BRIEFING — {today_str}   (information, not advice)")
    L.append("#" * 60)
    L.append("")
    L.append(f"STRATEGY SIGNAL (SPY intraday momentum, paper):")
    L.append(f"  {spy_signal}")
    L.append("")
    L.append("WATCHLIST (ranked by what's notable today):")
    L.append("-" * 60)
    for c in cards:
        flags = c.flags()
        tag = ("  [" + ", ".join(flags) + "]") if flags else ""
        price = f"${c.price:,.2f}" if c.price else "n/a"
        rp = f"{c.range_pos:.0%} of range" if c.range_pos is not None else "range n/a"
        L.append(f"{c.symbol:<6} {price:<11} {rp}{tag}")
        bits = []
        if c.next_earnings:
            bits.append(f"earnings {c.next_earnings} ({c.days_to_earnings}d)")
        if c.beat_rate is not None:
            bits.append(f"beat rate {c.beat_rate:.0%}")
        if c.social:
            chg = c.social.mention_change
            chg_s = f"{chg:+.0%}" if chg is not None else "n/a"
            bits.append(f"WSB {c.social.mentions_24h} mentions ({chg_s}), {c.social.crowd_flag()}")
        if bits:
            L.append("        " + " | ".join(bits))
    L.append("-" * 60)
    L.append("Reminder: crowd spikes are a RISK flag, not a buy signal. Earnings")
    L.append("dates mean volatility, not direction. You decide and place trades.")
    L.append("#" * 60)
    return "\n".join(L)


def _demo():
    prices = json.load(open(os.path.join(HERE, "data", "prices.json")))
    earn = json.load(open(os.path.join(HERE, "data", "earnings.json")))
    earn.pop("_note", None)
    today = "2026-06-25"

    # Real WSB rows pulled live this session (mentions, 24h% -> prev derived)
    live = {
        "GOOGL": {"mentions": 55, "prev": 38, "sentiment": "neutral", "rank": 13},
        "AMZN":  {"mentions": 43, "prev": 40, "sentiment": "neutral", "rank": 17},
        "MSFT":  {"mentions": 354, "prev": 421, "sentiment": "neutral", "rank": 4},
        "AAPL":  {"mentions": 18, "prev": 37, "sentiment": "neutral", "rank": 45},
        "NKE":   None,
    }
    cards = []
    for sym in ["AAPL", "AMZN", "MSFT", "GOOGL", "NKE"]:
        cards.append(build_card(sym, prices.get(sym), earn.get(sym), live.get(sym), today))

    spy_signal = "first-30 +0.12% -> LONG last-30 (last session); euphoria gate clear"
    print(render_briefing(cards, spy_signal, today))


if __name__ == "__main__":
    _demo()
