"""
Swing pattern tournament — daily-bar patterns WITH a trend filter.

Prior findings this builds on:
  * Intraday patterns on all stocks LOSE (backtest_patterns: -0.09..-0.20%/trade).
  * The same pattern logic filtered by trend/momentum WINS (RSI2-in-uptrend
    scored t=+3.87; Mom5-Pullback is the best strategy we have).

So this tests genuinely ACTIVE pattern strategies — many trades per month, not
5/year — where every entry still requires the stock to be in an uptrend:

  RSI2-Uptrend  : RSI(2)<10 AND price>200dMA -> exit RSI(2)>70, 5d, or -5%
  RSI2-Momo     : same, but only names in the momentum top-20 (quality gate)
  BB-Uptrend    : close < lower Bollinger(20,2) AND >200dMA -> exit at mid band
  Dip-Uptrend   : -2.5% day AND >200dMA -> exit +5%/-3%/10d  (old scanner rule)
  3DownUp       : 3 consecutive down days AND >200dMA -> exit +4%/-3%/5d

Portfolio sim: up to MAX_POS concurrent equal-weight positions, costs on every
entry/exit, unfilled slots sit in cash. Benchmarks: SPY and Mom5-Pullback.

    python -m trading_agent.backtest_swing
"""
from __future__ import annotations

import statistics as st

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "JNJ", "PG", "KO",
    "PEP", "WMT", "HD", "CVX", "XOM", "BAC", "AXP", "MCD", "NKE", "DIS",
    "CSCO", "INTC", "VZ", "T", "CMCSA", "ADBE", "CRM", "ACN", "MRK", "PFE",
    "ABBV", "TMO", "COST", "AMD", "QCOM", "TXN", "CAT", "GE", "BA", "LLY",
    "UNH", "V", "MA", "ORCL", "IBM", "QCOM", "LOW", "SBUX", "GILD", "MDT",
]
YEARS = "8y"
MAX_POS = 10            # concurrent positions
COST_BPS = 10 / 10000   # round-trip per trade
STOP = -0.05
TIME_STOP = 10


def rsi(v, period=2):
    out = [None] * len(v)
    ag = al = None
    for i in range(1, len(v)):
        ch = v[i] - v[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i == period:
            gs = [max(v[k] - v[k - 1], 0.0) for k in range(1, period + 1)]
            ls = [max(v[k - 1] - v[k], 0.0) for k in range(1, period + 1)]
            ag, al = sum(gs) / period, sum(ls) / period
        elif i > period:
            ag = (ag * (period - 1) + g) / period
            al = (al * (period - 1) + l) / period
        if ag is not None:
            out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def metrics(rets):
    if not rets:
        return {"total": 0, "cagr": 0, "vol": 0, "sharpe": 0, "mdd": 0}
    eq, curve = 1.0, []
    for r in rets:
        eq *= (1 + r); curve.append(eq)
    yrs = len(rets) / 252
    cagr = eq ** (1 / yrs) - 1 if yrs > 0 else 0
    vol = st.pstdev(rets) * (252 ** 0.5)
    sharpe = (st.mean(rets) * 252) / vol if vol else 0
    peak, mdd = -1e9, 0.0
    for v in curve:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return {"total": eq - 1, "cagr": cagr, "vol": vol, "sharpe": sharpe, "mdd": mdd}


def run():
    import numpy as np, yfinance as yf
    syms = sorted(set(UNIVERSE))
    print(f"Downloading {len(syms)} symbols ({YEARS})...")
    d = yf.download(syms + ["SPY"], period=YEARS, interval="1d",
                    group_by="ticker", progress=False, threads=True)
    idx = d["SPY"]["Close"].dropna().index
    px, rt, r2, ma200, mom = {}, {}, {}, {}, {}
    for s in syms + ["SPY"]:
        try:
            ser = d[s]["Close"].reindex(idx).ffill().dropna()
            if len(ser) < 300:
                continue
            c = [float(x) for x in ser.tolist()]
            px[s] = c
            rt[s] = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
            r2[s] = rsi(c, 2)
            m = [None] * len(c)
            for i in range(200, len(c)):
                m[i] = sum(c[i - 200:i]) / 200
            ma200[s] = m
            mm = [None] * len(c)
            for i in range(273, len(c)):
                mm[i] = c[i - 21] / c[i - 252] - 1
            mom[s] = mm
        except Exception:
            continue
    names = [s for s in syms if s in px]
    n = min(len(px[s]) for s in px)
    start = 280

    def uptrend(s, i):
        return ma200[s][i] is not None and px[s][i] > ma200[s][i]

    def momo_top(i, k=20):
        sc = [(mom[s][i], s) for s in names if mom[s][i] is not None]
        sc.sort(reverse=True)
        return {s for _, s in sc[:k]}

    def simulate(entry_fn, exit_fn, momo_gate=False):
        held = {}      # sym -> [entry_px, days_held]
        out = []
        for i in range(start, n - 1):
            gate = momo_top(i) if momo_gate else None
            # exits
            for s in list(held):
                e, dh = held[s]
                r = px[s][i] / e - 1
                if r <= STOP or dh >= TIME_STOP or exit_fn(s, i, r):
                    del held[s]
            # entries
            if len(held) < MAX_POS:
                cands = []
                for s in names:
                    if s in held or not uptrend(s, i):
                        continue
                    if gate is not None and s not in gate:
                        continue
                    if entry_fn(s, i):
                        cands.append(s)
                for s in cands[:MAX_POS - len(held)]:
                    held[s] = [px[s][i], 0]
            if not held:
                out.append(0.0); continue
            w = 1.0 / MAX_POS      # unfilled slots = cash
            day = sum(w * rt[s][i + 1] for s in held)
            day -= len(held) * w * COST_BPS / TIME_STOP   # amortized costs
            for s in held:
                held[s][1] += 1
            out.append(day)
        return out

    def bb_low(s, i, p=20, k=2.0):
        w = px[s][i - p:i]
        if len(w) < p:
            return False
        m = sum(w) / p; sd = st.pstdev(w)
        return sd > 0 and px[s][i] < m - k * sd

    def bb_mid(s, i, p=20):
        w = px[s][i - p:i]
        return len(w) == p and px[s][i] >= sum(w) / p

    strategies = {
        "RSI2-Uptrend": (lambda s, i: r2[s][i] is not None and r2[s][i] < 10,
                         lambda s, i, r: r2[s][i] is not None and r2[s][i] > 70, False),
        "RSI2-Momo20":  (lambda s, i: r2[s][i] is not None and r2[s][i] < 10,
                         lambda s, i, r: r2[s][i] is not None and r2[s][i] > 70, True),
        "BB-Uptrend":   (bb_low, lambda s, i, r: bb_mid(s, i), False),
        "Dip-Uptrend":  (lambda s, i: rt[s][i] <= -0.025,
                         lambda s, i, r: r >= 0.05, False),
        "3DownUp":      (lambda s, i: all(rt[s][i - k] < 0 for k in range(3)),
                         lambda s, i, r: r >= 0.04, False),
    }
    res = {}
    for name, (ef, xf, gate) in strategies.items():
        res[name] = metrics(simulate(ef, xf, gate))
    res["BH-SPY"] = metrics([rt["SPY"][i + 1] for i in range(start, n - 1)])

    print("=" * 78)
    print(f"SWING PATTERN TOURNAMENT — {len(names)} names, {YEARS}, "
          f"max {MAX_POS} positions, {COST_BPS*10000:.0f}bps/trade")
    print("=" * 78)
    print(f"{'strategy':<16}{'total':>10}{'CAGR':>9}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}")
    print("-" * 78)
    for k in sorted(res, key=lambda k: -res[k]["cagr"]):
        m = res[k]
        tag = "  (benchmark)" if k.startswith("BH") else ""
        print(f"{k:<16}{m['total']:>+9.0%}{m['cagr']:>+9.1%}{m['vol']:>8.0%}"
              f"{m['sharpe']:>8.2f}{m['mdd']:>9.0%}{tag}")
    print("=" * 78)
    print("Reference: Mom5-Pullback (live) backtested +40.4% CAGR / Sharpe 1.23.")
    print("A pattern bot must beat BOTH SPY and that to justify replacing it.")


if __name__ == "__main__":
    run()
