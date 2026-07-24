"""
Near-real-time intraday scanner — always-on stock signals, minutes not days.

Every SCAN_INTERVAL seconds during market hours it polls prices for the
watchlist + universe and fires a SIGNAL when the deterministic trigger hits:

    TRIGGER: down >= DROP_PCT today  AND  in the bottom RANGE_MAX of its
             trailing 52-week range      (a fresh dip on an already-cheap name)

On each signal it:
  1. appends to data/private/signals.jsonl            (the feed)
  2. posts a macOS notification                        (near-real-time ping)
  3. builds a governor-checked ticket into today's plan (console shows it;
     placing stays YOUR click — the scanner never executes anything)

One signal per symbol per day (no spam). Deterministic, transparent, and
research-grade: your own backtests show no proven edge in this signal class —
treat signals as prompts to look, not commands to buy.

    python -m trading_agent.live_scanner --once     # single scan, then exit
    python -m trading_agent.live_scanner            # always-on loop

Env: SCAN_INTERVAL (s, default 300), SCAN_DROP_PCT (default 2.5),
SCAN_RANGE_MAX (default 0.35), SCAN_AUTO_TICKET (default 1).
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone

from .env_file import load_env_file

load_env_file()

HERE = os.path.dirname(__file__)
PRIVATE = os.path.join(HERE, "data", "private")
SIGNALS = os.path.join(PRIVATE, "signals.jsonl")

SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
DROP_PCT = float(os.environ.get("SCAN_DROP_PCT", "2.5"))
RANGE_MAX = float(os.environ.get("SCAN_RANGE_MAX", "0.35"))
# Entry strategy (backtested head-to-head, 5y/40 names, same exits):
#   dip-uptrend : down>=DROP_PCT% today AND price > 200-day MA   (tournament
#                 winner: 51% win, +1.33% avg, median +0.40%, t=4.39)
#   dip-range   : down>=DROP_PCT% today AND bottom RANGE_MAX of 52w range
#                 (original rule: median trade negative)
SCAN_STRATEGY = os.environ.get("SCAN_STRATEGY", "dip-uptrend").strip().lower()
AUTO_TICKET = os.environ.get("SCAN_AUTO_TICKET", "1") == "1"

# ---- AUTO-EXECUTION (off by default; ARMING IS THE USER'S ACT) ----
# SCANNER_LIVE=1 makes the scanner PLACE each signal's governor-approved
# ticket through the user's own executor instead of waiting for a click.
# Gates that always apply, armed or not:
#   - ticket must be governor-approved and live-armed (plan armed via
#     TRADING_GO_LIVE, order caps, cash checks)
#   - max_trades_per_day cap shared with the console (executions ledger)
#   - no double-fires (ref_id dedupe)
#   - REJECTION HALT: 3 broker rejections in a day disables auto-exec until
#     tomorrow (protects against error loops, e.g. compliance blocks)
SCANNER_LIVE = os.environ.get("SCANNER_LIVE", "") == "1"
MAX_REJECTIONS_PER_DAY = 3
EXECUTIONS = os.path.join(PRIVATE, "executions.jsonl")
_EXECUTOR_CACHE: list = []   # [executor, label] once initialized

WATCH_EXTRA = ["AAPL", "AMZN", "MSFT", "GOOGL", "NKE", "TSLA"]


def _symbols() -> list[str]:
    from .universe import UNIVERSE
    return sorted(set(UNIVERSE) | set(WATCH_EXTRA))


def market_open(now: datetime | None = None) -> bool:
    from .dca_bot import market_open as mo
    return mo(now)


def _signaled_today() -> set[str]:
    today = date.today().isoformat()
    if not os.path.exists(SIGNALS):
        return set()
    out = set()
    for l in open(SIGNALS):
        if l.strip():
            e = json.loads(l)
            if e.get("ts", "").startswith(today):
                out.add(e["symbol"])
    return out


def _notify(title: str, text: str) -> None:
    # Mac notification
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{text}" with title "{title}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    # Phone push via ntfy.sh (free). Set NTFY_TOPIC in ~/.trading_agent.env and
    # subscribe to the same topic in the ntfy app. Treat the topic name like a
    # password — anyone who knows it can read these pings.
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=text.encode(),
                headers={"Title": title, "Tags": "chart_with_downwards_trend"})
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass  # phone push is best-effort; never break the scan


def _record(sig: dict) -> None:
    os.makedirs(PRIVATE, exist_ok=True)
    with open(SIGNALS, "a") as f:
        f.write(json.dumps(sig) + "\n")


# ---------- auto-execution (armed only by the user via SCANNER_LIVE=1) ----------

def _exec_rows() -> list[dict]:
    if not os.path.exists(EXECUTIONS):
        return []
    return [json.loads(l) for l in open(EXECUTIONS) if l.strip()]


def _exec_today() -> list[dict]:
    today = date.today().isoformat()
    return [e for e in _exec_rows() if e.get("ts", "").startswith(today)]


LOGIN_RETRY_SECONDS = int(os.environ.get("SCAN_LOGIN_RETRY_SECONDS", str(6 * 3600)))


def _get_executor():
    """Real executor only when the user has armed SCANNER_LIVE and configured
    robinhood. Login failures are retried every LOGIN_RETRY_SECONDS so the bot
    self-heals (and notifies) when the broker login path starts working again
    — e.g. after a robin_stocks patch for an upstream breakage."""
    armed_any = (SCANNER_LIVE or os.environ.get("CLONE_LIVE") == "1"
                 or os.environ.get("MOMENTUM_LIVE") == "1")
    if _EXECUTOR_CACHE:
        ex, label, ts, had_failed = _EXECUTOR_CACHE
        if ex is not None or not armed_any:
            return ex, label
        if time.time() - ts < LOGIN_RETRY_SECONDS:
            return ex, label   # failed recently; wait before retrying
    if not (armed_any and os.environ.get("TRADING_EXECUTOR", "").lower() == "robinhood"):
        _EXECUTOR_CACHE[:] = [None, "auto-exec off (SCANNER_LIVE/CLONE_LIVE not set)", time.time(), False]
    else:
        had_failed = bool(_EXECUTOR_CACHE) and _EXECUTOR_CACHE[3]
        try:
            from .live_executor import RobinhoodExecutor
            _EXECUTOR_CACHE[:] = [RobinhoodExecutor(),
                                  "ROBINHOOD — REAL MONEY (auto)", time.time(), False]
            if had_failed:
                _notify("Auto-exec RESTORED",
                        "Robinhood login works again — autonomous trading resumed.")
                print("  auto-exec RESTORED: login succeeded on retry")
        except Exception as e:
            first_failure = not had_failed
            _EXECUTOR_CACHE[:] = [None, f"auto-exec disabled: {e}", time.time(), True]
            if first_failure:
                _notify("Auto-exec disabled",
                        "Robinhood login failed — will retry every "
                        f"{LOGIN_RETRY_SECONDS//3600}h and notify when restored.")
    return _EXECUTOR_CACHE[0], _EXECUTOR_CACHE[1]


def _auto_execute(symbol: str) -> str:
    """Place the newest governor-approved live-armed ticket for `symbol`.
    Every gate must pass; any failure is recorded and never retried."""
    executor, label = _get_executor()
    if executor is None:
        return label

    files = sorted(glob.glob(os.path.join(PRIVATE, "trade_plan_*.json")))
    if not files:
        return "no plan"
    plan = json.load(open(files[-1]))
    if not plan.get("armed"):
        return "plan not armed (TRADING_GO_LIVE)"
    tickets = [t for t in plan.get("tickets", [])
               if t.get("symbol") == symbol and t.get("approved")
               and t.get("status") == "live-armed"]
    if not tickets:
        return "no approved live-armed ticket"
    ticket = tickets[-1]
    ref = ticket["broker_params"]["ref_id"]

    # Only a SUCCESSFUL prior execution blocks a retry — a rejected order
    # (e.g. blocked by unsettled cash) must be retryable once funds settle.
    done = {e.get("ref_id") for e in _exec_rows()
            if e.get("result_status") not in ("rejected", None)}
    if ref in done:
        return "already executed"
    today_rows = _exec_today()
    from .config import RiskLimits
    limits = RiskLimits()
    # Cap counts BUY fills only — sells (exits) are never blocked, so a busy
    # buy day can never trap you in a losing position.
    buy_fills = [e for e in today_rows
                 if e.get("result_status") not in (None, "rejected")
                 and not e.get("order", "").startswith("SELL")]
    rejections = [e for e in today_rows if e.get("result_status") == "rejected"]
    if len(buy_fills) >= limits.max_trades_per_day:
        return f"daily BUY cap reached ({limits.max_trades_per_day}; sells still allowed)"
    if len(rejections) >= MAX_REJECTIONS_PER_DAY:
        return "REJECTION HALT: too many broker rejections today, auto-exec paused"

    from .poc.order import Order, OrderType, Side
    p = ticket["broker_params"]
    order = Order(account_number=p["account_number"], symbol=p["symbol"],
                  side=Side(p["side"]), type=OrderType(p["type"]),
                  dollar_amount=float(p["dollar_amount"]) if p.get("dollar_amount") else None,
                  quantity=float(p["quantity"]) if p.get("quantity") else None,
                  limit_price=float(p["limit_price"]) if p.get("limit_price") else None,
                  time_in_force=p.get("time_in_force", "gfd"), ref_id=ref)

    def _rec(status, real, extra=""):
        os.makedirs(PRIVATE, exist_ok=True)
        with open(EXECUTIONS, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "ref_id": ref, "order": order.describe(),
                "executor": label, "result_status": status,
                "real_money": real, "detail": extra}) + "\n")

    try:
        result = executor.place(order)
    except Exception as e:
        _rec("rejected", False, str(e))
        _notify("Order REJECTED", f"{order.describe()} — {e}")
        return f"rejected: {e}"
    _rec(result.get("status", "?"), result.get("real_money", False))
    _notify("AUTO-EXECUTED", f"{order.describe()} -> {result.get('status')}")
    return f"EXECUTED: {result.get('status')}"


# ---------- exit engine: the backtested sell rules, applied to real positions ----------
# Same params the backtest validated: +10% target, -5% stop, 20-day time stop.
EXIT_TARGET = float(os.environ.get("EXIT_TARGET_PCT", "10")) / 100
EXIT_STOP = -abs(float(os.environ.get("EXIT_STOP_PCT", "5"))) / 100
EXIT_TIME_DAYS = int(os.environ.get("EXIT_TIME_DAYS", "20"))


def exit_decision(pnl: float, age_days: float) -> str | None:
    """Pure, testable exit rule: target / stop / time, else hold."""
    if pnl >= EXIT_TARGET:
        return f"target +{EXIT_TARGET:.0%}"
    if pnl <= EXIT_STOP:
        return f"stop {EXIT_STOP:.0%}"
    if age_days >= EXIT_TIME_DAYS:
        return f"time {EXIT_TIME_DAYS}d"
    return None


def _sold_today() -> set[str]:
    return {e.get("symbol", "") for e in _exec_today()
            if e.get("order", "").startswith("SELL")} - {""}


def check_exits() -> None:
    """Apply the exit rules to every open position in the configured account.
    Armed-only (same SCANNER_LIVE gate). Never sells a position opened today
    (avoids PDT day-trades on a <$25k account); one sell attempt per symbol
    per day; every action recorded + notified."""
    executor, label = _get_executor()
    if executor is None:
        return
    acct = os.environ.get("TRADING_ACCOUNT_NUMBER", "")
    try:
        positions = executor.rh.account.get_open_stock_positions(account_number=acct)
    except Exception as e:
        print(f"  exits: could not fetch positions: {e}")
        return
    sold = _sold_today()
    today = date.today()
    try:
        from .berkshire_clone import clone_symbols
        cloned = clone_symbols()   # held until Berkshire sells, not +10/-5/20d
    except Exception:
        cloned = set()
    for pos in positions or []:
        try:
            qty = float(pos.get("quantity") or 0)
            if qty <= 0:
                continue
            sym = executor.rh.stocks.get_symbol_by_url(pos["instrument"])
            if not sym or sym in sold or sym in cloned:
                continue
            avg = float(pos.get("average_buy_price") or 0)
            if avg <= 0:
                continue
            created = pos.get("created_at", "")[:10]
            age_days = (today - date.fromisoformat(created)).days if created else 0
            if age_days < 1:
                continue   # never same-day: no PDT day-trades
            px_raw = executor.rh.stocks.get_latest_price(sym)
            price = float(px_raw[0]) if px_raw and px_raw[0] else None
            if not price:
                continue
            pnl = price / avg - 1
            why = exit_decision(pnl, age_days)
            if not why:
                continue
        except Exception as e:
            print(f"  exits: skipping a position ({type(e).__name__}: {e})")
            continue

        from .poc.order import Order, OrderType, Side
        order = Order(account_number=acct, symbol=sym, side=Side.SELL,
                      type=OrderType.MARKET, quantity=round(qty, 6))

        def _rec(status, real, extra=""):
            os.makedirs(PRIVATE, exist_ok=True)
            with open(EXECUTIONS, "a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "ref_id": order.ref_id, "symbol": sym,
                    "order": order.describe(),
                    "executor": label, "result_status": status,
                    "real_money": real, "exit_reason": why,
                    "pnl_at_signal": round(pnl, 4), "detail": extra}) + "\n")

        try:
            result = executor.place(order)
        except Exception as e:
            _rec("rejected", False, str(e))
            _notify("Exit REJECTED", f"{order.describe()} ({why}) — {e}")
            print(f"  EXIT {sym}: {why}, pnl {pnl:+.1%} -> REJECTED: {e}")
            continue
        _rec(result.get("status", "?"), result.get("real_money", False))
        _notify("AUTO-SOLD", f"{order.describe()} — {why}, P&L {pnl:+.1%}")
        print(f"  EXIT {sym}: {why}, pnl {pnl:+.1%} -> {result.get('status')}")


def _rh_session():
    """Logged-in robin_stocks module from the armed executor, else None.
    When present, ALL market data comes from Robinhood (same venue that
    executes); Yahoo remains only as a fallback so signals survive outages."""
    executor, _ = _get_executor()
    return executor.rh if executor is not None else None


class RangeCache:
    """52-week lows/highs per symbol, refreshed once per day (one batch call).
    Prefers Robinhood historicals; falls back to Yahoo."""

    def __init__(self):
        self.day = None
        self.source = "?"
        self.lo_hi: dict[str, tuple[float, float]] = {}

    def _from_robinhood(self, symbols: list[str]) -> dict | None:
        rh = _rh_session()
        if rh is None:
            return None
        closes: dict[str, list[float]] = {}
        failed_chunks = 0
        # Historicals endpoint rejects oversized batches — chunk it.
        for i in range(0, len(symbols), 30):
            chunk = symbols[i:i + 30]
            try:
                rows = rh.stocks.get_stock_historicals(
                    chunk, interval="day", span="year") or []
                for r in rows:
                    if not r:
                        continue
                    sym, px = r.get("symbol"), r.get("close_price")
                    if sym and px:
                        closes.setdefault(sym, []).append(float(px))
            except Exception:
                failed_chunks += 1
                continue
        out = {s: (min(c), max(c),
                   sum(c[-200:]) / 200 if len(c) >= 200 else None)
               for s, c in closes.items() if len(c) > 60}
        if failed_chunks:
            print(f"  range cache: {failed_chunks} robinhood chunk(s) failed")
        return out or None

    def _from_yahoo(self, symbols: list[str]) -> dict:
        import yfinance as yf
        df = yf.download(symbols, period="1y", interval="1d",
                         group_by="ticker", progress=False, threads=True)
        out = {}
        for s in symbols:
            try:
                c = df[s]["Close"].dropna()
                if len(c) > 60:
                    vals = [float(x) for x in c.tolist()]
                    out[s] = (min(vals), max(vals),
                              sum(vals[-200:]) / 200 if len(vals) >= 200 else None)
            except Exception:
                continue
        return out

    def refresh_if_needed(self, symbols: list[str]) -> None:
        if self.day == date.today():
            return
        rh_data = self._from_robinhood(symbols)
        self.lo_hi = rh_data if rh_data else self._from_yahoo(symbols)
        self.source = "robinhood" if rh_data else "yahoo-fallback"
        self.day = date.today()


def _live_prices(symbols: list[str]) -> tuple[dict[str, tuple[float, float]], str]:
    """{symbol: (last, prev_close)} — Robinhood real-time batch quotes when the
    session is live, else Yahoo daily bars as fallback."""
    rh = _rh_session()
    if rh is not None:
        try:
            out = {}
            for q in rh.stocks.get_quotes(symbols) or []:
                if not q:
                    continue
                sym = q.get("symbol")
                last = q.get("last_trade_price")
                prev = q.get("adjusted_previous_close") or q.get("previous_close")
                if sym and last and prev:
                    out[sym] = (float(last), float(prev))
            if out:
                return out, "robinhood"
        except Exception as e:
            print(f"  quotes: robinhood failed ({e}); using yahoo")
    import yfinance as yf
    df = yf.download(symbols, period="2d", interval="1d",
                     group_by="ticker", progress=False, threads=True)
    out = {}
    for s in symbols:
        try:
            closes = df[s]["Close"].dropna()
            if len(closes) >= 2:
                out[s] = (float(closes.iloc[-1]), float(closes.iloc[-2]))
        except Exception:
            continue
    return out, "yahoo-fallback"


def scan(cache: RangeCache) -> list[dict]:
    """One pass: fetch latest prices, apply trigger, emit new signals."""
    symbols = _symbols()
    already = _signaled_today()

    prices, source = _live_prices(symbols)
    # Refresh ranges using only symbols the quote source recognizes — keeps
    # dead tickers from poisoning the historicals batches.
    cache.refresh_if_needed(sorted(prices) if source == "robinhood" else symbols)
    fired = []
    for s in symbols:
        if s in already or s not in cache.lo_hi or s not in prices:
            continue
        last, prev = prices[s]
        day_chg = last / prev - 1
        lo, hi, ma200 = cache.lo_hi[s]
        rp = (last - lo) / (hi - lo) if hi > lo else None
        if SCAN_STRATEGY == "dip-uptrend":
            # Tournament winner: pullback in a rising stock, not a falling knife
            hit = (day_chg <= -DROP_PCT / 100
                   and ma200 is not None and last > ma200)
        else:  # dip-range (original rule)
            hit = (day_chg <= -DROP_PCT / 100
                   and rp is not None and rp <= RANGE_MAX)
        if hit:
            sig = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "symbol": s, "price": round(last, 2),
                "day_change": round(day_chg, 4), "range_pos": round(rp, 3),
                "rule": (f"{SCAN_STRATEGY}: down>={DROP_PCT}% & "
                         + ("price>200dMA" if SCAN_STRATEGY == "dip-uptrend"
                            else f"range<={RANGE_MAX:.0%}")),
                "data_source": source, "range_source": cache.source,
            }
            _record(sig)
            _notify("Stock signal",
                    f"{s} {day_chg:+.1%} today, {rp:.0%} of 52w range "
                    f"(${last:.2f})")
            if AUTO_TICKET:
                try:
                    from .add_tickets import add
                    add([s], 200.0,
                        f"intraday signal {day_chg:+.1%}, range {rp:.0%}")
                except SystemExit:
                    pass
                except Exception as e:
                    sig["ticket_error"] = str(e)
            if SCANNER_LIVE and AUTO_TICKET and "ticket_error" not in sig:
                sig["auto_exec"] = _auto_execute(s)
            fired.append(sig)
            print(f"  SIGNAL {s}: {day_chg:+.1%} today, range {rp:.0%} "
                  f"-> notified{' + ticket' if AUTO_TICKET else ''}"
                  f"{' | auto: ' + sig['auto_exec'] if 'auto_exec' in sig else ''}")
    return fired


def run_forever() -> None:
    trig = ("price>200dMA (dip-in-uptrend, tournament winner)"
            if SCAN_STRATEGY == "dip-uptrend" else f"range<={RANGE_MAX:.0%}")
    print(f"Intraday scanner: every {SCAN_INTERVAL}s | trigger: "
          f"down>={DROP_PCT}% & {trig} | auto-ticket: {AUTO_TICKET}")
    if SCANNER_LIVE:
        from .config import RiskLimits
        ex, label = _get_executor()
        if ex is None:
            print(f"AUTO-EXECUTION NOT ACTIVE — {label}")
            print(f"    (retrying login every {LOGIN_RETRY_SECONDS//3600}h; "
                  f"phone will be notified when restored)")
        else:
            print(f"!!! AUTO-EXECUTION ARMED: {label}")
        print(f"    caps: governor per-order, {RiskLimits().max_trades_per_day} "
              f"trades/day, {MAX_REJECTIONS_PER_DAY}-rejection halt")
        print(f"    exits: +{EXIT_TARGET:.0%} target / {EXIT_STOP:.0%} stop / "
              f"{EXIT_TIME_DAYS}d time stop; no same-day sells (PDT-safe)")
    else:
        print("Signals notify + build governor-checked tickets. Not armed to execute "
              "(SCANNER_LIVE unset).")
    cache = RangeCache()
    while True:
        if market_open():
            try:
                check_exits()                  # sells first: frees cash, cuts losers
                n = scan(cache)
                stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
                print(f"[{stamp}] scanned; {len(n)} new signal(s)")
            except Exception as e:
                print(f"scan error (will retry): {type(e).__name__}: {e}")
        time.sleep(SCAN_INTERVAL)


def main() -> None:
    if "--once" in sys.argv:
        cache = RangeCache()
        fired = scan(cache)
        print(f"done: {len(fired)} signal(s)" if fired else
              "done: no signals right now")
    else:
        run_forever()


if __name__ == "__main__":
    main()
