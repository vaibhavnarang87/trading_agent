"""
Portfolio strategy tournament — long-only, cash-account-runnable strategies,
monthly rebalanced, measured on total return / CAGR / vol / Sharpe / max
drawdown vs buy-and-hold.

Strategies (all long-only, no shorting/leverage/options):
  BH-SPY        : buy & hold SPY (benchmark)
  BH-Basket     : equal-weight buy & hold the stock basket (benchmark)
  Mom 12-1      : monthly, hold top-N by trailing 12m return (skip last month)
  Dual-Momentum : Mom 12-1 while SPY's 12m return > 0, else cash (BIL)
  Low-Vol       : monthly, hold N lowest-volatility names (trailing 6m stdev)
  Sector-Rot    : monthly, hold top-K sector ETFs by trailing 6m return

Honesty guards: decisions use data through month t, returns applied for t+1
(no look-ahead); costs modeled at 5 bps per rebalance turnover; a Sharpe > 2
is flagged as suspicious per the literature.

    python -m trading_agent.backtest_portfolio
"""
from __future__ import annotations

import statistics as st

STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "JNJ", "PG", "KO",
    "PEP", "WMT", "HD", "CVX", "XOM", "BAC", "AXP", "MCD", "NKE", "DIS",
    "CSCO", "INTC", "VZ", "T", "CMCSA", "ADBE", "CRM", "ACN", "MRK", "PFE",
    "ABBV", "TMO", "COST", "AMD", "QCOM", "TXN", "CAT", "GE", "BA", "LLY",
]
SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC"]
YEARS = "8y"
TOP_N = 10
TOP_K_SECTOR = 3
COST_BPS = 5 / 10000        # per unit of turnover


def _month_end(dates, closes):
    """Return (month_labels, {sym: [month-end closes]}) resampled from daily."""
    import pandas as pd
    idx = pd.to_datetime(dates)
    out = {}
    for s, c in closes.items():
        ser = pd.Series(c, index=idx).resample("ME").last().dropna()
        out[s] = ser
    common = None
    for ser in out.values():
        common = ser.index if common is None else common.intersection(ser.index)
    months = sorted(common)
    return months, {s: ser.reindex(months) for s, ser in out.items()}


def _metrics(monthly_rets: list[float]) -> dict:
    if not monthly_rets:
        return {}
    eq = 1.0
    curve = []
    for r in monthly_rets:
        eq *= (1 + r)
        curve.append(eq)
    yrs = len(monthly_rets) / 12
    cagr = eq ** (1 / yrs) - 1 if yrs > 0 else 0
    vol = st.pstdev(monthly_rets) * (12 ** 0.5)
    mean_a = st.mean(monthly_rets) * 12
    sharpe = mean_a / vol if vol else 0
    peak = -1e9
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"total": eq - 1, "cagr": cagr, "vol": vol,
            "sharpe": sharpe, "mdd": mdd}


def run() -> None:
    import numpy as np
    import pandas as pd
    import yfinance as yf

    all_syms = STOCKS + SECTORS + ["SPY", "BIL"]
    print(f"Downloading {len(all_syms)} symbols ({YEARS})...")
    data = yf.download(all_syms, period=YEARS, interval="1d",
                       group_by="ticker", progress=False, threads=True)
    closes, dates = {}, None
    for s in all_syms:
        try:
            ser = data[s]["Close"].dropna()
            if len(ser) > 300:
                closes[s] = ser.tolist()
                dates = ser.index if dates is None else dates
        except Exception:
            continue
    # align all on SPY's daily index
    spy_idx = data["SPY"]["Close"].dropna().index
    aligned = {}
    for s in list(closes):
        ser = data[s]["Close"].reindex(spy_idx).ffill().dropna()
        aligned[s] = ser.tolist()
    months, m = _month_end(spy_idx.tolist(), aligned)

    def ret(sym, i, lookback):
        a, b = m[sym].iloc[i - lookback], m[sym].iloc[i]
        return (b / a - 1) if (a and b and not np.isnan(a) and not np.isnan(b)) else None

    def fwd(sym, i):
        a, b = m[sym].iloc[i], m[sym].iloc[i + 1]
        return (b / a - 1) if (a and b and not np.isnan(a) and not np.isnan(b)) else None

    stock_syms = [s for s in STOCKS if s in m]
    sector_syms = [s for s in SECTORS if s in m]
    n = len(months)
    start = 13   # need 12m lookback

    def simulate(pick_fn):
        rets, prev = [], set()
        for i in range(start, n - 1):
            picks = pick_fn(i)
            if not picks:
                rets.append(fwd("BIL", i) or 0.0); prev = {"BIL"}; continue
            fwds = [fwd(s, i) for s in picks if fwd(s, i) is not None]
            r = sum(fwds) / len(fwds) if fwds else 0.0
            turnover = len(set(picks) ^ prev) / max(len(picks), 1)
            rets.append(r - turnover * COST_BPS)
            prev = set(picks)
        return rets

    def mom(i):
        scored = [(ret(s, i - 1, 11), s) for s in stock_syms]  # 12-1 momentum
        scored = [(v, s) for v, s in scored if v is not None]
        scored.sort(reverse=True)
        return [s for _, s in scored[:TOP_N]]

    def dual(i):
        spy12 = ret("SPY", i, 12)
        return mom(i) if (spy12 is not None and spy12 > 0) else []

    def lowvol(i):
        scored = []
        for s in stock_syms:
            window = [fwd(s, j) for j in range(i - 6, i) if fwd(s, j) is not None]
            if len(window) >= 4:
                scored.append((st.pstdev(window), s))
        scored.sort()
        return [s for _, s in scored[:TOP_N]]

    def sector(i):
        scored = [(ret(s, i, 6), s) for s in sector_syms]
        scored = [(v, s) for v, s in scored if v is not None]
        scored.sort(reverse=True)
        return [s for _, s in scored[:TOP_K_SECTOR]]

    strategies = {
        "Mom 12-1": mom, "Dual-Momentum": dual,
        "Low-Vol": lowvol, "Sector-Rot": sector,
    }
    results = {name: _metrics(simulate(fn)) for name, fn in strategies.items()}

    # benchmarks
    spy_rets = [fwd("SPY", i) or 0.0 for i in range(start, n - 1)]
    results["BH-SPY"] = _metrics(spy_rets)
    basket_rets = []
    for i in range(start, n - 1):
        fs = [fwd(s, i) for s in stock_syms if fwd(s, i) is not None]
        basket_rets.append(sum(fs) / len(fs) if fs else 0.0)
    results["BH-Basket"] = _metrics(basket_rets)

    print("=" * 82)
    print(f"PORTFOLIO TOURNAMENT — {len(stock_syms)} stocks, {len(sector_syms)} "
          f"sectors, {YEARS}, monthly, 5bps costs")
    print("=" * 82)
    print(f"{'strategy':<16}{'total':>10}{'CAGR':>9}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}")
    print("-" * 82)
    order = sorted(results, key=lambda k: -results[k].get("cagr", -9))
    for name in order:
        r = results[name]
        flag = "  <-- Sharpe>2, verify" if r.get("sharpe", 0) > 2 else ""
        bench = "  (benchmark)" if name.startswith("BH-") else ""
        print(f"{name:<16}{r['total']:>+9.0%}{r['cagr']:>+9.1%}{r['vol']:>8.0%}"
              f"{r['sharpe']:>8.2f}{r['mdd']:>9.0%}{bench}{flag}")
    print("=" * 82)
    print("CAGR = annualized return; maxDD = worst peak-to-trough; higher Sharpe")
    print("= better risk-adjusted. Beat BH-SPY on BOTH CAGR and maxDD to be worth")
    print("deploying. All long-only, cash-account-runnable.")


if __name__ == "__main__":
    run()
