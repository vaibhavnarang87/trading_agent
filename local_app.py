"""
Local ticket console — PRIVATE, localhost-only, with a gated Execute button.

Lists today's order tickets. Each risk-approved, live-armed ticket has:
  - a "copy call" button (hands you the exact place_equity_order(...) call)
  - an "EXECUTE" button that fires the order through YOUR executor
    (live_executor.py — your credentials, your login, your click).

The Execute path is deliberately hard to reach. ALL of these must be true:
  1. You started this server yourself, on this machine (binds 127.0.0.1 only).
  2. The plan's switch is LIVE-armed (Mode.LIVE + live_trading_armed in
     briefing_daily.py — your deliberate edit).
  3. The ticket passed the deterministic risk governor.
  4. TRADING_EXECUTOR=robinhood is set AND you completed Robinhood login/MFA
     in your terminal at startup. (Default executor is paper: simulated fills.)
  5. You click EXECUTE and confirm the exact order in a dialog.
  6. Per-request token matches (blocks CSRF from random web pages) and the
     Host header is localhost (blocks DNS-rebinding).
  7. The ticket's ref_id has not already been executed (no double-fires) and
     the daily trade cap has not been reached.

Every execution (paper or real) is appended to data/private/executions.jsonl.

    python -m trading_agent.local_app          # http://127.0.0.1:8787
"""
from __future__ import annotations

import glob
import html
import json
import os
import secrets
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import RiskLimits
from .poc.order import Order, OrderType, Side
from .trade_plan import PRIVATE_DIR

HOST = "127.0.0.1"     # localhost only — deliberately not 0.0.0.0
PORT = int(os.environ.get("TICKET_APP_PORT", "8787"))
EXECUTIONS = os.path.join(PRIVATE_DIR, "executions.jsonl")

TOKEN = secrets.token_hex(16)          # per-server-run CSRF token
EXECUTOR = None                        # set in main()
EXEC_LABEL = "not initialized"
LIMITS = RiskLimits()


# ---------- plan / ledger helpers ----------

def _latest_plan() -> dict | None:
    files = sorted(glob.glob(os.path.join(PRIVATE_DIR, "trade_plan_*.json")))
    return json.load(open(files[-1])) if files else None


def _executions() -> list[dict]:
    if not os.path.exists(EXECUTIONS):
        return []
    return [json.loads(l) for l in open(EXECUTIONS) if l.strip()]


def _executed_ref_ids() -> set[str]:
    return {e.get("ref_id") for e in _executions()}


def _executions_today() -> int:
    today = date.today().isoformat()
    return sum(1 for e in _executions() if e.get("ts", "").startswith(today))


def _record_execution(ref_id: str, describe: str, result: dict) -> None:
    os.makedirs(PRIVATE_DIR, exist_ok=True)
    with open(EXECUTIONS, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "ref_id": ref_id,
            "order": describe,
            "executor": EXEC_LABEL,
            "result_status": result.get("status"),
            "real_money": result.get("real_money", False),
        }) + "\n")


def _order_from_params(p: dict) -> Order:
    return Order(
        account_number=p["account_number"],
        symbol=p["symbol"],
        side=Side(p["side"]),
        type=OrderType(p["type"]),
        quantity=float(p["quantity"]) if p.get("quantity") else None,
        dollar_amount=float(p["dollar_amount"]) if p.get("dollar_amount") else None,
        limit_price=float(p["limit_price"]) if p.get("limit_price") else None,
        time_in_force=p.get("time_in_force", "gfd"),
        ref_id=p["ref_id"],
    )


# ---------- the execute gate (every check, in order, with a reason) ----------

def try_execute(ref_id: str) -> tuple[bool, str, dict | None]:
    plan = _latest_plan()
    if plan is None:
        return False, "no trade plan on disk", None
    if not plan.get("armed"):
        return False, "switch is PAPER — arm it in briefing_daily.py first", None

    ticket = next((t for t in plan.get("tickets", [])
                   if t.get("broker_params", {}).get("ref_id") == ref_id), None)
    if ticket is None:
        return False, "unknown ticket ref_id", None
    if not ticket.get("approved") or ticket.get("status") != "live-armed":
        return False, f"ticket not executable (status={ticket.get('status')})", None

    if ref_id in _executed_ref_ids():
        return False, "already executed (double-fire blocked)", None
    if _executions_today() >= LIMITS.max_trades_per_day:
        return False, (f"daily trade cap reached "
                       f"({LIMITS.max_trades_per_day}/day)"), None

    order = _order_from_params(ticket["broker_params"])
    errs = order.validate()
    if errs:
        return False, f"order failed validation: {errs}", None

    result = EXECUTOR.place(order)
    _record_execution(ref_id, order.describe(), result)
    return True, "submitted", result


# ---------- rendering ----------

def _render(plan: dict | None) -> str:
    if plan is None:
        return _page("<p class='empty'>No trade plan found. Run "
                     "<code>python -m trading_agent.briefing_daily</code> first.</p>",
                     "No plan")

    armed = plan.get("armed", False)
    real = "REAL MONEY" in EXEC_LABEL
    switch = "LIVE-ARMED" if armed else "PAPER (safe)"
    tickets = plan.get("tickets", [])
    done = _executed_ref_ids()

    rows = []
    for t in tickets:
        p = t.get("broker_params", {})
        ref = p.get("ref_id", "")
        status = t.get("status", "paper")
        badge = {"live-armed": ("READY", "b-ready"), "paper": ("paper", "b-paper"),
                 "vetoed": ("vetoed", "b-veto")}.get(status, ("paper", "b-paper"))
        call = f"place_equity_order(**{p})"
        executable = armed and t.get("approved") and status == "live-armed"

        if ref in done:
            action = "<span class='donetag'><i class='ti ti-check'></i> executed</span>"
        elif executable:
            action = (
                f"<button class='copy' data-call=\"{html.escape(call, quote=True)}\">"
                f"<i class='ti ti-copy'></i> copy call</button>"
                f"<button class='exec' data-ref='{html.escape(ref)}' "
                f"data-desc=\"{html.escape(t.get('describe',''), quote=True)}\">"
                f"<i class='ti ti-bolt'></i> EXECUTE</button>")
        else:
            action = "<span class='muted'>execute enabled when live-armed &amp; approved</span>"

        rows.append(f"""
        <div class="ticket" id="tk-{html.escape(ref)}">
          <div class="thead">
            <span class="desc">{html.escape(t.get('describe',''))}</span>
            <span class="badge {badge[1]}">{badge[0]}</span>
          </div>
          <div class="why">{html.escape('; '.join(t.get('reasons', [])))}</div>
          <div class="gov">governor: {html.escape(t.get('governor_reason',''))}</div>
          <div class="callrow">{action}</div>
          <div class="result" id="rs-{html.escape(ref)}"></div>
        </div>""")

    exec_cls = "x-real" if real else "x-paper"
    body = f"""
      <div class="switch {'armed' if armed else 'paper'}">Switch: {switch}
        <span class="sub">plan {html.escape(str(plan.get('date')))} ·
        executions today: {_executions_today()}/{LIMITS.max_trades_per_day}</span>
      </div>
      <div class="execline {exec_cls}">Executor: {html.escape(EXEC_LABEL)}</div>
      {''.join(rows) or "<p class='empty'>No tickets in today's plan.</p>"}
      <p class="foot">localhost only · every execute is confirmed by you and logged
      to executions.jsonl · the trigger is yours</p>
    """
    return _page(body, switch)


def _page(body: str, subtitle: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket console — {html.escape(subtitle)}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/tabler-icons/2.47.0/iconfont/tabler-icons.min.css">
<style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
   max-width:720px;margin:0 auto;padding:24px 16px 60px;line-height:1.5;color:#1a1a1a;background:#fafafa}}
 h1{{font-size:1.25rem;margin:0 0 16px}}
 .switch{{font-weight:600;padding:10px 14px;border-radius:8px 8px 0 0}}
 .switch .sub{{display:block;font-weight:400;font-size:.8rem;color:#555;margin-top:2px}}
 .paper{{background:#e8f5e9;color:#1b5e20}} .armed{{background:#fff3e0;color:#b23c00}}
 .execline{{font-size:.85rem;font-weight:600;padding:8px 14px;border-radius:0 0 8px 8px;margin-bottom:20px}}
 .x-paper{{background:#eef3f8;color:#1a4b7a}} .x-real{{background:#fdecea;color:#a01919}}
 .ticket{{background:#fff;border:1px solid #e2e2e2;border-radius:10px;padding:14px 16px;margin-bottom:12px}}
 .thead{{display:flex;justify-content:space-between;align-items:center}}
 .desc{{font-weight:600}}
 .badge{{font-size:.72rem;padding:3px 9px;border-radius:6px;font-weight:600}}
 .b-ready{{background:#fff3e0;color:#b23c00}} .b-paper{{background:#eee;color:#555}}
 .b-veto{{background:#fdecea;color:#a01919}}
 .why{{font-size:.85rem;color:#444;margin:8px 0 2px}}
 .gov{{font-size:.78rem;color:#777;margin-bottom:10px}}
 .callrow{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
 button.copy{{border:1px solid #888;background:#fff;color:#444;border-radius:6px;
   padding:7px 12px;font-size:.8rem;cursor:pointer}}
 button.copy:hover{{background:#f0f0f0}}
 button.exec{{border:1px solid #a01919;background:#a01919;color:#fff;border-radius:6px;
   padding:7px 14px;font-size:.8rem;font-weight:700;cursor:pointer}}
 button.exec:hover{{background:#7d1010}}
 button:disabled{{opacity:.5;cursor:default}}
 .donetag{{font-size:.8rem;color:#1b5e20;font-weight:600}}
 .result{{font-size:.78rem;margin-top:8px;color:#333;white-space:pre-wrap}}
 .muted{{font-size:.75rem;color:#999}}
 .foot{{font-size:.75rem;color:#999;margin-top:24px;text-align:center}}
 .empty{{color:#777}} code{{font-family:ui-monospace,Menlo,monospace}}
</style></head><body>
<h1>Ticket console</h1>
{body}
<script>
var TOKEN="{TOKEN}";
document.querySelectorAll('button.copy').forEach(function(b){{
  b.addEventListener('click',function(){{
    navigator.clipboard.writeText(b.dataset.call).then(function(){{
      var o=b.innerHTML; b.textContent='copied ✓';
      setTimeout(function(){{b.innerHTML=o;}},1200);
    }});
  }});
}});
document.querySelectorAll('button.exec').forEach(function(b){{
  b.addEventListener('click',function(){{
    var msg="PLACE THIS ORDER?\\n\\n"+b.dataset.desc+
      "\\n\\nExecutor: {html.escape(EXEC_LABEL)}"+
      "\\nThis is YOUR trigger. OK = place the order.";
    if(!confirm(msg)) return;
    b.disabled=true;
    fetch('/execute',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{ref_id:b.dataset.ref,token:TOKEN}})}})
    .then(function(r){{return r.json();}})
    .then(function(d){{
      var el=document.getElementById('rs-'+b.dataset.ref);
      el.textContent=(d.ok?'✓ ':'✗ ')+d.detail+
        (d.result?('\\n'+JSON.stringify(d.result,null,1)):'');
      if(!d.ok) b.disabled=false; else b.style.display='none';
    }})
    .catch(function(e){{b.disabled=false;alert('request failed: '+e);}});
  }});
}});
</script></body></html>"""


# ---------- http ----------

def _host_ok(headers) -> bool:
    h = (headers.get("Host") or "").split(":")[0]
    return h in ("127.0.0.1", "localhost")


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not _host_ok(self.headers):
            self.send_error(403, "Bad Host header.")
            return
        if self.path not in ("/", "/index.html"):
            self.send_error(404, "Only / is served.")
            return
        page = _render(_latest_plan()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        if self.path != "/execute":
            self._json(404, {"ok": False, "detail": "unknown endpoint"})
            return
        if not _host_ok(self.headers):
            self._json(403, {"ok": False, "detail": "bad Host header"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"ok": False, "detail": "bad request body"})
            return
        if body.get("token") != TOKEN:
            self._json(403, {"ok": False, "detail": "bad token (reload the page)"})
            return
        try:
            ok, detail, result = try_execute(str(body.get("ref_id", "")))
        except Exception as e:
            self._json(500, {"ok": False, "detail": f"{type(e).__name__}: {e}"})
            return
        self._json(200 if ok else 409,
                   {"ok": ok, "detail": detail, "result": result})

    def log_message(self, *args):
        pass


def main() -> None:
    global EXECUTOR, EXEC_LABEL
    from .live_executor import get_executor
    EXECUTOR, EXEC_LABEL = get_executor()   # robinhood login/MFA happens here, in YOUR terminal

    server = HTTPServer((HOST, PORT), Handler)
    print(f"Ticket console at http://{HOST}:{PORT}")
    print(f"Executor: {EXEC_LABEL}")
    if "REAL MONEY" in EXEC_LABEL:
        print("!!! REAL-MONEY EXECUTOR ACTIVE — every Execute click places a real order.")
    else:
        print("Paper executor: Execute clicks record simulated fills only.")
    print("Localhost only. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
