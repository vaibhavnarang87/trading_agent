# Strategy Survey: Post-Earnings-Announcement Drift (PEAD)

*Prepared for the Robinhood trading-agent build. This is research, not advice.*

## The thesis

When a company reports earnings that beat expectations, its stock does not
fully reprice on the announcement day. Instead it tends to **drift** in the
direction of the surprise over the following days and weeks. Buy the beats,
hold for ~a month, capture the drift.

This is not a fringe idea. It's one of the oldest and most-documented anomalies
in academic finance — Ball & Brown (1968) first noticed it, Bernard & Thomas
(1989) formalized it, and it's been replicated across decades and markets. If
any "sophisticated, literature-backed" retail strategy deserves a look, this is
a reasonable candidate. It connects directly to the earnings/fundamentals
research you wanted, and it produces clean, fixed, testable rules.

## Why it's *supposed* to work

The academic explanation is under-reaction: investors anchor on prior beliefs
and update too slowly to earnings news, especially for the full implications of
a surprise. The drift is the market catching up.

## Why it may not work for you (the honest part)

1. **It's been heavily arbitraged.** A famous anomaly is a crowded one. Studies
   since the 2000s find PEAD has weakened substantially as funds trade it away.
   The edge that existed in 1989 data is not the edge available in 2026.
2. **The biggest move happens overnight, where you can't trade.** Most of the
   reaction to earnings is the after-hours gap. By the time you can buy at the
   next open, that's gone. The *drift* is what's left — smaller, noisier.
3. **Transaction costs and slippage** eat a thin edge. On a $5k account, even
   commission-free, the bid/ask and timing slippage matter.
4. **Small sample = loud noise.** A handful of trades can't distinguish skill
   from luck. You need many events before a result means anything.

## The rules this thesis determines

| Parameter | Value | Why |
|---|---|---|
| Signal | EPS surprise = (actual − est) / \|est\| | Standardized beat size |
| Filter | surprise ≥ 2.5% | Trade only meaningful beats |
| Entry | next-day **open** after report | Reports are after-close; the gap isn't tradeable |
| Hold | 20 trading days (~1 month) | The classic PEAD window |
| Exit | close on day 20 | Fixed horizon, no discretion |
| Sizing | equal dollars per event | No bet-sizing games |

These are *fixed in advance*. That's the whole point — a thesis you commit to,
then test, so the backtest means something.

## What the backtest found (real data)

I ran exactly these rules against **real** Robinhood data: 30 qualifying
earnings beats across AAPL, AMZN, MSFT, GOOGL, NKE, from Oct 2024 to Apr 2026,
using split-adjusted daily prices.

**Result: essentially no edge.**

- Average return per trade: **−0.06%**
- Win rate: **53%** (16 of 30 — barely a coin flip)
- Spread: best +13.5%, worst −16.3%, std dev 8.1%
- t-statistic on the mean: **−0.04** — utterly insignificant. You cannot
  distinguish this from random noise.

In plain terms: over this period, on these names, buying the beats and holding a
month was a wash. The drift the literature describes did not show up in a form
you could have profited from. This is *not* a bug in the code — it's the honest
answer, and it matches what the modern literature predicts: the anomaly has
largely been competed away.

## What this tells us

This is the single most valuable output of the whole exercise, and it's worth
sitting with: a genuinely sophisticated, decades-validated strategy, tested
honestly on real data, **showed no usable edge for an account like yours.** That
is the normal result. It's why "keep researching until something works" is a
trap — if you torture enough variations, one will look good on the past by pure
chance, and that's the one that loses money live.

The constructive read isn't "give up." It's:

- The backtester now works and is honest. You can point it at any rule set and
  get a real answer instead of a hopeful one.
- A wash-after-costs result on a famous anomaly is the *expected* outcome, and
  knowing that protects you from the strategies that merely *look* like edges.
- Where retail accounts actually do better is usually boring: low-cost
  diversification, not high-frequency cleverness. That's not what you asked for,
  but it's what the evidence keeps pointing at.

## Next options

1. Test a different thesis (momentum, mean-reversion) the same honest way.
2. Widen the universe and event count to see if the PEAD result holds or was
   sample-specific.
3. Use the market-research layer to inform *your own* discretionary decisions,
   with the deterministic engine only ever executing rules you've validated.

What I won't do is keep mutating the strategy until the backtest turns green.
That number turning green that way is the warning sign, not the goal.
