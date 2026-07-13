"""
trading-agent-mcp — expose YOUR trading pipeline to AI agents as MCP tools.

The idea borrowed from robinhood-for-agents, applied with this project's
safety model: agents get everything up to the trigger — research, screens,
plans, governor-checked tickets, ledgers, backtests — and NO execution tool.
Placing an order stays a human act in the local console. By design there is
no place/execute/cancel tool in this server and none should ever be added.

Register with Claude Code (one time):
    claude mcp add -s user trading-agent -- \
        /Users/vaibhavnarang/Documents/Claude/trading_agent/.venv/bin/python3 \
        -m trading_agent.mcp_server

Then any session can call get_plan / run_screen / add_ticket / get_ledgers /
dca_status / backtest_screen.
"""
from __future__ import annotations

import glob
import io
import json
import os
from contextlib import redirect_stdout
from datetime import date

from mcp.server.fastmcp import FastMCP

from .env_file import load_env_file

load_env_file()

HERE = os.path.dirname(__file__)
PRIVATE = os.path.join(HERE, "data", "private")
BRIEFINGS = os.path.join(HERE, "data", "briefings")

mcp = FastMCP(
    "trading-agent",
    instructions=(
        "Research and planning tools for the user's paper/live trading agent. "
        "These tools READ state and BUILD risk-checked tickets only — there is "
        "deliberately no order-placement tool. Execution happens in the user's "
        "local console with their click."
    ),
)


@mcp.tool()
def get_briefing(day: str = "") -> str:
    """Get the daily research briefing (watchlist + screen candidates).
    day: YYYY-MM-DD, empty for latest."""
    files = sorted(glob.glob(os.path.join(BRIEFINGS, "briefing_*.txt")))
    if not files:
        return "No briefings found. Run: python -m trading_agent.briefing_daily"
    if day:
        want = os.path.join(BRIEFINGS, f"briefing_{day}.txt")
        if not os.path.exists(want):
            have = ", ".join(f.split("briefing_")[-1][:-4] for f in files[-10:])
            return f"No briefing for {day}. Recent: {have}"
        return open(want).read()
    return open(files[-1]).read()


@mcp.tool()
def get_plan() -> str:
    """Get the latest trade plan: every ticket with its reasons, risk-governor
    verdict, armed status, and broker params. Read-only."""
    files = sorted(glob.glob(os.path.join(PRIVATE, "trade_plan_*.json")))
    if not files:
        return "No trade plan on disk. Run: python -m trading_agent.briefing_daily"
    return open(files[-1]).read()


@mcp.tool()
def add_ticket(symbol: str, dollars: float = 200.0, reason: str = "added via MCP") -> str:
    """Add a market-buy ticket for `symbol` to today's plan. The ticket runs
    through the SAME deterministic risk governor as all others (order caps,
    cash checks). This BUILDS the ticket only — placing it remains the user's
    click in their local console."""
    from .add_tickets import add
    buf = io.StringIO()
    with redirect_stdout(buf):
        add([symbol], dollars, reason)
    return buf.getvalue() or "done"


@mcp.tool()
def run_screen(include_universe: bool = False) -> str:
    """Run the quality-dip screen live on the watchlist (and optionally the
    ~100-name universe — slower). Returns candidates with the exact reasons
    they passed or were blocked. Research, not advice."""
    from .briefing_daily import WATCHLIST, _prices_for, _sentiment_index
    from .daily_briefing import build_card
    from .run_daily import fetch_sentiment_rows
    from .screener import render_screen_section, run_screen as _run

    today = date.today().isoformat()
    earn = json.load(open(os.path.join(HERE, "data", "earnings.json")))
    earn.pop("_note", None)
    sent = _sentiment_index(fetch_sentiment_rows())
    cards = [build_card(s, _prices_for(s), earn.get(s), sent.get(s), today)
             for s in WATCHLIST]
    out = render_screen_section(_run(cards))
    if include_universe:
        from .universe import render_universe_section, scan_universe
        out += "\n\n" + render_universe_section(
            scan_universe(sent, exclude=frozenset(WATCHLIST)))
    return out


@mcp.tool()
def get_ledgers() -> str:
    """All activity ledgers: console executions (real + paper clicks), DCA bot
    buys, and the forward-paper strategy record summary."""
    out = {}
    for name, path in (("executions", os.path.join(PRIVATE, "executions.jsonl")),
                       ("dca_buys", os.path.join(PRIVATE, "dca_ledger.jsonl"))):
        if os.path.exists(path):
            out[name] = [json.loads(l) for l in open(path) if l.strip()]
        else:
            out[name] = []
    fp = os.path.join(HERE, "data", "forward_paper_ledger.jsonl")
    if os.path.exists(fp):
        rows = [json.loads(l) for l in open(fp) if l.strip()]
        out["forward_paper"] = {"days": len(rows),
                                "last": rows[-1] if rows else None}
    return json.dumps(out, indent=2)


@mcp.tool()
def dca_status() -> str:
    """Status of the always-on DCA bot: rule, mode (paper/live), last buy,
    next due date."""
    from .dca_bot import (DCA_DOLLARS, DCA_INTERVAL_DAYS, DCA_SYMBOL,
                          _buys, _dca_executor, due, market_open)
    _, label = _dca_executor()
    buys = _buys()
    return json.dumps({
        "rule": f"${DCA_DOLLARS:.0f} {DCA_SYMBOL} every {DCA_INTERVAL_DAYS:g} days",
        "executor": label,
        "buys_recorded": len(buys),
        "last_buy": buys[-1] if buys else None,
        "due_now": due(),
        "market_open_now": market_open(),
    }, indent=2)


@mcp.tool()
def backtest_screen() -> str:
    """Backtest the quality-dip screen rule on ~5y of real history (40 large
    caps) vs buy-and-hold benchmarks. Slow (~1-2 min, downloads data)."""
    from .backtest_screen import run
    buf = io.StringIO()
    with redirect_stdout(buf):
        run()
    return buf.getvalue()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
