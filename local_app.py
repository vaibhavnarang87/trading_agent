"""
Local ticket console — a PRIVATE, localhost-only mini-app.

Serves a single page listing today's risk-approved order tickets, each with a
"copy the place_equity_order(...) call" button. You copy the call and run it
yourself in your own Robinhood/MCP session. That's the whole point of the copy
button: it hands you the exact, correct call — it does not fire it.

Safety properties, on purpose:
  - Binds to 127.0.0.1 ONLY. Not your LAN, not the internet. If you can't open a
    terminal on this machine, you can't reach this page.
  - No POST/execute route exists. The server can only READ the local plan file
    and render it. There is no code path here that places an order.
  - Reads the git-ignored data/private/ plan, so nothing it shows is published.

    python -m trading_agent.local_app         # then open http://127.0.0.1:8787

Stop it with Ctrl-C.
"""
from __future__ import annotations

import glob
import html
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from .trade_plan import PRIVATE_DIR

HOST = "127.0.0.1"     # localhost only — deliberately not 0.0.0.0
PORT = int(os.environ.get("TICKET_APP_PORT", "8787"))


def _latest_plan() -> dict | None:
    files = sorted(glob.glob(os.path.join(PRIVATE_DIR, "trade_plan_*.json")))
    if not files:
        return None
    return json.load(open(files[-1]))


def _render(plan: dict | None) -> str:
    if plan is None:
        body = ("<p class='empty'>No trade plan found. Run "
                "<code>python -m trading_agent.briefing_daily</code> first.</p>")
        return _page(body, "No plan")

    armed = plan.get("armed", False)
    switch = ("LIVE-ARMED" if armed else "PAPER (safe)")
    switch_cls = "armed" if armed else "paper"
    tickets = plan.get("tickets", [])
    approved = [t for t in tickets if t.get("approved")]

    rows = []
    for t in tickets:
        status = t.get("status", "paper")
        badge = {"live-armed": ("READY", "b-ready"),
                 "paper": ("paper", "b-paper"),
                 "vetoed": ("vetoed", "b-veto")}.get(status, ("paper", "b-paper"))
        call = f"place_equity_order(**{t.get('broker_params')})"
        reasons = "; ".join(t.get("reasons", []))
        can_copy = t.get("approved") and status == "live-armed"
        copy_btn = (
            f"<button class='copy' data-call=\"{html.escape(call, quote=True)}\">"
            f"<i class='ti ti-copy'></i> copy call</button>"
            if can_copy else
            "<span class='muted'>copy enabled when live-armed &amp; approved</span>"
        )
        rows.append(f"""
        <div class="ticket">
          <div class="thead">
            <span class="desc">{html.escape(t.get('describe',''))}</span>
            <span class="badge {badge[1]}">{badge[0]}</span>
          </div>
          <div class="why">{html.escape(reasons)}</div>
          <div class="gov">governor: {html.escape(t.get('governor_reason',''))}</div>
          <div class="callrow">
            <code class="call">{html.escape(call)}</code>
            {copy_btn}
          </div>
        </div>""")

    note = ("You copy the call and run it yourself in Robinhood. This page never "
            "places an order." if armed else
            "PAPER mode — nothing here is live. Flip the switch in briefing_daily.py "
            "only after a strategy earns it.")

    body = f"""
      <div class="switch {switch_cls}">Switch: {switch}
        <span class="sub">plan {html.escape(str(plan.get('date')))} ·
        {len(approved)}/{len(tickets)} risk-approved</span>
      </div>
      <p class="note">{note}</p>
      {''.join(rows) or "<p class='empty'>No tickets in today's plan.</p>"}
      <p class="foot">localhost only (127.0.0.1) · read-only · the trigger stays yours</p>
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
 .switch{{font-weight:600;padding:10px 14px;border-radius:8px;margin-bottom:8px}}
 .switch .sub{{display:block;font-weight:400;font-size:.8rem;color:#555;margin-top:2px}}
 .paper{{background:#e8f5e9;color:#1b5e20}} .armed{{background:#fff3e0;color:#b23c00}}
 .note{{font-size:.85rem;color:#555;margin:0 0 20px}}
 .ticket{{background:#fff;border:1px solid #e2e2e2;border-radius:10px;padding:14px 16px;margin-bottom:12px}}
 .thead{{display:flex;justify-content:space-between;align-items:center}}
 .desc{{font-weight:600}}
 .badge{{font-size:.72rem;padding:3px 9px;border-radius:6px;font-weight:600}}
 .b-ready{{background:#fff3e0;color:#b23c00}} .b-paper{{background:#eee;color:#555}}
 .b-veto{{background:#fdecea;color:#a01919}}
 .why{{font-size:.85rem;color:#444;margin:8px 0 2px}}
 .gov{{font-size:.78rem;color:#777;margin-bottom:10px}}
 .callrow{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
 .call{{flex:1;min-width:220px;background:#f5f5f5;border-radius:6px;padding:8px 10px;
   font-size:.72rem;overflow-x:auto;white-space:nowrap}}
 button.copy{{border:1px solid #b23c00;background:#fff;color:#b23c00;border-radius:6px;
   padding:7px 12px;font-size:.8rem;cursor:pointer;white-space:nowrap}}
 button.copy:hover{{background:#fff3e0}}
 .muted{{font-size:.75rem;color:#999}}
 .foot{{font-size:.75rem;color:#999;margin-top:24px;text-align:center}}
 .empty{{color:#777}} code{{font-family:ui-monospace,Menlo,monospace}}
</style></head><body>
<h1>Ticket console</h1>
{body}
<script>
document.querySelectorAll('button.copy').forEach(function(b){{
  b.addEventListener('click',function(){{
    navigator.clipboard.writeText(b.dataset.call).then(function(){{
      var o=b.innerHTML; b.textContent='copied ✓';
      setTimeout(function(){{b.innerHTML=o;}},1200);
    }});
  }});
}});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404, "Only / is served (read-only console).")
            return
        page = _render(_latest_plan()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    # There is intentionally no do_POST: this console cannot place orders.

    def log_message(self, *args):
        pass  # quiet


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Ticket console (read-only) at http://{HOST}:{PORT}")
    print("Localhost only. It shows tickets and copies the call — it never places one.")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
