"""
The bot. One scheduled job that runs the whole thing unattended:
  1. records the SPY strategy signal into the paper ledger (forward_paper)
  2. generates a research briefing across your watchlist and saves it

Run once per trading day after close (4:15 PM ET). No orders, no brokerage
access. You read the briefing and place any trades yourself.

    pip install yfinance
    python -m trading_agent.briefing_daily
    # cron: 15 16 * * 1-5  cd /path && python3 -m trading_agent.briefing_daily >> bot.log 2>&1

Watchlist is yours to edit below.
"""
from __future__ import annotations

import json
import os
from datetime import date

from .config import AgentConfig, Mode
from .daily_briefing import build_card, render_briefing
from .run_daily import fetch_sentiment_rows, fetch_spy_today, _already_recorded
from .forward_paper import record_day
from .screener import run_screen, render_screen_section
from .trade_plan import build_plan, write_plan, render_plan
from .universe import scan_universe, render_universe_section

HERE = os.path.dirname(__file__)
WATCHLIST = ["AAPL", "AMZN", "MSFT", "GOOGL", "NKE", "TSLA"]
BRIEF_DIR = os.path.join(HERE, "data", "briefings")

# ---- THE SWITCH ----
# Armed by ENVIRONMENT, never by code: the committed default is always PAPER,
# so the public repo and the nightly CI job can never produce live-armed
# tickets. To arm on YOUR machine only:
#     export TRADING_GO_LIVE=1
#     export TRADING_ACCOUNT_NUMBER=...   # your account, never committed
# Even armed, this pipeline never places orders — arming makes tickets
# live-ready for YOU to place (local console Execute button / show_tickets).
_GO_LIVE = os.environ.get("TRADING_GO_LIVE") == "1"
TRADE_CONFIG = AgentConfig(
    account_number=os.environ.get("TRADING_ACCOUNT_NUMBER", "PAPER-ACCOUNT"),
    mode=Mode.LIVE if _GO_LIVE else Mode.PAPER,
    live_trading_armed=_GO_LIVE,
)
DOLLARS_PER_TRADE = 200.0


def _prices_for(symbol: str):
    """Trailing daily closes via yfinance -> [{'close': float}, ...]. [] on fail."""
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period="1y", interval="1d")
        if h.empty:
            return []
        return [{"close": float(c)} for c in h["Close"].tolist()]
    except Exception:
        return []


def _sentiment_index(rows: list[dict]) -> dict:
    """Map ticker -> {mentions, prev, sentiment, rank} from ApeWisdom rows."""
    sent_map = {"1": "negative", "2": "neutral", "3": "positive"}
    out = {}
    for r in rows:
        t = str(r.get("ticker", "")).upper()
        out[t] = {
            "mentions": int(r.get("mentions", 0) or 0),
            "prev": int(r.get("mentions_24h_ago", 0) or 0),
            "sentiment": sent_map.get(str(r.get("sentiment")), "neutral"),
            "rank": int(r["rank"]) if r.get("rank") else None,
        }
    return out


def run_strategy_record() -> str:
    """Record the SPY strategy signal; return a one-line summary for the brief."""
    res = fetch_spy_today()
    if res is None:
        return "SPY data unavailable today; no strategy record."
    session_date, bars, prev_close, ma20 = res
    if len(bars) != 13:
        return f"SPY session incomplete ({len(bars)} bars); no record yet."
    if _already_recorded(session_date):
        return f"SPY {session_date} already recorded."
    rows = fetch_sentiment_rows()
    rec = record_day(session_date, bars, prev_close, ma20, rows, use_sentiment_gate=True)
    return (f"SPY {session_date}: first30 {rec['first30_return']:+.3%} -> "
            f"{rec['decision'].upper()} (euphoria={rec['sentiment_euphoria']})")


def generate_briefing(spy_signal: str) -> str:
    today = date.today().isoformat()
    earn = json.load(open(os.path.join(HERE, "data", "earnings.json")))
    earn.pop("_note", None)
    sent = _sentiment_index(fetch_sentiment_rows())

    cards = []
    for sym in WATCHLIST:
        cards.append(build_card(sym, _prices_for(sym), earn.get(sym),
                                sent.get(sym), today))

    # Deterministic screen -> research section appended to the public briefing.
    candidates = run_screen(cards)
    # Same screen over the broad universe -> names you don't already follow.
    universe_hits = scan_universe(sent, exclude=frozenset(WATCHLIST))
    brief = render_briefing(cards, spy_signal, today)
    brief += "\n\n" + render_screen_section(candidates)
    brief += "\n\n" + render_universe_section(universe_hits) + "\n" + "#" * 60
    candidates = candidates + universe_hits

    os.makedirs(BRIEF_DIR, exist_ok=True)
    path = os.path.join(BRIEF_DIR, f"briefing_{today}.txt")
    with open(path, "w") as f:
        f.write(brief)
    return brief, path, candidates


def main() -> None:
    signal = run_strategy_record()
    brief, path, candidates = generate_briefing(signal)
    print(brief)

    # Executable, risk-checked tickets -> PRIVATE (git-ignored), never published.
    plan = build_plan(candidates, TRADE_CONFIG, dollars_per_trade=DOLLARS_PER_TRADE)
    plan_path = write_plan(plan)
    print("\n" + render_plan(plan))
    print(f"\nPrivate plan: {plan_path}")
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
