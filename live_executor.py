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
        # robin_stocks 3.4.0 bug: if the post-approval login reply lacks
        # 'token_type', it has ALREADY opened the session pickle for writing,
        # so the crash truncates the saved token to 0 bytes. Every failed
        # attempt then forces a fresh device approval on the user's phone.
        # Back the token up and restore it on failure, and record the reply's
        # KEYS (never values) so the incompatibility can be diagnosed.
        import shutil
        import robin_stocks.robinhood.authentication as _auth
        pkl = os.path.expanduser("~/.tokens/robinhood.pickle")
        bak = pkl + ".bak"
        if os.path.exists(pkl) and os.path.getsize(pkl) > 0:
            shutil.copy2(pkl, bak)
        _orig_post = _auth.request_post

        def _logged_post(url, payload=None, *a, **kw):
            r = _orig_post(url, payload, *a, **kw)
            if "oauth2/token" in str(url):
                keys = sorted(r) if isinstance(r, dict) else type(r).__name__
                # When the reply carries no token, Robinhood puts the reason in
                # 'detail'. That is an error string, never a credential, and it
                # is the only thing that says WHY the login is being refused.
                if isinstance(r, dict) and "access_token" not in r and r.get("detail"):
                    keys = f"{keys}  detail={r['detail']!r}"
                with open(os.path.join(os.path.dirname(__file__), "data",
                                       "private", "login_debug.log"), "a") as f:
                    from datetime import datetime as _dt
                    f.write(f"{_dt.now().isoformat(timespec='seconds')} "
                            f"token reply keys: {keys}\n")
            return r

        _auth.request_post = _logged_post
        try:
            # Interactive: robin_stocks prompts for MFA in the terminal — that
            # prompt is you, personally, authorizing this session.
            self.rh.login(user, pwd)
        finally:
            _auth.request_post = _orig_post
            if (os.path.exists(bak) and
                    (not os.path.exists(pkl) or os.path.getsize(pkl) == 0)):
                shutil.copy2(bak, pkl)
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
        # An order ID is NOT acceptance: Robinhood can reject milliseconds
        # later (a real ADP order did exactly that while we logged 'submitted').
        # Re-fetch the order and check its actual state.
        import time as _time
        state = resp.get("state")
        try:
            _time.sleep(2)
            info = self.rh.orders.get_stock_order_info(resp["id"])
            if isinstance(info, dict) and info.get("state"):
                state = info["state"]
        except Exception:
            pass  # keep the creation-time state if the re-fetch fails
        if state in ("rejected", "cancelled", "failed", "voided"):
            raise RuntimeError(
                f"Order {state} by Robinhood after submission "
                f"(order id {resp['id']}).")
        return {"status": "submitted", "real_money": True,
                "order": order.describe(), "account": acct,
                "ref_id": order.ref_id, "broker_order_id": resp.get("id"),
                "broker_state": state, "broker_response": resp}


def all_positions(rh, account_number: str) -> list[dict]:
    """ALL non-zero positions for one account, following pagination.

    robin_stocks' get_open_stock_positions(account_number=...) silently returns
    only the FIRST PAGE — 10 of 18 positions here — while the no-argument form
    returns a different account entirely (the default/margin one). Both are
    wrong for us: the truncated view hid momentum and RSI2 holdings from the
    exit engine, so no stop-loss or exit rule could fire on them.

    This walks every page of /positions/ for the requested account.
    """
    out: list[dict] = []
    url = (f"https://api.robinhood.com/positions/"
           f"?account_number={account_number}&nonzero=true")
    seen = set()
    while url and url not in seen:
        seen.add(url)
        data = rh.helper.request_get(url, "regular")
        if not isinstance(data, dict):
            break
        for p in data.get("results") or []:
            if float(p.get("quantity") or 0) > 0:
                out.append(p)
        url = data.get("next")
    return out


def all_orders(rh, account_number: str, since: str = "") -> list[dict]:
    """All orders for one account, following pagination. robin_stocks'
    find_stock_orders() resolves one symbol at a time and misses most history,
    which made the reconciliation report 1 broker fill against 30 local ones."""
    out: list[dict] = []
    url = f"https://api.robinhood.com/orders/?account_number={account_number}"
    seen = set()
    while url and url not in seen:
        seen.add(url)
        data = rh.helper.request_get(url, "regular")
        if not isinstance(data, dict):
            break
        stop = False
        for o in data.get("results") or []:
            ts = (o.get("last_transaction_at") or o.get("created_at") or "")[:10]
            if since and ts and ts < since:
                stop = True          # results are newest-first
                continue
            out.append(o)
        if stop:
            break
        url = data.get("next")
    return out


def get_executor() -> tuple[OrderExecutor, str]:
    """Executor + human-readable label, chosen by TRADING_EXECUTOR env var.
    Default is paper: simulated fills, no credentials, no real money."""
    kind = os.environ.get("TRADING_EXECUTOR", "paper").strip().lower()
    if kind == "robinhood":
        return RobinhoodExecutor(), "ROBINHOOD — REAL MONEY"
    return PaperExecutor(), "paper (simulated fills)"
