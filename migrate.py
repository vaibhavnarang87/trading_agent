"""
Package this agent's operational state for a move to another machine.

The repo carries the CODE. Everything that makes the bots know what they own
lives outside git on purpose:

  * data/private/  — strategy state, stop cooldowns, the execution ledger
  * ~/.trading_agent.env — arming switches, account number, notify topic
  * macOS Keychain — the broker password (machine-bound; NEVER bundled)
  * ~/.tokens/     — the broker session (device-bound; NEVER bundled)

Losing data/private is not cosmetic: the bots would forget which strategy owns
which position, momentum would re-adopt clone holdings, and stop cooldowns
would reset to zero.

    python -m trading_agent.migrate            # build the bundle
    python -m trading_agent.migrate --restore <bundle.tar.gz>

Secrets are never written to the bundle. You re-enter the broker password on
the new machine and log in once there.
"""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "data", "private")
ENV = os.path.expanduser("~/.trading_agent.env")

# Settings that are safe to carry. Anything resembling a secret is re-entered
# on the new machine instead of travelling in a tarball.
SECRET_KEYS = ("PASSWORD", "SECRET", "TOKEN", "HASH", "KEY", "TOTP")
# Ledger/state worth carrying. Logs are left behind — they are large and the
# new machine writes its own.
STATE_FILES = ("momentum_state.json", "rsi2_state.json", "clone_state.json",
               "executions.jsonl", "rsi2_symbols.json")


def _safe_env() -> tuple[str, list[str]]:
    """Env file with secret-looking values stripped. Returns (text, redacted)."""
    out, redacted = [], []
    if os.path.exists(ENV):
        for line in open(ENV):
            k = line.split("=", 1)[0].strip()
            if any(t in k.upper() for t in SECRET_KEYS) and "=" in line:
                out.append(f"{k}=            # RE-ENTER ON THE NEW MACHINE\n")
                redacted.append(k)
            else:
                out.append(line)
    return "".join(out), redacted


def build() -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest = os.path.expanduser(f"~/trading_agent_state_{stamp}.tar.gz")
    env_text, redacted = _safe_env()

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "trading_agent.env"), "w") as f:
            f.write(env_text)
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(os.path.join(tmp, "trading_agent.env"),
                    arcname="trading_agent.env")
            for name in STATE_FILES:
                p = os.path.join(PRIVATE, name)
                if os.path.exists(p):
                    tar.add(p, arcname=f"private/{name}")
            for p in sorted(os.listdir(os.path.expanduser("~/Library/LaunchAgents"))
                            if os.path.isdir(os.path.expanduser("~/Library/LaunchAgents"))
                            else []):
                if p.startswith("com.trading-agent."):
                    tar.add(os.path.join(os.path.expanduser("~/Library/LaunchAgents"), p),
                            arcname=f"launchagents/{p}")

    size = os.path.getsize(dest)
    print(f"Bundle: {dest}  ({size/1024:.1f} KB)")
    print("\nIncluded: strategy state, execution ledger, schedules, settings.")
    print("NOT included (by design):")
    print("  * the Robinhood password — in the macOS Keychain, machine-bound")
    print("  * the broker session token — device-bound, must be re-created")
    if redacted:
        print(f"  * redacted from the settings file: {', '.join(redacted)}")
    print("\nOn the new machine:")
    print("  1. git clone git@github.com:vaibhavnarang87/trading_agent.git")
    print("  2. python3 -m venv .venv && source .venv/bin/activate")
    print("     pip install -r requirements.txt")
    print("  3. python -m trading_agent.migrate --restore <this bundle>")
    print("  4. python -m trading_agent.local_app --setup   # re-enter password")
    print("  5. python -m trading_agent.local_app           # approve device prompt")
    print("  6. python -m trading_agent.reconcile           # verify before arming")
    print("\nArm the strategies LAST, and only after reconcile is clean.")


def restore(bundle: str) -> None:
    if not os.path.exists(bundle):
        raise SystemExit(f"No such bundle: {bundle}")
    os.makedirs(PRIVATE, exist_ok=True)
    with tarfile.open(bundle, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name.startswith("private/"):
                m.name = os.path.basename(m.name)
                tar.extract(m, PRIVATE)
                print(f"  restored state: {m.name}")
            elif m.name == "trading_agent.env":
                if os.path.exists(ENV):
                    print(f"  {ENV} exists — writing {ENV}.new instead")
                    m.name = os.path.basename(ENV) + ".new"
                    tar.extract(m, os.path.dirname(ENV))
                else:
                    m.name = os.path.basename(ENV)
                    tar.extract(m, os.path.dirname(ENV))
                    os.chmod(ENV, 0o600)
                print("  restored settings (secrets still blank)")
            elif m.name.startswith("launchagents/"):
                print(f"  schedule available: {os.path.basename(m.name)} "
                      f"(not installed — see below)")
    print("\nSchedules were NOT installed automatically: they hold absolute "
          "paths from the old machine.")
    print("Edit the paths, copy them to ~/Library/LaunchAgents/, then "
          "`launchctl load` each one.")
    print("On Linux, use systemd timers or cron instead — the bots are plain "
          "Python and do not need launchd.")
    print("\nNext: set the password, log in once, then run reconcile.")


if __name__ == "__main__":
    if "--restore" in sys.argv:
        restore(sys.argv[sys.argv.index("--restore") + 1])
    else:
        build()
