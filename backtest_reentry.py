"""
After a stop-loss fires, WHEN should the name be allowed back?

Live behaviour blocks a stopped-out name until it drops out of the top-N. In
production that stranded capital badly: AMD, CSCO and INTC were all stopped,
all stayed top-ranked, and momentum sat on 2 of 5 slots for weeks. The question
is whether that block earns its keep or just costs exposure.

Policies tested (stop level held constant, then swept):
  block-until-out : current live rule
  cooldown N      : re-enter after N trading days regardless of rank
  none            : re-enter at the next rebalance
  recovered       : re-enter once price is back above the stop price

    python -m trading_agent.backtest_reentry
"""
from __future__ import annotations

from .backtest_hybrid import (COST_BPS, LOOKBACK, SELL_RANK_PAD, SKIP,
                              UNIVERSE, metrics)

YEARS, TOP_N = "8y", 5


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

    def sim(stop, policy, cooldown=0):
        held, blocked = {}, {}       # blocked: sym -> (day_stopped, stop_price)
        out, fired, idle = [], 0, 0
        for i in range(start, n - 1):
            for s in list(held):
                if px[s][i] <= held[s] * (1 - stop):
                    blocked[s] = (i, px[s][i])
                    del held[s]
                    fired += 1
            if i % 21 == 0 or not held:
                sc = sorted(((px[s][i - SKIP] / px[s][i - LOOKBACK] - 1, s)
                             for s in syms if px[s][i - LOOKBACK] > 0), reverse=True)
                rk = [s for _, s in sc]
                pos = {s: j + 1 for j, s in enumerate(rk)}
                tgt = set(rk[:TOP_N])
                for s in list(blocked):
                    day, sp = blocked[s]
                    if policy == "block":      release = s not in tgt
                    elif policy == "cooldown": release = (i - day) >= cooldown
                    elif policy == "none":     release = True
                    else:                      release = px[s][i] > sp   # recovered
                    if release:
                        del blocked[s]
                for s in list(held):
                    if pos.get(s, 10**6) > sell_rank:
                        del held[s]
                for s in tgt:
                    if s not in held and s not in blocked:
                        held[s] = px[s][i]
            idle += TOP_N - len(held)
            if not held:
                out.append(0.0)
            else:
                day = sum((1.0 / TOP_N) * rt[s][i + 1] for s in held)
                if i % 21 == 0:
                    day -= COST_BPS
                out.append(day)
        return metrics(out), fired, idle / ((n - 1 - start) * TOP_N)

    for stop in (0.07, 0.10):
        print("=" * 78)
        print(f"RE-ENTRY POLICY after a {stop:.0%} stop — Mom{TOP_N}, {len(syms)} names, {YEARS}")
        print("=" * 78)
        print(f"{'policy':<20}{'CAGR':>9}{'maxDD':>9}{'Sharpe':>8}{'stops':>8}{'idle slots':>12}")
        print("-" * 78)
        for label, kw in (("block-until-out", dict(policy="block")),
                          ("cooldown 5d",     dict(policy="cooldown", cooldown=5)),
                          ("cooldown 21d",    dict(policy="cooldown", cooldown=21)),
                          ("cooldown 63d",    dict(policy="cooldown", cooldown=63)),
                          ("none (rebuy)",    dict(policy="none")),
                          ("recovered",       dict(policy="recovered"))):
            m, f, idle = sim(stop, **kw)
            print(f"{label:<20}{m['cagr']:>+9.1%}{m['mdd']:>9.0%}{m['sharpe']:>8.2f}"
                  f"{f:>8}{idle:>11.0%}")
        print()
    spy = metrics([rt["SPY"][i + 1] for i in range(start, n - 1)])
    print(f"{'BH-SPY':<20}{spy['cagr']:>+9.1%}{spy['mdd']:>9.0%}{spy['sharpe']:>8.2f}")


if __name__ == "__main__":
    run()
