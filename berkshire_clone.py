"""
Berkshire 13F clone — coattail investing, straight from SEC EDGAR.

The strategy (academically studied "alpha cloning"): hold Berkshire Hathaway's
top-10 disclosed stock positions, refreshed each quarter when the new 13F-HR
files (~45 days after quarter end). Evidence: Martin & Puthenpurackal (2008)
found a Buffett-mimicking portfolio beat the S&P by ~10%/yr over 30 years;
Faber replicated similar results. It works for Berkshire specifically because
their turnover is tiny — the filing lag barely matters.

What this module does (deterministic, no discretion):
  1. Pulls the latest 13F-HR infotable from SEC EDGAR (official data).
  2. Ranks holdings by reported value, maps the top N to tickers.
  3. Diffs against the current clone state and builds governor-checked
     BUY tickets for new/added names (and flags exits for names Berkshire
     dropped). Tickets land in the console for YOUR click — the clone does
     not auto-execute.
  4. Records clone-managed symbols in data/private/clone_state.json; the
     exit engine SKIPS those (they're held until Berkshire sells, not
     +10%/-5%/20d).

    python -m trading_agent.berkshire_clone          # fetch + build tickets
    python -m trading_agent.berkshire_clone --show   # just show the top-10
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timezone

from .env_file import load_env_file

load_env_file()

# ---- ARMING (the user's act, never the assistant's) ----
# CLONE_LIVE=1 in ~/.trading_agent.env makes each run AUTO-EXECUTE the clone:
# buys new/added top-10 names and sells positions Berkshire dropped, through
# the same executor + gates as the scanner (daily trade cap, dedupe,
# rejection halt, no same-day sells). Unarmed, it only builds tickets.
CLONE_LIVE = os.environ.get("CLONE_LIVE") == "1"

HERE = os.path.dirname(__file__)
PRIVATE = os.path.join(HERE, "data", "private")
STATE = os.path.join(PRIVATE, "clone_state.json")

CIK = "0001067983"                      # Berkshire Hathaway Inc
UA = {"User-Agent": "trading_agent research vaibhavnarang87@gmail.com"}
TOP_N = 10
DOLLARS_PER_NAME = float(os.environ.get("CLONE_DOLLARS_PER_NAME", "200"))

# Issuer-name fragments -> tickers, for Berkshire's known/likely holdings.
NAME_TO_TICKER = {
    "APPLE": "AAPL", "AMERICAN EXPRESS": "AXP", "COCA COLA": "KO",
    "COCA-COLA": "KO", "BANK AMER": "BAC", "BANK OF AMER": "BAC",
    "CHEVRON": "CVX", "OCCIDENTAL": "OXY", "KRAFT HEINZ": "KHC",
    "MOODYS": "MCO", "MOODY'S": "MCO", "CHUBB": "CB", "DAVITA": "DVA",
    "CITIGROUP": "C", "ALPHABET INC CL A": "GOOGL", "ALPHABET INC CAP STK CL A": "GOOGL",
    "ALPHABET INC CL C": "GOOG", "ALPHABET INC CAP STK CL C": "GOOG",
    "ALPHABET": "GOOGL", "DELTA AIR": "DAL", "LENNAR CORP CL A": "LEN",
    "LENNAR CORP CL B": "LEN.B", "LENNAR": "LEN", "NEW YORK TIMES": "NYT",
    "MACY": "M", "SIRIUS": "SIRI", "VERISIGN": "VRSN", "KROGER": "KR",
    "AMAZON": "AMZN", "VISA": "V", "MASTERCARD": "MA", "AON": "AON",
    "CAPITAL ONE": "COF", "ALLY FINL": "ALLY", "T-MOBILE": "TMUS",
    "CONSTELLATION BRANDS": "STZ", "DOMINOS": "DPZ", "DOMINO'S": "DPZ",
    "POOL CORP": "POOL", "HEICO": "HEI", "LIBERTY MEDIA": "FWONK",
    "NU HLDGS": "NU", "UNITEDHEALTH": "UNH", "LAMAR": "LAMR",
}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def latest_13f_infotable() -> tuple[str, str, bytes]:
    """Return (accession, filing_date, infotable_xml_bytes) of the newest 13F-HR."""
    subs = json.loads(_get(f"https://data.sec.gov/submissions/CIK{CIK}.json"))
    recent = subs["filings"]["recent"]
    for form, acc, fdate in zip(recent["form"], recent["accessionNumber"],
                                recent["filingDate"]):
        if form in ("13F-HR", "13F-HR/A"):
            acc_nodash = acc.replace("-", "")
            base = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{acc_nodash}"
            index = json.loads(_get(f"{base}/index.json"))
            names = [i["name"] for i in index["directory"]["item"]]
            xmls = [n for n in names if n.lower().endswith(".xml")
                    and "primary_doc" not in n.lower()]
            if not xmls:
                continue
            return acc, fdate, _get(f"{base}/{xmls[0]}")
    raise RuntimeError("No 13F-HR found in recent EDGAR filings")


def parse_top_holdings(xml: bytes, top_n: int = TOP_N) -> list[dict]:
    """Namespace-agnostic infotable parse -> top N by value with tickers."""
    text = xml.decode("utf-8", "replace")
    entries = re.findall(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>",
                         text, re.S | re.I)
    agg: dict[str, float] = {}
    for e in entries:
        name = re.search(r"<(?:\w+:)?nameOfIssuer>(.*?)</", e, re.S)
        val = re.search(r"<(?:\w+:)?value>([\d.]+)</", e)
        cls = re.search(r"<(?:\w+:)?titleOfClass>(.*?)</", e, re.S)
        if not (name and val):
            continue
        key = name.group(1).strip().upper()
        if cls and "CL" in cls.group(1).upper():
            key += " " + cls.group(1).strip().upper().replace("COM ", "")
        agg[key] = agg.get(key, 0.0) + float(val.group(1))
    ranked = sorted(agg.items(), key=lambda kv: -kv[1])
    total = sum(agg.values()) or 1.0
    out = []
    for issuer, value in ranked:
        ticker = None
        for frag, t in NAME_TO_TICKER.items():
            if frag in issuer:
                ticker = t
                break
        out.append({"issuer": issuer, "ticker": ticker,
                    "value_usd": value, "weight": value / total})
        if len([o for o in out if o["ticker"]]) >= top_n:
            break
    return out


def load_state() -> dict:
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save_state(acc: str, fdate: str, symbols: list[str]) -> None:
    os.makedirs(PRIVATE, exist_ok=True)
    json.dump({"accession": acc, "filing_date": fdate, "symbols": symbols,
               "updated": datetime.now(timezone.utc).isoformat()},
              open(STATE, "w"), indent=2)


def clone_symbols() -> set[str]:
    """Symbols under clone management (exit engine must skip these)."""
    return set(load_state().get("symbols", []))


def _auto_deploy(symbols: list[str]) -> None:
    """Armed only: execute the clone's un-executed tickets through the shared
    gate chain (governor approval, daily cap, dedupe, rejection halt)."""
    import trading_agent.live_scanner as sc
    executor, label = sc._get_executor()
    if executor is None:
        print(f"  auto-deploy unavailable: {label}")
        return
    for s in symbols:
        outcome = sc._auto_execute(s)
        print(f"  {s:<6} -> {outcome}")
        if "daily trade cap" in outcome:
            print("  (cap reached — the daily run will deploy the rest tomorrow)")
            break


def _auto_sell_dropped(dropped: list[str]) -> None:
    """Armed only: sell whole positions in names Berkshire dropped."""
    if not dropped:
        return
    import trading_agent.live_scanner as sc
    from .poc.order import Order, OrderType, Side
    executor, label = sc._get_executor()
    if executor is None:
        return
    acct = os.environ.get("TRADING_ACCOUNT_NUMBER", "")
    positions = executor.rh.account.get_open_stock_positions(account_number=acct) or []
    today = date.today()
    for pos in positions:
        try:
            qty = float(pos.get("quantity") or 0)
            if qty <= 0:
                continue
            sym = executor.rh.stocks.get_symbol_by_url(pos["instrument"])
            if sym not in dropped:
                continue
            created = pos.get("created_at", "")[:10]
            if created and (today - date.fromisoformat(created)).days < 1:
                continue   # PDT-safe: never same-day
            order = Order(account_number=acct, symbol=sym, side=Side.SELL,
                          type=OrderType.MARKET, quantity=round(qty, 6))
            result = executor.place(order)
            with open(os.path.join(PRIVATE, "executions.jsonl"), "a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "ref_id": order.ref_id, "symbol": sym,
                    "order": order.describe(), "executor": label,
                    "result_status": result.get("status"),
                    "real_money": result.get("real_money", False),
                    "exit_reason": "berkshire dropped from 13F"}) + "\n")
            print(f"  SOLD {sym} (dropped from 13F) -> {result.get('status')}")
        except Exception as e:
            print(f"  sell {pos.get('instrument','?')[-13:]}: {type(e).__name__}: {e}")


def run(show_only: bool = False) -> None:
    acc, fdate, xml = latest_13f_infotable()
    top = parse_top_holdings(xml)
    mapped = [t for t in top if t["ticker"]]
    print(f"Berkshire 13F {acc} (filed {fdate}) — top holdings by value:")
    for t in top:
        tick = t["ticker"] or "?unmapped?"
        print(f"  {tick:<7} {t['weight']:>6.1%}  {t['issuer'][:40]}")
    unmapped = [t for t in top if not t["ticker"]]
    if unmapped:
        print(f"  ({len(unmapped)} unmapped issuer(s) shown above — extend "
              f"NAME_TO_TICKER if they belong in the top {TOP_N})")
    if show_only:
        return

    prev = set(load_state().get("symbols", []))
    now = [t["ticker"] for t in mapped]
    added, dropped = [s for s in now if s not in prev], sorted(prev - set(now))

    if added:
        from .add_tickets import add
        print(f"\nBuilding governor-checked tickets (${DOLLARS_PER_NAME:.0f} each) "
              f"for: {', '.join(added)}")
        add(added, DOLLARS_PER_NAME,
            f"Berkshire 13F clone ({fdate}, held until Berkshire sells)")
    else:
        print("\nNo new names vs current clone state.")
    if dropped:
        print(f"Berkshire DROPPED: {', '.join(dropped)} — if you hold these "
              f"as clone positions, sell them in the console/app (the clone "
              f"only rebalances on filings).")
    save_state(acc, fdate, now)
    print(f"\nClone state saved ({len(now)} symbols). The exit engine will "
          f"SKIP these — they are held until Berkshire's next filing, not "
          f"managed by +10%/-5%/20d.")
    if CLONE_LIVE:
        print("\nCLONE_LIVE armed — auto-deploying through the shared gates:")
        _auto_deploy(now)
        _auto_sell_dropped(dropped)
    else:
        print("Not armed (CLONE_LIVE unset): deploying is YOUR click — the "
              "tickets are in the console.")


if __name__ == "__main__":
    run(show_only="--show" in sys.argv)
