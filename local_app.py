"""
Ticket console — PRIVATE website with 2FA and a gated Execute button.

Lists today's order tickets with reasons, governor verdicts, and (when armed)
an EXECUTE button that fires YOUR executor (live_executor.py — your Robinhood
login, your click). Nothing here routes an order through an AI or the public
site, ever.

Access control
--------------
  TICKET_APP_PASSWORD      enable login: this password is required
  TICKET_APP_TOTP_SECRET   also require a 6-digit authenticator code (2FA)
  TICKET_APP_BIND          bind address (default 127.0.0.1). Anything other
                           than localhost REQUIRES password+TOTP to be set, or
                           the server refuses to start.

Generate a 2FA secret (add it to Google Authenticator / 1Password / Authy):
    python -m trading_agent.local_app --gen-totp

Typical private-website setup:
    export TICKET_APP_PASSWORD='a long passphrase'
    export TICKET_APP_TOTP_SECRET='BASE32SECRETFROMGENTOTP'
    python -m trading_agent.local_app
For phone access, prefer a private overlay network (e.g. Tailscale) over
opening ports: keep the bind on 127.0.0.1 and reach it through the tailnet,
or bind to your tailscale IP. Avoid 0.0.0.0 on a network you don't control.

Execute gates (ALL must hold): authenticated session (when auth is on) ->
localhost/known Host header -> CSRF token -> switch LIVE-armed -> ticket
governor-approved -> your confirm click -> no double-fire -> daily trade cap.
Every execution is appended to data/private/executions.jsonl.
"""
from __future__ import annotations

import base64
import glob
import hashlib
import hmac
import html
import json
import os
import secrets
import struct
import sys
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from .config import RiskLimits
from .env_file import env_path, init_env_file, load_env_file, update_env_values
from .poc.order import Order, OrderType, Side
from .trade_plan import PRIVATE_DIR

_ENV_LOADED = load_env_file()   # ~/.trading_agent.env fills in unset vars

BIND = os.environ.get("TICKET_APP_BIND", "127.0.0.1")
PORT = int(os.environ.get("TICKET_APP_PORT", "8787"))
PASSWORD = os.environ.get("TICKET_APP_PASSWORD")
TOTP_SECRET = os.environ.get("TICKET_APP_TOTP_SECRET")
EXECUTIONS = os.path.join(PRIVATE_DIR, "executions.jsonl")

TOKEN = secrets.token_hex(16)            # per-run CSRF token
SESSIONS: dict[str, float] = {}          # session token -> expiry epoch
SESSION_TTL = 12 * 3600
FAILED: dict[str, list[float]] = {}      # client ip -> recent failure times
MAX_FAILS, FAIL_WINDOW = 5, 300.0

EXECUTOR = None
EXEC_LABEL = "not initialized"
LIMITS = RiskLimits()


# ---------- TOTP (RFC 6238, stdlib only) ----------

def totp_code(secret_b32: str, at: float | None = None) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    counter = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    num = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return str(num % 1_000_000).zfill(6)


def totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    now = time.time()
    code = (code or "").strip()
    return any(hmac.compare_digest(totp_code(secret_b32, now + i * 30), code)
               for i in range(-window, window + 1))


def gen_totp_secret() -> None:
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    print("TOTP secret (put in your authenticator app, keep private):")
    print(f"  {secret}")
    print("otpauth URI (paste/scan into Google Authenticator, 1Password, Authy):")
    print(f"  otpauth://totp/ticket-console?secret={secret}&issuer=trading_agent")
    print("Then: export TICKET_APP_TOTP_SECRET=" + secret)


# ---------- auth ----------

def auth_enabled() -> bool:
    return bool(PASSWORD)


def session_ok(headers) -> bool:
    if not auth_enabled():
        return True
    cookie = headers.get("Cookie") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "session" and SESSIONS.get(v, 0) > time.time():
            return True
    return False


def locked_out(ip: str) -> bool:
    now = time.time()
    FAILED[ip] = [t for t in FAILED.get(ip, []) if now - t < FAIL_WINDOW]
    return len(FAILED[ip]) >= MAX_FAILS


def try_login(ip: str, password: str, code: str) -> str | None:
    """Returns a new session token on success, else None."""
    ok = bool(PASSWORD) and hmac.compare_digest(password or "", PASSWORD)
    if ok and TOTP_SECRET:
        ok = totp_verify(TOTP_SECRET, code)
    if not ok:
        FAILED.setdefault(ip, []).append(time.time())
        return None
    tok = secrets.token_hex(24)
    SESSIONS[tok] = time.time() + SESSION_TTL
    return tok


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


# ---------- the execute gate ----------

def try_execute(ref_id: str) -> tuple[bool, str, dict | None]:
    plan = _latest_plan()
    if plan is None:
        return False, "no trade plan on disk", None
    if not plan.get("armed"):
        return False, "switch is PAPER — arm with TRADING_GO_LIVE=1 first", None

    ticket = next((t for t in plan.get("tickets", [])
                   if t.get("broker_params", {}).get("ref_id") == ref_id), None)
    if ticket is None:
        return False, "unknown ticket ref_id", None
    if not ticket.get("approved") or ticket.get("status") != "live-armed":
        return False, f"ticket not executable (status={ticket.get('status')})", None

    if ref_id in _executed_ref_ids():
        return False, "already executed (double-fire blocked)", None
    if _executions_today() >= LIMITS.max_trades_per_day:
        return False, f"daily trade cap reached ({LIMITS.max_trades_per_day}/day)", None

    order = _order_from_params(ticket["broker_params"])
    errs = order.validate()
    if errs:
        return False, f"order failed validation: {errs}", None

    result = EXECUTOR.place(order)
    _record_execution(ref_id, order.describe(), result)
    return True, "submitted", result


# ---------- pages ----------

def _settings_page(msg: str = "", generated: str = "") -> str:
    plan = _latest_plan()
    armed_now = bool(plan and plan.get("armed"))
    totp_state = "configured" if TOTP_SECRET else "NOT set"
    acct = os.environ.get("TRADING_ACCOUNT_NUMBER", "")
    user = os.environ.get("RH_USERNAME", "")
    execu = os.environ.get("TRADING_EXECUTOR", "paper")
    note = f"<p class='ok'>{html.escape(msg)}</p>" if msg else ""
    gen = ""
    if generated:
        gen = (f"<div class='gen'><b>New 2FA secret — add to your authenticator now:</b>"
               f"<br><code>otpauth://totp/ticket-console?secret={generated}"
               f"&issuer=trading_agent</code></div>")
    rb_sel = " selected" if execu == "robinhood" else ""
    pp_sel = " selected" if execu != "robinhood" else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket console — settings</title><style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
   max-width:520px;margin:6vh auto;padding:0 16px;color:#1a1a1a}}
 h1{{font-size:1.2rem}} h2{{font-size:.95rem;margin:22px 0 6px;color:#333}}
 label{{font-size:.85rem;color:#444;display:block;margin-top:10px}}
 input,select{{width:100%;padding:9px 10px;margin-top:4px;border:1px solid #ccc;
   border-radius:6px;font-size:1rem;box-sizing:border-box}}
 .hint{{font-size:.75rem;color:#888;margin-top:2px}}
 .row{{display:flex;align-items:center;gap:8px;margin-top:14px}}
 .row input{{width:auto;margin:0}}
 button{{margin-top:20px;width:100%;padding:11px;border:none;border-radius:6px;
   background:#1a1a1a;color:#fff;font-size:.95rem;cursor:pointer}}
 .ok{{color:#1b5e20;font-size:.85rem}} .gen{{background:#fff8e1;border:1px solid #e0c46c;
   border-radius:6px;padding:10px;font-size:.78rem;margin:10px 0;word-break:break-all}}
 .warn{{background:#fdecea;border:1px solid #e0a0a0;border-radius:6px;padding:10px;
   font-size:.78rem;color:#7d1010;margin:10px 0}}
 a{{font-size:.85rem}} code{{font-family:ui-monospace,Menlo,monospace}}
</style></head><body>
<h1>Console settings</h1>
{note}{gen}
<p class="hint">Saved to your private config file. Leave a password field blank to
keep the current one. <b>Restart the console after saving</b> for execution and
arming changes to take effect (the Robinhood login/MFA happens at startup).</p>
<div class="warn">Arming LIVE + Robinhood executor means each Execute click places
a REAL order. Currently armed: <b>{'YES' if armed_now else 'no'}</b>.</div>
<form method="POST" action="/settings">
<input type="hidden" name="token" value="{TOKEN}">
<h2>Console access</h2>
<label>Console sign-in password
<input type="password" name="console_password" autocomplete="new-password"
 placeholder="leave blank to keep current"></label>
<div class="row"><input type="checkbox" name="regen_totp" id="rt">
<label for="rt" style="margin:0">Generate a new 2FA secret (current: {totp_state})</label></div>
<h2>Account &amp; execution</h2>
<label>Robinhood account number
<input name="account_number" value="{html.escape(acct)}"></label>
<label>Robinhood email
<input name="rh_username" value="{html.escape(user)}" autocomplete="username"></label>
<label>Robinhood password
<input type="password" name="rh_password" autocomplete="new-password"
 placeholder="leave blank to keep current"></label>
<label>Executor
<select name="executor"><option value="paper"{pp_sel}>paper (simulated fills)</option>
<option value="robinhood"{rb_sel}>robinhood (REAL MONEY)</option></select></label>
<div class="row"><input type="checkbox" name="go_live" id="gl">
<label for="gl" style="margin:0">Arm LIVE trading (TRADING_GO_LIVE=1)</label></div>
<button type="submit">Save settings</button>
</form>
<p style="margin-top:18px"><a href="/">&larr; back to tickets</a></p>
</body></html>"""


def _login_page(msg: str = "") -> str:
    totp_field = ("<label>2FA code<br><input name='code' inputmode='numeric' "
                  "autocomplete='one-time-code' placeholder='123456'></label><br>"
                  if TOTP_SECRET else "")
    note = f"<p class='err'>{html.escape(msg)}</p>" if msg else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket console — sign in</title><style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
   max-width:360px;margin:12vh auto;padding:0 16px;color:#1a1a1a}}
 h1{{font-size:1.15rem}} label{{font-size:.9rem;color:#444}}
 input{{width:100%;padding:9px 10px;margin:6px 0 14px;border:1px solid #ccc;
   border-radius:6px;font-size:1rem}}
 button{{width:100%;padding:10px;border:none;border-radius:6px;background:#1a1a1a;
   color:#fff;font-size:.95rem;cursor:pointer}}
 .err{{color:#a01919;font-size:.85rem}}
</style></head><body>
<h1>Ticket console</h1>
{note}
<form method="POST" action="/login">
<label>Password<br><input type="password" name="password" autofocus></label><br>
{totp_field}
<button type="submit">Sign in</button>
</form>
</body></html>"""


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

    auth_note = ("password + 2FA" if TOTP_SECRET else "password") if auth_enabled() else "no auth (localhost)"
    body = f"""
      <div class="switch {'armed' if armed else 'paper'}">Switch: {switch}
        <span class="sub">plan {html.escape(str(plan.get('date')))} ·
        executions today: {_executions_today()}/{LIMITS.max_trades_per_day} ·
        access: {auth_note}</span>
      </div>
      <div class="execline {'x-real' if real else 'x-paper'}">Executor: {html.escape(EXEC_LABEL)}
        <a href="/settings" style="float:right;color:inherit">settings</a></div>
      {''.join(rows) or "<p class='empty'>No tickets in today's plan.</p>"}
      <p class="foot">private console · every execute is confirmed by you and logged ·
      the trigger is yours</p>
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
    if BIND in ("127.0.0.1", "localhost"):
        return h in ("127.0.0.1", "localhost")
    return h in ("127.0.0.1", "localhost", BIND)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, code: int, page: str, cookie: str | None = None) -> None:
        raw = page.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie",
                             f"session={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not _host_ok(self.headers):
            self.send_error(403, "Bad Host header.")
            return
        if self.path not in ("/", "/index.html", "/login", "/settings"):
            self.send_error(404, "Only / is served.")
            return
        # Always open with a gate: setup screen if nothing is configured yet,
        # sign-in if a password exists. Tickets never show before the gate.
        if not auth_enabled():
            self._html(200, _settings_page(
                "Welcome — set a console sign-in password to get started, then "
                "restart the console. Until then the console is locked."))
            return
        if not session_ok(self.headers):
            self._html(200, _login_page())
            return
        if self.path == "/settings":
            self._html(200, _settings_page())
            return
        self._html(200, _render(_latest_plan()))

    def do_POST(self):
        if not _host_ok(self.headers):
            self._json(403, {"ok": False, "detail": "bad Host header"})
            return
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""

        if self.path == "/login":
            ip = self.client_address[0]
            if locked_out(ip):
                self._html(429, _login_page("Too many attempts. Wait 5 minutes."))
                return
            form = parse_qs(raw.decode("utf-8", "replace"))
            tok = try_login(ip, (form.get("password") or [""])[0],
                            (form.get("code") or [""])[0])
            if tok is None:
                self._html(403, _login_page("Wrong password or code."))
                return
            self._html(200, _render(_latest_plan()), cookie=tok)
            return

        if self.path == "/settings":
            if auth_enabled() and not session_ok(self.headers):
                self._html(401, _login_page("Sign in first."))
                return
            form = parse_qs(raw.decode("utf-8", "replace"))
            if (form.get("token") or [""])[0] != TOKEN:
                self._html(403, _settings_page("Bad token — reload the page."))
                return
            updates: dict[str, str] = {
                "TRADING_GO_LIVE": "1" if form.get("go_live") else "0",
                "TRADING_EXECUTOR": (form.get("executor") or ["paper"])[0],
            }
            for field, key in (("account_number", "TRADING_ACCOUNT_NUMBER"),
                               ("rh_username", "RH_USERNAME"),
                               ("console_password", "TICKET_APP_PASSWORD"),
                               ("rh_password", "RH_PASSWORD")):
                val = (form.get(field) or [""])[0].strip()
                if val:
                    updates[key] = val
            generated = ""
            if form.get("regen_totp"):
                import base64 as _b64
                generated = _b64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
                updates["TICKET_APP_TOTP_SECRET"] = generated
            try:
                update_env_values(updates)
            except Exception as e:
                self._html(500, _settings_page(f"Save failed: {e}"))
                return
            self._html(200, _settings_page(
                "Saved. Restart the console (Ctrl-C, then re-run) for execution "
                "and arming changes to take effect.", generated))
            return

        if self.path != "/execute":
            self._json(404, {"ok": False, "detail": "unknown endpoint"})
            return
        if auth_enabled() and not session_ok(self.headers):
            self._json(401, {"ok": False, "detail": "not signed in"})
            return
        try:
            body = json.loads(raw or b"{}")
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


def run_setup() -> None:
    """Guided setup: prompts fill ~/.trading_agent.env directly. Password
    typing is HIDDEN (getpass) — nothing sensitive echoes to the screen."""
    import getpass

    path = env_path()
    if not os.path.exists(path):
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        init_env_file(secret, account_number="899433726")
        print(f"Created {path}")
        print("ADD THIS 2FA SECRET to Google Authenticator now (scan/paste):")
        print(f"  otpauth://totp/ticket-console?secret={secret}&issuer=trading_agent\n")

    print("Console sign-in password — invent your own words; typing is hidden.")
    while True:
        pw1 = getpass.getpass("  console password: ").strip()
        if len(pw1) < 8:
            print("  too short (8+ characters) — try again")
            continue
        if pw1 == getpass.getpass("  repeat it: ").strip():
            break
        print("  didn't match — try again")

    current_user = os.environ.get("RH_USERNAME", "")
    user = input(f"Robinhood email [{current_user or 'required'}]: ").strip() or current_user
    rh_pw = getpass.getpass("Robinhood password (typing hidden): ").strip()

    arm = input("Arm LIVE trading? Execute clicks will place REAL orders. [y/N]: ").strip().lower()
    go_live = "1" if arm == "y" else "0"
    executor = "robinhood" if arm == "y" else "paper"

    update_env_values({
        "TICKET_APP_PASSWORD": pw1,
        "RH_USERNAME": user,
        "RH_PASSWORD": rh_pw,
        "TRADING_GO_LIVE": go_live,
        "TRADING_EXECUTOR": executor,
    })
    print(f"\nSaved to {path} (owner-only).")
    print("Mode: " + ("LIVE-ARMED — real money on Execute clicks"
                      if arm == "y" else "paper (safe)"))
    print("Next:  python -m trading_agent.briefing_daily")
    print("Then:  python -m trading_agent.local_app")


def main() -> None:
    global EXECUTOR, EXEC_LABEL
    if "--setup" in sys.argv:
        run_setup()
        return
    if "--gen-totp" in sys.argv:
        gen_totp_secret()
        return
    if "--init-env" in sys.argv:
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        path = init_env_file(secret, account_number="899433726")
        print(f"Created {path} (owner-only).")
        print("Add the 2FA secret to your authenticator app (scan/paste):")
        print(f"  otpauth://totp/ticket-console?secret={secret}&issuer=trading_agent")
        print("Now edit the file and fill in:")
        print("  TICKET_APP_PASSWORD=   (words of your own)")
        print("  RH_USERNAME= / RH_PASSWORD=   (only when you go live)")
        print("  TRADING_GO_LIVE=1 and TRADING_EXECUTOR=robinhood  (only when you go live)")
        print(f"Edit with: open -t {path}")
        return

    if BIND not in ("127.0.0.1", "localhost") and not (PASSWORD and TOTP_SECRET):
        raise SystemExit(
            f"Refusing to bind {BIND} without full auth. A non-localhost bind "
            f"exposes the console beyond this machine, so BOTH "
            f"TICKET_APP_PASSWORD and TICKET_APP_TOTP_SECRET are required. "
            f"(Prefer keeping 127.0.0.1 and reaching it over Tailscale.)"
        )

    from .live_executor import PaperExecutor, get_executor
    if os.environ.get("TRADING_EXECUTOR", "paper").lower() == "robinhood" and not PASSWORD:
        # No console password => the execute endpoint would be unauthenticated.
        # Never run a real-money executor open like that. Force paper until a
        # sign-in password is set (via the setup screen).
        EXECUTOR, EXEC_LABEL = PaperExecutor(), "paper (set a console password to enable live)"
        print("SAFETY: real-money executor disabled — no console password set.")
        print(f"        Set one at http://{BIND}:{PORT}/settings, then restart.")
    else:
      try:
        EXECUTOR, EXEC_LABEL = get_executor()   # robinhood login/MFA happens here, in YOUR terminal
      except Exception as e:
        # Don't crash on a bad/incomplete live config — degrade to paper so the
        # console (and its settings page) stays reachable to fix it.
        EXECUTOR, EXEC_LABEL = PaperExecutor(), "paper (live setup incomplete)"
        print(f"NOTE: falling back to paper — {type(e).__name__}: {e}")
        print("      Fix it at http://%s:%d/settings, then restart." % (BIND, PORT))

    server = HTTPServer((BIND, PORT), Handler)
    print(f"Ticket console at http://{BIND}:{PORT}")
    print(f"Access: " + ("password + 2FA" if (PASSWORD and TOTP_SECRET)
                         else "password only" if PASSWORD
                         else "no auth (localhost only)"))
    print(f"Executor: {EXEC_LABEL}")
    if "REAL MONEY" in EXEC_LABEL:
        print("!!! REAL-MONEY EXECUTOR ACTIVE — every Execute click places a real order.")
    else:
        print("Paper executor: Execute clicks record simulated fills only.")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
