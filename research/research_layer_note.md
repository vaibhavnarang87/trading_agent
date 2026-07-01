# The Research Layer — design & honest framing

The research layer answers a different question from the strategy engine. The
engine asks *"do my fixed rules fire?"* The research layer asks *"what should I,
the human, know about this name before I decide?"* It produces **information,
never a buy/sell signal.** You make the call.

It has two components.

## 1. Market research (from Robinhood, structured & reliable)

For any ticker, it assembles:
- **Valuation & profile** — price, P/E, market cap, 52-week range position,
  shares outstanding (get_equity_fundamentals)
- **Earnings** — last 8 quarters of estimate-vs-actual, next report date, and
  the EPS surprise history (get_earnings_results)
- **Price context** — where it sits in its recent range, recent trend
  (get_equity_historicals)

This is the solid, factual core. It's the same data a careful investor would
pull up manually, just gathered in one place.

## 2. Social sentiment (Reddit / WallStreetBets, handled with care)

It pulls WSB mention volume and sentiment from public aggregators (ApeWisdom,
SwaggyStocks, YOLOStocks, QuiverQuant) — how much a ticker is being talked
about, whether chatter is rising fast, and the rough tone.

**Here is the design decision I'm making explicit, and won't quietly reverse:**

Social hype is shown as a **risk flag, not a buy signal.** This isn't caution
for its own sake — it's what the evidence says. Barber & Odean ("All That
Glitters", 2008) documented that attention-driven retail buying tends to
*precede underperformance*. Meme-stock surges routinely round-trip — the people
who buy *because* a name is trending are usually late. A spike in WSB mentions
more reliably marks elevated risk and crowding than opportunity.

So the layer surfaces sentiment, but frames a sharp rise in retail attention as
*"caution: crowded / late-stage interest"* rather than *"momentum: buy."* If I
built it to treat hype as a buy trigger, I'd be handing you a machine for
buying tops — the precise behavior that drains small aggressive accounts. The
information is useful; the naive reading of it is the trap.

## What this layer does NOT do

- It does not emit recommendations. No "buy NVDA." It shows you the facts and
  the crowd state; the judgment is yours.
- It does not feed an auto-trader. Sentiment never pulls a trigger. (The
  execution engine only ever runs *your* deterministic, backtested rules.)
- It does not treat Reddit as a signal source to optimize against. Fitting a
  strategy to last month's meme flow is the overfitting trap in a louder shirt.

## How to actually use it

Use it like a briefing. Before you decide anything on a name, read the market
card (is it expensive? when's earnings? where in its range?) and glance at the
crowd state (is this quietly fundamental, or is it the WSB ticker of the day?).
Then *you* decide. The layer makes you better-informed; it doesn't decide for
you — by design.
