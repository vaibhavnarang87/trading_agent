"""
Live executor — YOUR Robinhood access, wired to the poc OrderExecutor interface.

This implements the one piece the project deliberately left to you: place().
It talks DIRECTLY to Robinhood with YOUR login via the robin_stocks library.
Nothing routes through an AI assistant, the public site, or anyone else's
session: your credentials live in env vars you set, the login (incl. MFA
prompt) happens in your terminal, and the order fires only when you click
Execute in the localhost console and confirm.

Setup (only if/when you choose to go live):
    pip install robin_stocks
    export RH_USERNAME="you@example.com"
    export RH_PASSWORD="..."            # your machine, your env, never committed
    export TRADING_EXECUTOR=robinhood   # default is "paper" (simulated fills)
    python -m trading_agent.local_app   # login/MFA prompt appears at startup

Notes, honestly:
  - robin_stocks is an UNOFFICIAL library (community wrapper around Robinhood's
    private endpoints). Using it is your call and subject to Robinhood's terms.
  - Default remains the PaperExecutor. Real money requires you to explicitly
    set TRADING_EXECUTOR=robinhood AND arm the switch AND click AND confirm.
"""
from __future__ import annotations

import os

from .poc.execution import OrderExecutor, PaperExecutor
from .poc.order import Order, OrderType


class RobinhoodExecutor(OrderExecutor):
    """Direct-API executor. Credentials come from YOUR env; login happens in
    YOUR terminal at startup. place() maps a validated Order to the matching
    robin_stocks order call and returns the raw broker response."""

    def __init__(self):
        try:
            import robin_stocks.robinhood as rh
        except ImportError as e:
            raise RuntimeError(
                "robin_stocks is not installed. Run: pip install robin_stocks"
            ) from e
        from .secrets_store import get_rh_password
        user = os.environ.get("RH_USERNAME")
        pwd = get_rh_password()   # macOS Keychain first, env fallback
        if not user or not pwd:
            raise RuntimeError(
                "No Robinhood credentials found. Set them once via the console "
                "settings page or `python -m trading_agent.local_app --setup`. "
                "The password is stored in your macOS Keychain, not in plaintext."
            )
        self.rh = rh
        # Interactive: robin_stocks prompts for MFA in the terminal — that
        # prompt is you, personally, authorizing this session.
        self.rh.login(user, pwd)
        # robin_stocks can FAIL login without raising (expired token + headless
        # challenge). Verify, or an "armed" bot would silently bounce every
        # order. Raise -> callers label auto-exec disabled and notify.
        import robin_stocks.robinhood.helper as _helper
        if not getattr(_helper, "LOGGED_IN", False):
            raise RuntimeError(
                "Robinhood login failed (session expired; interactive approval "
                "needed). Run `python -m trading_agent.local_app` in a terminal "
                "once to re-login, then reload the scanner."
            )

    def _verify_account(self, account_number: str) -> None:
        """Confirm the target account exists and is reachable by this login.
        Refuse to place if we can't resolve it — better than silently landing
        on the Robinhood default account (the wrong-account bug)."""
        if not account_number or account_number == "PAPER-ACCOUNT":
            raise RuntimeError(
                "No real account_number configured — refusing to place. Set "
                "TRADING_ACCOUNT_NUMBER to your Agentic account."
            )
        try:
            url = self.rh.account.load_account_profile(
                account_number=account_number, info="url")
        except Exception as e:
            raise RuntimeError(f"Could not resolve account {account_number}: {e}") from e
        if not url:
            raise RuntimeError(
                f"Account {account_number} is not accessible by this login. "
                f"Refusing to place (would otherwise hit the default account)."
            )

    def place(self, order: Order) -> dict:
        # Route to the SPECIFIC configured account. Without this, robin_stocks
        # places on the Robinhood default account — which is NOT the Agentic
        # account and caused a real order to land in the wrong account.
        acct = order.account_number
        self._verify_account(acct)
        o = self.rh.orders
        tif = order.time_in_force
        if order.type == OrderType.MARKET and order.dollar_amount is not None:
            fn = (o.order_buy_fractional_by_price if order.side.value == "buy"
                  else o.order_sell_fractional_by_price)
            resp = fn(order.symbol, order.dollar_amount, account_number=acct,
                      timeInForce=tif)
        elif order.type == OrderType.MARKET and order.quantity is not None:
            fn = (o.order_buy_market if order.side.value == "buy"
                  else o.order_sell_market)
            resp = fn(order.symbol, order.quantity, account_number=acct,
                      timeInForce=tif)
        elif order.type == OrderType.LIMIT and order.quantity is not None:
            fn = (o.order_buy_limit if order.side.value == "buy"
                  else o.order_sell_limit)
            resp = fn(order.symbol, order.quantity, order.limit_price,
                      account_number=acct, timeInForce=tif)
        else:
            raise ValueError(f"Unsupported order shape: {order.describe()}")

        # Verify the broker actually accepted it. robin_stocks returns a dict
        # with an 'id' on success, or an error payload (no 'id') on failure —
        # do NOT report success blindly (that produced a false confirmation).
        if not isinstance(resp, dict) or not resp.get("id"):
            raise RuntimeError(
                f"Order was NOT accepted by Robinhood. Response: {resp}")
        return {"status": "submitted", "real_money": True,
                "order": order.describe(), "account": acct,
                "ref_id": order.ref_id, "broker_order_id": resp.get("id"),
                "broker_state": resp.get("state"), "broker_response": resp}


def get_executor() -> tuple[OrderExecutor, str]:
    """Executor + human-readable label, chosen by TRADING_EXECUTOR env var.
    Default is paper: simulated fills, no credentials, no real money."""
    kind = os.environ.get("TRADING_EXECUTOR", "paper").strip().lower()
    if kind == "robinhood":
        return RobinhoodExecutor(), "ROBINHOOD — REAL MONEY"
    return PaperExecutor(), "paper (simulated fills)"
