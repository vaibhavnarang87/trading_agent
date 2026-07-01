# Trading Agent — Paper-Mode Skeleton

A proposal-and-approval trading framework for the Robinhood Agentic cash account
(••••3726, $5,000, equities-only). **Paper mode by default. No real money is at
risk in this skeleton.**

## What this is

The deterministic, auditable scaffolding from the architecture handoff:

```
strategy proposal -> risk governor -> human approval gate -> broker -> audit log
   (your rules)      (deterministic     (yes/no)            (paper      (JSON
                      hard limits,                           by          ledger)
                      can VETO)                              default)
```

## What's built

| Component | File | Status |
|---|---|---|
| Risk governor (the safety core) | `risk_governor.py` | Implemented + 10 unit tests |
| Config & risk limits | `config.py` | Implemented |
| Domain types | `types.py` | Implemented |
| Paper broker (simulated fills) | `broker.py` | Implemented |
| Live broker | `broker.py` | **Stub — intentionally raises** |
| Strategy interface | `strategy.py` | Interface + placeholder example |
| Executor (wires the gates) | `executor.py` | Implemented |
| Audit ledger | `audit.py` | Implemented |
| Paper demo | `main.py` | Runnable |

## What's deliberately NOT built

- **No live order placement.** `LiveBroker.execute` raises. Wiring it to the
  Robinhood MCP `place_equity_order` is a separate, explicit step — gated behind
  the governor and human approval, and only after paper + backtest prove out.
- **No LLM picking live trades.** The strategy is *your* deterministic rules, by
  design. That keeps it backtestable, replayable, and auditable. (It's also the
  line I won't cross: an LLM generating live personalized buy/sell calls is
  automated investment advice.)
- **No margin, options, or shorting.** The Agentic account is cash + equities only.

## The risk governor

Deterministic checks, in order. Any failure vetoes the trade with a logged reason:

1. Global kill switch (latches for the day once engaged)
2. Max daily loss (breaching it latches the kill switch)
3. Max trades per day
4. Proposal sanity (positive qty/price)
5. **Buys only:** max order value ($), max order as % of account
6. **Buys only:** cash-only guard (no spending beyond settled cash)
7. **Buys only:** per-symbol concentration cap
8. **Sells:** cannot sell more than held (no shorting)

Sells are never blocked by sizing caps — you must always be able to exit a position.

## Run it

```bash
python -m pytest trading_agent/tests/ -q   # 10 passing
python -m trading_agent.main               # paper demo, prints JSON audit trail
```

## Your turn — author the rules (handoff Section 8)

Replace `ExampleThresholdStrategy` in `strategy.py` with your rules:

- Entry trigger — what makes it buy?
- Exit trigger — profit target and/or signal to sell?
- Position size — capital per trade
- Stop-loss — max loss per position
- Max trades/day and max daily loss — already wired into `RiskLimits`, just set the numbers

Once the rules are in, next steps are the backtester (handoff Step 5) and only
then a deliberate, gated live wiring (Step 6).

---

*Not financial advice. Trading involves substantial risk of loss; aggressive
automated strategies on small accounts can lose money rapidly.*
