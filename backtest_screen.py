"""
Backtest the quality-dip SCREEN rule on real history.

The live screen flags a name when it's in the bottom 30% of its trailing
52-week range (the earnings-beat filter is left out here — point-in-time
beat-rate history isn't reliably available from free data, and the range
signal is the core driver). This tests that core signal honestly:

  - Universe: a basket of large caps (edit below).
  - Entry: when range position <= 0.30 and not already holding, buy.
  - Exit: +10% target, -5% stop, or a 20-trading-day time stop.
  - Benchmark: SPY buy-and-hold over the same window, and equal-weight
    buy-and-hold of the same basket.

Prints per-strategy stats vs. benchmarks so you can see whether the rule
actually beats just holding the market. Not advice — evidence.

    python -m trading_agent.backtest_screen
"""
from __future__ import annotations

import statistics as st

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "JNJ", "PG", "KO",
    "PEP", "WMT", "HD", "CVX", "XOM", "BAC", "AXP", "MCD", "NKE", "DIS",
    "CSCO", "INTC", "VZ", "T", "CMCSA", "ADBE", "CRM", "ACN", "MRK", "PFE",
    "ABBV", "TMO", "COST", "AMD", "QCOM", "TXN", "CAT", "GE", "BA", "CME",
]
YEARS = "5y"
RANGE_WINDOW = 252
MAX_RANGE_POS = 0.30
PROFIT_TARGET = 0.10
STOP_LOSS = -0.05
TIME_STOP = 20      # trading days


def _range_pos(window: list[float], price: float) -> float | None:
    lo, hi = min(window), max(window)
    return (price - lo) / (hi - lo) if hi > lo else None


def run() -> None:
    import yfinance as yf
    print(f"Downloading {len(UNIVERSE)} names + SPY ({YEARS})...")
    data = yf.download(UNIVERSE + ["SPY"], period=YEARS, interval="1d",
                       group_by="ticker", progress=False, threads=True)

    closes: dict[str, list[float]] = {}
    for s in UNIVERSE + ["SPY"]:
        try:
            c = data[s]["Close"].dropna().tolist()
            if len(c) > RANGE_WINDOW + TIME_STOP:
                closes[s] = [float(x) for x in c]
        except Exception:
            continue

    trades: list[tuple[str, float, float, str]] = []   # sym, entry, exit, reason
    for s, c in closes.items():
        if s == "SPY":
            continue
        i = RANGE_WINDOW
        while i < len(c):
            window = c[i - RANGE_WINDOW:i]
            rp = _range_pos(window, c[i])
            if rp is not None and rp <= MAX_RANGE_POS:
                entry = c[i]
                exit_px, reason = None, None
                for j in range(i + 1, min(i + 1 + TIME_STOP, len(c))):
                    r = c[j] / entry - 1
                    if r >= PROFIT_TARGET:
                        exit_px, reason = c[j], "target"; i = j; break
                    if r <= STOP_LOSS:
                        exit_px, reason = c[j], "stop"; i = j; break
                if exit_px is None:
                    end = min(i + TIME_STOP, len(c) - 1)
                    exit_px, reason = c[end], "time"; i = end
                trades.append((s, entry, exit_px, reason))
            i += 1

    rets = [ex / en - 1 for _, en, ex, _ in trades]
    n = len(rets)
    print("=" * 70)
    print(f"QUALITY-DIP SCREEN BACKTEST — {len(closes)-1} names, {YEARS}")
    print(f"entry: range<= {MAX_RANGE_POS:.0%} | exit: +{PROFIT_TARGET:.0%} / "
          f"{STOP_LOSS:.0%} / {TIME_STOP}d")
    print("=" * 70)
    if n:
        wins = sum(1 for r in rets if r > 0)
        avg = st.mean(rets)
        sd = st.pstdev(rets)
        tstat = avg / (sd / n ** 0.5) if sd else 0
        by_reason: dict[str, int] = {}
        for _, _, _, why in trades:
            by_reason[why] = by_reason.get(why, 0) + 1
        print(f"Signals/trades:    {n}")
        print(f"Win rate:          {wins/n:.0%}")
        print(f"Avg per trade:     {avg:+.2%}")
        print(f"Median per trade:  {st.median(rets):+.2%}")
        print(f"t-stat:            {tstat:+.2f}  ({'significant' if abs(tstat)>2 else 'NOT significant'})")
        print(f"Exit mix:          {by_reason}")
    else:
        print("No signals fired.")

    print("-" * 70)
    # Benchmarks over the same window
    bh = [closes[s][-1] / closes[s][RANGE_WINDOW] - 1 for s in closes if s != "SPY"]
    if "SPY" in closes:
        spy = closes["SPY"][-1] / closes["SPY"][RANGE_WINDOW] - 1
        print(f"SPY buy-and-hold (same window):        {spy:+.1%}")
    print(f"Basket equal-weight buy-and-hold:      {sum(bh)/len(bh):+.1%}")
    print("=" * 70)
    print("Each 'trade' return is per-position, not compounded; treat win rate +")
    print("avg/trade vs. buy-and-hold as the honest signal. Earnings filter NOT")
    print("included (data limits) — the live screen adds it on top.")


if __name__ == "__main__":
    run()
