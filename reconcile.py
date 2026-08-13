"""
Reconcile the bots' internal state against the broker's ground truth.

Seven silent-failure bugs in this system shared one trait: the logs looked
healthy while reality diverged. This compares, position by position and order
by order, what the bots BELIEVE against what Robinhood actually holds:

  * every broker position -> which strategy claims it (or ORPHAN)
  * every strategy claim  -> is it actually held (or PHANTOM)
  * recorded share counts -> do they match the broker (sell-blocking drift)
  * local execution ledger -> does it match the broker's order history
  * dust positions too small to sell

    python -m trading_agent.reconcile
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from .env_file import load_env_file

load_env_file()

HERE = os.path.dirname(__file__)
PRIVATE = os.path.join(HERE, "data", "private")
ACCOUNT = os.environ.get("TRADING_ACCOUNT_NUMBER", "")
DUST = 0.01           # shares below this are unsellable remnants


def _claims() -> dict[str, str]:
    """symbol -> owning strategy, per each bot's own state file."""
    out: dict[str, str] = {}
    for mod, fn, label in (("berkshire_clone", "clone_symbols", "clone"),
                           ("momentum_bot", "momentum_symbols", "momentum"),
                           ("rsi2_bot", "rsi2_symbols", "rsi2")):
        try:
            m = __import__(f"trading_agent.{mod}", fromlist=[fn])
            for s in getattr(m, fn)():
                out[s] = label if s not in out else f"{out[s]}+{label}"
        except Exception:
            continue
    return out


def _recorded_qty() -> dict[str, float]:
    """Share counts the bots think they hold (rsi2 tracks these explicitly)."""
    try:
        st = json.load(open(os.path.join(PRIVATE, "rsi2_state.json")))
        return {s: float(i.get("quantity", 0)) for s, i in st.get("positions", {}).items()}
    except Exception:
        return {}


def run() -> None:
    from .live_executor import RobinhoodExecutor
    ex = RobinhoodExecutor()
    rh = ex.rh

    # ---- broker truth ----
    broker: dict[str, float] = {}
    for p in rh.account.get_open_stock_positions(account_number=ACCOUNT) or []:
        q = float(p.get("quantity") or 0)
        if q > 0:
            broker[rh.stocks.get_symbol_by_url(p["instrument"])] = q

    claims = _claims()
    recorded = _recorded_qty()

    print("=" * 74)
    print(f"RECONCILIATION — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 74)

    # ---- positions ----
    print(f"\nPOSITIONS ({len(broker)} at broker, {len(claims)} claimed by strategies)")
    print(f"  {'symbol':<8}{'broker qty':>12}  {'owner':<18}{'status'}")
    orphans, dust = [], []
    for s in sorted(broker):
        owner = claims.get(s, "— UNCLAIMED —")
        status = "ok"
        if s not in claims:
            status = "ORPHAN: no strategy manages this"
            orphans.append(s)
        elif "+" in owner:
            status = "CONFLICT: two strategies claim it"
        if broker[s] < DUST:
            status = f"DUST ({broker[s]:.6f} sh) — too small to sell"
            dust.append(s)
        print(f"  {s:<8}{broker[s]:>12.6f}  {owner:<18}{status}")

    # ---- phantom claims ----
    phantom = [s for s in claims if s not in broker]
    print(f"\nPHANTOM CLAIMS ({len(phantom)}) — strategy thinks it holds, broker disagrees")
    for s in phantom:
        print(f"  {s:<8} claimed by {claims[s]} but NOT held")
    if not phantom:
        print("  none")

    # ---- share-count drift (the bug that blocked every rsi2 exit) ----
    print("\nSHARE-COUNT DRIFT — recorded vs broker (drift blocks sells)")
    drift = False
    for s, q in recorded.items():
        b = broker.get(s, 0.0)
        flag = "WOULD REJECT (recorded > held)" if q > b + 1e-9 else "ok"
        if q > b + 1e-9:
            drift = True
        print(f"  {s:<8} recorded {q:>10.6f}  broker {b:>10.6f}  {flag}")
    if not recorded:
        print("  no explicit share counts recorded")
    elif not drift:
        print("  no drift")

    # ---- ledger vs broker orders (last 14 days) ----
    since = (date.today() - timedelta(days=14)).isoformat()
    led = []
    p = os.path.join(PRIVATE, "executions.jsonl")
    if os.path.exists(p):
        for line in open(p):
            if line.strip():
                e = json.loads(line)
                if e["ts"][:10] >= since and e.get("result_status") not in ("rejected", None):
                    led.append(e)
    from .live_executor import all_orders
    filled = sum(1 for o in all_orders(rh, ACCOUNT, since)
                 if o.get("state") == "filled")
    print(f"\nORDER LEDGER (last 14 days)")
    print(f"  local ledger says filled : {len(led)}")
    print(f"  broker says filled       : {filled}")
    if filled != len(led):
        print(f"  MISMATCH of {abs(filled-len(led))} — the ledger is not a reliable record;")
        print(f"  trust the broker. (Rejected-then-retried orders can inflate broker count.)")
    else:
        print("  in agreement")

    print("\n" + "=" * 74)
    issues = len(orphans) + len(phantom) + len(dust) + (1 if drift else 0)
    print(f"SUMMARY: {issues} issue(s) found"
          f"{' — clean' if issues == 0 else ''}")
    if orphans:
        print(f"  orphans (unmanaged, no exit rule will ever fire): {orphans}")
    if phantom:
        print(f"  phantom claims (stale state): {phantom}")
    if dust:
        print(f"  dust remnants: {dust}")
    if drift:
        print("  share-count drift present — sells may reject")


if __name__ == "__main__":
    run()
