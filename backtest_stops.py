"""
Does adding a stop-loss to the momentum sleeve actually help?

Momentum currently has NO stop — it exits only when a name decays past
SELL_RANK. That is the classic design (momentum's edge comes from letting
winners run and cutting only on relative weakness), but it means a position
like CSCO can fall 8% in a day with nothing to catch it.

Stops are not free: getting stopped out of a name that then recovers is the
main way stop-losses destroy momentum returns. So this measures, on the same
8-year daily sim as the live strategy:

  none            : current behaviour (rank-decay exits only)
  hard -10/-15/-20: sell if price falls X% below entry
  trail -15/-20   : sell if price falls X% below its high since entry
  circuit breaker : stop BUYING while the portfolio is >15% below its peak

Compares CAGR, worst drawdown, Sharpe, and how often the stop fires.
A stop earns its place only if it cuts drawdown MORE than it cuts return.

    python -m trading_agent.backtest_stops
"""
from __future__ import annotations

import statistics as st

from .backtest_hybrid import (COST_BPS, LOOKBACK, SELL_RANK_PAD, SKIP,
                              UNIVERSE, metrics)

YEARS = "8y"
TOP_N = 5


def run() -> None:
    import yfinance as yf

    print(f"Downloading {len(UNIVERSE)} names + SPY ({YEARS})...")
    d = yf.download(UNIVERSE + ["SPY"], period=YEARS, interval="1d",
                    group_by="ticker", progress=False, threads=True)
    idx = d["SPY"]["Close"].dropna().index
    px, rt = {}, {}
    for s in UNIVERSE + ["SPY"]:
        try:
            ser = d[s]["Close"].reindex(idx).ffill().dropna()
            if len(ser) < LOOKBACK + 60:
                continue
            c = [float(x) for x in ser.tolist()]
            px[s] = c
            rt[s] = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        except Exception:
            continue
    syms = [s for s in UNIVERSE if s in px]
    n = min(len(px[s]) for s in px)
    start = LOOKBACK + SKIP + 5
    sell_rank = TOP_N + SELL_RANK_PAD

    def simulate(hard=None, trail=None, breaker=None):
        held: dict[str, dict] = {}
        out, stops_fired = [], 0
        eq, peak = 1.0, 1.0
        blocked = set()          # stopped-out names, until they re-enter target
        for i in range(start, n - 1):
            # --- stop checks (daily, before any rebalance) ---
            for s in list(held):
                p = px[s][i]
                held[s]["peak"] = max(held[s]["peak"], p)
                hit = False
                if hard and p <= held[s]["entry"] * (1 - hard):
                    hit = True
                if trail and p <= held[s]["peak"] * (1 - trail):
                    hit = True
                if hit:
                    del held[s]
                    blocked.add(s)
                    stops_fired += 1
            # --- monthly rebalance ---
            if i % 21 == 0 or not held:
                sc = sorted(((px[s][i - SKIP] / px[s][i - LOOKBACK] - 1, s)
                             for s in syms if px[s][i - LOOKBACK] > 0), reverse=True)
                rk = [s for _, s in sc]
                pos = {s: j + 1 for j, s in enumerate(rk)}
                tgt = set(rk[:TOP_N])
                blocked &= set(rk[TOP_N:])       # re-allow once out of target
                for s in list(held):
                    if pos.get(s, 10**6) > sell_rank:
                        del held[s]
                drawdown = eq / peak - 1
                halted = breaker is not None and drawdown <= -breaker
                if not halted:
                    for s in tgt:
                        if s not in held and s not in blocked:
                            held[s] = {"entry": px[s][i], "peak": px[s][i]}
            if not held:
                out.append(0.0)
            else:
                w = 1.0 / TOP_N                   # empty slots sit in cash
                day = sum(w * rt[s][i + 1] for s in held)
                if i % 21 == 0:
                    day -= COST_BPS
                out.append(day)
            eq *= (1 + out[-1])
            peak = max(peak, eq)
        return out, stops_fired

    variants = {
        "none (current)":  dict(),
        "hard -10%":       dict(hard=0.10),
        "hard -15%":       dict(hard=0.15),
        "hard -20%":       dict(hard=0.20),
        "trail -15%":      dict(trail=0.15),
        "trail -20%":      dict(trail=0.20),
        "breaker -15%":    dict(breaker=0.15),
    }
    print("=" * 76)
    print(f"STOP-LOSS TEST — Mom{TOP_N}, {len(syms)} names, {YEARS}, daily sim")
    print("=" * 76)
    print(f"{'variant':<18}{'total':>10}{'CAGR':>9}{'maxDD':>9}{'Sharpe':>8}{'stops':>8}")
    print("-" * 76)
    base = None
    for name, kw in variants.items():
        rets, fired = simulate(**kw)
        m = metrics(rets)
        if base is None:
            base = m
        print(f"{name:<18}{m['total']:>+9.0%}{m['cagr']:>+9.1%}{m['mdd']:>9.0%}"
              f"{m['sharpe']:>8.2f}{fired:>8}")
    spy = metrics([rt["SPY"][i + 1] for i in range(start, n - 1)])
    print("-" * 76)
    print(f"{'BH-SPY':<18}{spy['total']:>+9.0%}{spy['cagr']:>+9.1%}"
          f"{spy['mdd']:>9.0%}{spy['sharpe']:>8.2f}{'—':>8}")
    print("=" * 76)
    print("A stop is worth adding only if maxDD improves MORE than CAGR falls.")


if __name__ == "__main__":
    run()
