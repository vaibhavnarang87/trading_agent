# How to Build & Run the Bot

Everything is written. This is the assembly guide — how the pieces fit, what to
install, how to run each mode, and the three things that are yours to wire.

---

## What the bot is (the pieces)

| Layer | Module | What it does |
|---|---|---|
| Research (LLM) | `llm_research/` | Reads news → structured signal (facts/sentiment, no trade call) |
| Research (data) | `research/` | Fundamentals, earnings, WSB sentiment → cards |
| Briefing | `briefing_daily.py` | Autonomous daily research briefing across a watchlist |
| Strategy | `strategy_dipbuy.py`, `strategy_spy_intraday.py` | Deterministic rules (your logic) |
| Backtests | `backtest_*.py` | Honest evaluation on real history |
| Risk | `risk_governor.py` | Deterministic caps + kill switch (can veto anything) |
| Autonomy | `runner.py` | Unattended loop (paper) running a deterministic strategy |
| Forward test | `forward_paper.py`, `run_daily.py` | Logs live paper decisions to a ledger |
| Approval POC | `poc/` | Order → review preview → approval → your `place()` |

---

## Prerequisites

```bash
python3 --version           # 3.10+
pip install yfinance        # price/data feeds (read-only, no login)
# optional, for the LLM layer:
export ANTHROPIC_API_KEY="your_key"
```

Put the `trading_agent/` folder inside a project folder. Run all commands from
the folder that *contains* `trading_agent/`.

---

## Run each mode

**1. Research briefing (fully autonomous, zero risk) — start here.**
```bash
python -m trading_agent.briefing_daily
```
Edit `WATCHLIST` at the top of `briefing_daily.py`. Runs after a weekday close,
writes a briefing to `data/briefings/`. This is the piece that works regardless
of whether any strategy has an edge — it makes *you* a sharper decision-maker.

**2. LLM research layer (news → structured signal → your rule).**
```bash
python -m trading_agent.llm_research.demo
```
Live: set `ANTHROPIC_API_KEY`, wire `search_fn` in `gather.py` to a news source,
replace `example_rule` in `demo.py` with your logic.

**3. Backtest a strategy (do this before trusting anything).**
```bash
python -m trading_agent.backtest_dipbuy        # dip-buy-quality
python -m trading_agent.backtest_spy_strategy  # SPY intraday
```
Reality check: as tested, both UNDERPERFORMED buy-and-hold. Don't run real money
on a rule that failed its backtest.

**4. Forward paper test (collect out-of-sample evidence).**
```bash
python -m trading_agent.run_daily              # records one day to the ledger
python -m trading_agent.forward_paper report   # shows the running record
```

**5. Autonomous runner (paper).**
```bash
python -m trading_agent.runner
```

**6. Approval-flow POC (review previews + your execution).**
```bash
python -m trading_agent.poc.demo
```

---

## The three things that are YOURS to wire

1. **Your rule.** Replace the placeholder in `strategy_dipbuy.py` / the LLM
   `example_rule` with logic you believe in and have backtested.
2. **`search_fn`** (in `llm_research/gather.py`) — point at a news source so the
   LLM layer reads live news.
3. **`place()`** (in `poc/execution.py`) — the live order call. This is the one
   piece that stays with you, wired to your own Robinhood access with your own
   credentials. Everything up to it is built; the trigger is yours.

---

## Make it autonomous (scheduling)

```bash
# weekdays 4:15 PM ET — runs briefing + strategy record
15 16 * * 1-5  cd /path/to/project && python3 -m trading_agent.briefing_daily >> bot.log 2>&1
```
(macOS/Linux via `crontab -e`; Windows via Task Scheduler.) The machine must be
awake at run time.

---

## Honest operating rules (baked into the design)

- **Paper first.** Nothing here places real orders. The live path is a stub you
  implement deliberately, after a strategy proves out.
- **No strategy has shown an edge yet.** Five backtests, five no-edges. Treat the
  bot as a research + evidence-collection system until *your* forward data earns
  a live trial.
- **The risk governor is mandatory.** Keep `max_order_value` / `max_position_pct`
  tight. Test that it vetoes an oversized order before trusting it.
- **PDT rule:** under $25k, max 3 day trades / 5 business days. Build around it.
- **The execution tap is yours.** By design, not oversight.
```
