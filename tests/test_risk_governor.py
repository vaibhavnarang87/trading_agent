"""
Tests for the risk governor. The safety core gets real coverage.
Run: python -m pytest trading_agent/tests/ -q
"""
from datetime import date

from trading_agent.config import RiskLimits
from trading_agent.risk_governor import RiskGovernor
from trading_agent.types import (
    AccountState,
    DailyState,
    Position,
    Side,
    TradeProposal,
)


def acct(value=5000.0, cash=5000.0, positions=None):
    return AccountState(account_value=value, cash=cash, positions=positions or {})


def daily():
    return DailyState(day=date(2026, 6, 25))


def limits(**kw):
    return RiskLimits(**kw)


def buy(symbol="AAPL", qty=1.0, price=100.0):
    return TradeProposal(symbol, Side.BUY, qty, price, reason="test")


def sell(symbol="AAPL", qty=1.0, price=100.0):
    return TradeProposal(symbol, Side.SELL, qty, price, reason="test")


def gov(**kw):
    return RiskGovernor(limits(**kw))


def test_clean_buy_passes():
    g = gov(max_order_value=250, max_position_pct=0.10, max_position_per_symbol_pct=0.20)
    d = g.evaluate(buy(qty=1, price=100), acct(), daily())
    assert d.approved, d.reason


def test_order_value_cap_blocks():
    g = gov(max_order_value=50)
    d = g.evaluate(buy(qty=1, price=100), acct(), daily())
    assert not d.approved and "max_order_value" in d.reason


def test_position_pct_cap_blocks():
    g = gov(max_order_value=10000, max_position_pct=0.01)  # 1% of 5000 = $50
    d = g.evaluate(buy(qty=1, price=100), acct(), daily())
    assert not d.approved and "of account" in d.reason


def test_buy_over_cash_blocks():
    g = gov(max_order_value=10000, max_position_pct=1.0, enforce_cash_only=True)
    d = g.evaluate(buy(qty=10, price=100), acct(cash=500), daily())
    assert not d.approved and "cash" in d.reason


def test_per_symbol_concentration_blocks():
    g = gov(max_order_value=10000, max_position_pct=1.0, max_position_per_symbol_pct=0.20)
    # already holding $900 of AAPL; account 5000 -> cap is $1000; buying $200 more -> $1100 > cap
    positions = {"AAPL": Position("AAPL", quantity=9, average_cost=100)}
    d = g.evaluate(buy(qty=2, price=100), acct(positions=positions), daily())
    assert not d.approved and "per-symbol" in d.reason


def test_max_trades_per_day_blocks():
    g = gov(max_trades_per_day=2)
    d = daily()
    d.trades_today = 2
    out = g.evaluate(buy(), acct(), d)
    assert not out.approved and "Max trades/day" in out.reason


def test_daily_loss_latches_kill_switch():
    g = gov(max_daily_loss=150)
    d = daily()
    d.realized_pnl_today = -200
    out = g.evaluate(buy(), acct(), d)
    assert not out.approved
    assert d.kill_switch_engaged  # latched
    # subsequent proposals blocked by kill switch
    out2 = g.evaluate(buy(), acct(), d)
    assert not out2.approved and "Kill switch" in out2.reason


def test_cannot_sell_more_than_held():
    g = gov()
    positions = {"AAPL": Position("AAPL", quantity=1, average_cost=100)}
    d = g.evaluate(sell(qty=5, price=100), acct(positions=positions), daily())
    assert not d.approved and "no shorting" in d.reason


def test_sell_held_passes():
    g = gov()
    positions = {"AAPL": Position("AAPL", quantity=5, average_cost=100)}
    d = g.evaluate(sell(qty=5, price=100), acct(positions=positions), daily())
    assert d.approved, d.reason


def test_rejects_nonpositive_qty_and_price():
    g = gov()
    assert not g.evaluate(buy(qty=0, price=100), acct(), daily()).approved
    assert not g.evaluate(buy(qty=1, price=0), acct(), daily()).approved
