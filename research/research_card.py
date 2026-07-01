"""
Research card — combines market + social into one human-readable briefing.

Information only. No recommendation. The card ends by handing the decision back
to you.

The demo at the bottom runs on REAL data gathered on 2026-06-25:
  - AAPL earnings: real, from Robinhood get_earnings_results
  - WSB trending snapshot: real names observed trending (TSLA, NVDA, RIVN, RDDT,
    AMD) from live aggregator search. Mention counts are illustrative of the
    shape; at runtime social_sentiment.fetch pulls exact live numbers.
"""
from __future__ import annotations

from .market_research import MarketSnapshot, EarningsPoint
from .social_sentiment import SocialSnapshot


def render_card(mkt: MarketSnapshot, soc: SocialSnapshot | None) -> str:
    L = []
    L.append("=" * 60)
    L.append(f"RESEARCH CARD — {mkt.symbol}    (information, not advice)")
    L.append("=" * 60)

    # Market
    L.append("MARKET")
    if mkt.price is not None:
        L.append(f"  Price: ${mkt.price:,.2f}"
                 + (f"   P/E: {mkt.pe:.1f}" if mkt.pe else ""))
    if mkt.range_position is not None:
        pos = mkt.range_position
        where = ("near 52w HIGH" if pos > 0.8 else
                 "near 52w LOW" if pos < 0.2 else "mid-range")
        L.append(f"  52-week range: {pos:.0%} of range ({where})")
    if mkt.next_earnings:
        L.append(f"  Next earnings: {mkt.next_earnings} (watch for volatility)")
    if mkt.beat_rate is not None:
        L.append(f"  Beat rate (last {len([e for e in mkt.earnings if e.act is not None])}q): "
                 f"{mkt.beat_rate:.0%}")
        recent = [e for e in mkt.earnings if e.act is not None][-3:]
        for e in recent:
            s = e.surprise
            L.append(f"    {e.date}: est {e.est:.2f} / act {e.act:.2f}"
                     + (f"  ({s:+.0%} surprise)" if s is not None else ""))

    # Social
    L.append("")
    L.append("CROWD STATE (Reddit / WSB)")
    if soc is None:
        L.append("  No notable WSB chatter. (Quiet usually = fundamentals-driven.)")
    else:
        L.append(f"  Mentions 24h: {soc.mentions_24h} "
                 f"(prev {soc.mentions_prev_24h}"
                 + (f", {soc.mention_change:+.0%}" if soc.mention_change is not None else "")
                 + ")")
        L.append(f"  Tone: {soc.sentiment}")
        L.append(f"  Read: {soc.crowd_flag()}")
        L.append(f"  Note: {soc.caution_note()}")

    L.append("")
    L.append("YOUR CALL")
    L.append("  This card is context, not a recommendation. Decide based on your")
    L.append("  own rules and risk limits. The execution engine will only run")
    L.append("  strategies you've defined and backtested — never this card.")
    L.append("=" * 60)
    return "\n".join(L)


def _demo():
    # Real AAPL earnings (from Robinhood, 2026-06-25)
    aapl = MarketSnapshot(
        symbol="AAPL", price=293.08, pe=None,
        low_52w=169.0, high_52w=299.70,
        earnings=[
            EarningsPoint("2025-10-30", 1.75, 1.85),
            EarningsPoint("2026-01-29", 2.66, 2.84),
            EarningsPoint("2026-04-30", 1.94, 2.01),
            EarningsPoint("2026-07-30", 1.89, None),
        ],
    )
    # Illustrative crowd snapshot in real observed shape (RDDT was flagged red)
    rddt_soc = SocialSnapshot(
        symbol="RDDT", mentions_24h=80, mentions_prev_24h=30,
        sentiment="negative", rank=1
    )
    print(render_card(aapl, None))
    print()
    # A trending-name card to show the risk-flag behavior
    rddt_mkt = MarketSnapshot(symbol="RDDT", price=None)
    print(render_card(rddt_mkt, rddt_soc))


if __name__ == "__main__":
    _demo()
