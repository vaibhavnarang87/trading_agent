"""
Hybrid + sizing tournament — momentum selection x pattern timing x aggression.

The pattern tournament showed intraday patterns lose when applied to ALL
stocks. The open question is whether they help as *entry timing* inside a
strategy that already works: momentum picks WHAT to own, a pullback pattern
picks WHEN to buy it. This tests that, plus the two real "aggression" levers.

Variants (all long-only, daily sim, monthly-style rebalancing, costs on turnover):
  Mom10            : hold top-10 by 12-1 momentum            (CURRENT LIVE)
  Mom5             : hold top-5   -> concentration = aggression
  Mom3             : hold top-3   -> maximum concentration
  Mom10-InvVol     : top-10, weights ~ 1/volatility (equal RISK, not equal $)
  Mom10-Pullback   : top-10, but only ENTER on a pullback (RSI2<10 or -2% day)
  Mom5-Pullback    : top-5 + pullback timing (aggressive hybrid)
Benchmarks: SPY, equal-weight basket.

Exit for all: name falls past SELL_RANK (hysteresis), same as the live bot.

    python -m trading_agent.backtest_hybrid
"""
from __future__ import annotations

import statistics as st

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "JNJ", "PG", "KO",
    "PEP", "WMT", "HD", "CVX", "XOM", "BAC", "AXP", "MCD", "NKE", "DIS",
    "CSCO", "INTC", "VZ", "T", "CMCSA", "ADBE", "CRM", "ACN", "MRK", "PFE",
    "ABBV", "TMO", "COST", "AMD", "QCOM", "TXN", "CAT", "GE", "BA", "LLY",
]
YEARS = "8y"
LOOKBACK, SKIP = 252, 21      # 12-1 momentum
SELL_RANK_PAD = 5             # sell once rank > TOP_N + pad
COST_BPS = 5 / 10000
VOL_WINDOW = 20


def rsi(vals, period=2):
    out = [None] * len(vals)
    ag = al = None
    for i in range(1, len(vals)):
        ch = vals[i] - vals[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i == period:
            gs = [max(vals[k] - vals[k - 1], 0.0) for k in range(1, period + 1)]
            ls = [max(vals[k - 1] - vals[k], 0.0) for k in range(1, period + 1)]
            ag, al = sum(gs) / period, sum(ls) / period
        elif i > period:
            ag = (ag * (period - 1) + g) / period
            al = (al * (period - 1) + l) / period
        if ag is not None:
            out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def metrics(rets: list[float]) -> dict:
    if not rets:
        return {"cagr": 0, "vol": 0, "sharpe": 0, "mdd": 0, "total": 0}
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


def run() -> None:
    import numpy as np
    import yfinance as yf

    print(f"Downloading {len(UNIVERSE)} symbols + SPY ({YEARS})...")
    data = yf.download(UNIVERSE + ["SPY"], period=YEARS, interval="1d",
                       group_by="ticker", progress=False, threads=True)
    spy_idx = data["SPY"]["Close"].dropna().index
    px, rets, rsis, vols = {}, {}, {}, {}
    for s in UNIVERSE + ["SPY"]:
        try:
            ser = data[s]["Close"].reindex(spy_idx).ffill().dropna()
            if len(ser) < LOOKBACK + 60:
                continue
            c = [float(x) for x in ser.tolist()]
            px[s] = c
            rets[s] = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
            rsis[s] = rsi(c, 2)
            v = [None] * len(c)
            for i in range(VOL_WINDOW, len(c)):
                v[i] = st.pstdev(rets[s][i - VOL_WINDOW:i]) or 1e-6
            vols[s] = v
        except Exception:
            continue
    syms = [s for s in UNIVERSE if s in px]
    n = min(len(px[s]) for s in px)
    start = LOOKBACK + SKIP + 5

    def ranking(i):
        sc = []
        for s in syms:
            a, b = px[s][i - LOOKBACK], px[s][i - SKIP]
            if a > 0:
                sc.append((b / a - 1, s))
        sc.sort(reverse=True)
        return [s for _, s in sc]

    def simulate(top_n, pullback=False, inv_vol=False):
        held: dict[str, float] = {}
        out = []
        sell_rank = top_n + SELL_RANK_PAD
        for i in range(start, n - 1):
            if i % 21 == 0 or not held:        # monthly-style refresh
                rk = ranking(i)
                pos = {s: j + 1 for j, s in enumerate(rk)}
                target = set(rk[:top_n])
                # exits: fell past hysteresis rank
                for s in list(held):
                    if pos.get(s, 10**6) > sell_rank:
                        del held[s]
                # entries
                for s in target:
                    if s in held:
                        continue
                    if pullback:
                        r = rsis[s][i]
                        if not ((r is not None and r < 10) or rets[s][i] <= -0.02):
                            continue           # wait for a pullback
                    held[s] = 1.0
            if not held:
                out.append(0.0); continue
            if inv_vol:
                w = {s: 1.0 / (vols[s][i] or 1e-6) for s in held}
                tot = sum(w.values())
                w = {s: v / tot for s, v in w.items()}
            else:
                w = {s: 1.0 / len(held) for s in held}
            day = sum(w[s] * rets[s][i + 1] for s in held)
            if i % 21 == 0:
                day -= COST_BPS
            out.append(day)
        return out

    variants = {
        "Mom10 (current)":  simulate(10),
        "Mom5":             simulate(5),
        "Mom3":             simulate(3),
        "Mom10-InvVol":     simulate(10, inv_vol=True),
        "Mom10-Pullback":   simulate(10, pullback=True),
        "Mom5-Pullback":    simulate(5, pullback=True),
    }
    spy_rets = [rets["SPY"][i + 1] for i in range(start, n - 1)]
    basket = [sum(rets[s][i + 1] for s in syms) / len(syms)
              for i in range(start, n - 1)]
    variants["BH-SPY"] = spy_rets
    variants["BH-Basket"] = basket

    print("=" * 80)
    print(f"HYBRID & SIZING TOURNAMENT — {len(syms)} names, {YEARS}, daily sim, "
          f"{COST_BPS*10000:.0f}bps rebalance cost")
    print("=" * 80)
    print(f"{'variant':<20}{'total':>10}{'CAGR':>9}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}")
    print("-" * 80)
    for name in sorted(variants, key=lambda k: -metrics(variants[k])["cagr"]):
        m = metrics(variants[name])
        tag = "  (benchmark)" if name.startswith("BH-") else ""
        print(f"{name:<20}{m['total']:>+9.0%}{m['cagr']:>+9.1%}{m['vol']:>8.0%}"
              f"{m['sharpe']:>8.2f}{m['mdd']:>9.0%}{tag}")
    print("=" * 80)
    print("Aggression is only worth it if CAGR rises MORE than maxDD deepens.")
    print("Pullback = pattern timing layered on momentum selection.")


if __name__ == "__main__":
    run()
