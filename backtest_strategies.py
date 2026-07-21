"""
Strategy tournament — the leading retail-bot entry rules, tested head-to-head.

Five deterministic entry rules (the current live rule + four from the 2026
algo-trading literature), all with the SAME exits the live bot uses
(+10% target / -5% stop / 20-day time stop), on the same ~40-name universe
over ~5 years, vs buy-and-hold benchmarks.

  A. live-dip        : down >=2.5% today AND bottom 35% of 52w range (CURRENT)
  B. dip-in-uptrend  : down >=2.5% today AND price > 200-day MA
  C. 52w-high-momo   : within 2% of 52-week high (classic momentum anchor)
  D. rsi2-reversion  : RSI(2) < 10 AND price > 200-day MA (Connors-style)
  E. regime-dip      : rule A, but only while SPY > its 200-day MA

    python -m trading_agent.backtest_strategies
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
WINDOW = 252          # 52-week lookback
TARGET, STOP, TDAYS = 0.10, -0.05, 20


def rsi(closes: list[float], period: int = 2) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    avg_g = avg_l = None
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        g, l = max(chg, 0.0), max(-chg, 0.0)
        if i == period:
            gains = [max(closes[k] - closes[k - 1], 0.0) for k in range(1, period + 1)]
            losses = [max(closes[k - 1] - closes[k], 0.0) for k in range(1, period + 1)]
            avg_g, avg_l = sum(gains) / period, sum(losses) / period
        elif i > period:
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
        if avg_g is not None:
            out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def sma(closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def simulate(closes: list[float], entry_ok) -> list[float]:
    """Walk the series; on entry signal, apply the live exits; no overlap."""
    rets, i = [], WINDOW
    while i < len(closes) - 1:
        if entry_ok(i):
            entry = closes[i]
            exit_px = None
            for j in range(i + 1, min(i + 1 + TDAYS, len(closes))):
                r = closes[j] / entry - 1
                if r >= TARGET or r <= STOP:
                    exit_px, i = closes[j], j
                    break
            if exit_px is None:
                i = min(i + TDAYS, len(closes) - 1)
                exit_px = closes[i]
            rets.append(exit_px / entry - 1)
        i += 1
    return rets


def run() -> None:
    import yfinance as yf
    print(f"Downloading {len(UNIVERSE)} names + SPY ({YEARS})...")
    data = yf.download(UNIVERSE + ["SPY"], period=YEARS, interval="1d",
                       group_by="ticker", progress=False, threads=True)
    series: dict[str, list[float]] = {}
    for s in UNIVERSE + ["SPY"]:
        try:
            c = [float(x) for x in data[s]["Close"].dropna().tolist()]
            if len(c) > WINDOW + TDAYS:
                series[s] = c
        except Exception:
            continue

    spy = series.get("SPY", [])
    spy_ma = sma(spy, 200)
    spy_regime = [bool(m and p > m) for p, m in zip(spy, spy_ma)]

    def strategies_for(c: list[float]):
        ma200 = sma(c, 200)
        r2 = rsi(c, 2)

        def lo_hi(i):
            w = c[i - WINDOW:i]
            return min(w), max(w)

        def a(i):  # current live rule
            lo, hi = lo_hi(i)
            rp = (c[i] - lo) / (hi - lo) if hi > lo else None
            return (c[i] / c[i - 1] - 1 <= -0.025) and rp is not None and rp <= 0.35

        def b(i):  # dip in uptrend
            return (c[i] / c[i - 1] - 1 <= -0.025) and ma200[i] and c[i] > ma200[i]

        def c_momo(i):  # near 52w high
            _, hi = lo_hi(i)
            return c[i] >= hi * 0.98

        def d(i):  # RSI(2) reversion in uptrend
            return r2[i] is not None and r2[i] < 10 and ma200[i] and c[i] > ma200[i]

        def e(i):  # regime-gated current rule (align by index ratio; approx)
            k = min(i, len(spy_regime) - 1)
            return spy_regime[k] and a(i)

        return {"A live-dip (current)": a, "B dip-in-uptrend": b,
                "C 52w-high-momo": c_momo, "D rsi2-reversion": d,
                "E regime-dip": e}

    results: dict[str, list[float]] = {}
    for s, c in series.items():
        if s == "SPY":
            continue
        for name, fn in strategies_for(c).items():
            results.setdefault(name, []).extend(simulate(c, fn))

    print("=" * 78)
    print(f"STRATEGY TOURNAMENT — {len(series)-1} names, {YEARS}, exits "
          f"+{TARGET:.0%}/{STOP:.0%}/{TDAYS}d for ALL strategies")
    print("=" * 78)
    print(f"{'strategy':<24}{'trades':>7}{'win%':>7}{'avg':>8}{'median':>9}{'t':>7}")
    print("-" * 78)
    for name in sorted(results):
        rets = results[name]
        n = len(rets)
        if not n:
            print(f"{name:<24}{0:>7}")
            continue
        wins = sum(1 for r in rets if r > 0) / n
        avg, med = st.mean(rets), st.median(rets)
        sd = st.pstdev(rets)
        t = avg / (sd / n ** 0.5) if sd else 0
        print(f"{name:<24}{n:>7}{wins:>7.0%}{avg:>8.2%}{med:>9.2%}{t:>7.2f}")
    print("-" * 78)
    bh = [series[s][-1] / series[s][WINDOW] - 1 for s in series if s != "SPY"]
    if spy:
        print(f"SPY buy-and-hold same window:    {spy[-1]/spy[WINDOW]-1:+.1%}")
    print(f"Basket buy-and-hold same window: {sum(bh)/len(bh):+.1%}")
    print("=" * 78)
    print("avg/trade is per-position (not compounded). Positive t>2 = edge is")
    print("statistically real; whether it beats buy-and-hold is the money question.")


if __name__ == "__main__":
    run()
