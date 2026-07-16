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
AUTO_TICKET = os.environ.get("SCAN_AUTO_TICKET", "1") == "1"

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
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{text}" with title "{title}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def _record(sig: dict) -> None:
    os.makedirs(PRIVATE, exist_ok=True)
    with open(SIGNALS, "a") as f:
        f.write(json.dumps(sig) + "\n")


class RangeCache:
    """52-week lows/highs per symbol, refreshed once per day (one batch call)."""

    def __init__(self):
        self.day = None
        self.lo_hi: dict[str, tuple[float, float]] = {}

    def refresh_if_needed(self, symbols: list[str]) -> None:
        if self.day == date.today():
            return
        import yfinance as yf
        df = yf.download(symbols, period="1y", interval="1d",
                         group_by="ticker", progress=False, threads=True)
        self.lo_hi = {}
        for s in symbols:
            try:
                c = df[s]["Close"].dropna()
                if len(c) > 60:
                    self.lo_hi[s] = (float(c.min()), float(c.max()))
            except Exception:
                continue
        self.day = date.today()


def scan(cache: RangeCache) -> list[dict]:
    """One pass: fetch latest prices, apply trigger, emit new signals."""
    import yfinance as yf
    symbols = _symbols()
    cache.refresh_if_needed(symbols)
    already = _signaled_today()

    df = yf.download(symbols, period="2d", interval="1d",
                     group_by="ticker", progress=False, threads=True)
    fired = []
    for s in symbols:
        if s in already or s not in cache.lo_hi:
            continue
        try:
            closes = df[s]["Close"].dropna()
            if len(closes) < 2:
                continue
            prev, last = float(closes.iloc[-2]), float(closes.iloc[-1])
        except Exception:
            continue
        day_chg = last / prev - 1
        lo, hi = cache.lo_hi[s]
        rp = (last - lo) / (hi - lo) if hi > lo else None
        if rp is None:
            continue
        if day_chg <= -DROP_PCT / 100 and rp <= RANGE_MAX:
            sig = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "symbol": s, "price": round(last, 2),
                "day_change": round(day_chg, 4), "range_pos": round(rp, 3),
                "rule": f"down>={DROP_PCT}% & range<={RANGE_MAX:.0%}",
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
            fired.append(sig)
            print(f"  SIGNAL {s}: {day_chg:+.1%} today, range {rp:.0%} "
                  f"-> notified{' + ticket' if AUTO_TICKET else ''}")
    return fired


def run_forever() -> None:
    print(f"Intraday scanner: every {SCAN_INTERVAL}s | trigger: "
          f"down>={DROP_PCT}% & range<={RANGE_MAX:.0%} | auto-ticket: {AUTO_TICKET}")
    print("Signals notify + build governor-checked tickets. Never executes.")
    cache = RangeCache()
    while True:
        if market_open():
            try:
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
