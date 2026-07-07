"""
Private env file — set your secrets once, the tools load them on every start.

File: ~/.trading_agent.env  (override path with TRADING_AGENT_ENV_FILE)
Format: KEY=VALUE, one per line. NO quotes, NO angle brackets, NO 'export'.
Full-line comments start with '#'. Values you leave empty are ignored.
Real shell exports always win over the file (the file fills in what's unset).

Create it with:  python -m trading_agent.local_app --init-env
The file is created owner-only (chmod 600) and lives in your HOME, outside
the repo — it can never be committed or published.
"""
from __future__ import annotations

import os
import stat


def env_path() -> str:
    return os.environ.get(
        "TRADING_AGENT_ENV_FILE",
        os.path.expanduser("~/.trading_agent.env"),
    )


def load_env_file(path: str | None = None) -> list[str]:
    """Load KEY=VALUE lines into os.environ (without overriding real exports).
    Returns the list of keys loaded. Missing file is fine — returns []."""
    path = path or env_path()
    if not os.path.exists(path):
        return []
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"WARNING: {path} is readable by other users — run: chmod 600 {path}")
    loaded: list[str] = []
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


TEMPLATE = """\
# trading_agent private config — owner-only file, lives outside the repo.
# Format: KEY=VALUE. No quotes, no <brackets>, no 'export'. Lines starting
# with # are comments. Empty values are ignored.

# --- console sign-in (private website) ---
# Password you type at the console login. Choose your own words.
TICKET_APP_PASSWORD=
# 2FA secret (generated for you; already in your authenticator if you scanned it)
TICKET_APP_TOTP_SECRET={totp_secret}

# --- the live switch (safe defaults: paper, nothing armed) ---
# Set to 1 only when you deliberately want live-armed tickets.
TRADING_GO_LIVE=0
TRADING_ACCOUNT_NUMBER={account_number}

# --- execution backend: paper (simulated) or robinhood (REAL MONEY) ---
TRADING_EXECUTOR=paper
# Your Robinhood login, used only by robin_stocks on this machine.
RH_USERNAME=
RH_PASSWORD=
"""


def init_env_file(totp_secret: str, account_number: str = "") -> str:
    """Create the env file with safe defaults + a fresh TOTP secret.
    Refuses to overwrite an existing file."""
    path = env_path()
    if os.path.exists(path):
        raise SystemExit(
            f"{path} already exists — edit it directly instead of re-initializing "
            f"(delete it first if you really want a fresh one)."
        )
    content = TEMPLATE.format(totp_secret=totp_secret, account_number=account_number)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path
