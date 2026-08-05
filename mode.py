"""
Trading mode switch — change the whole system's aggression in one command.

    python -m trading_agent.mode status       # what's set right now
    python -m trading_agent.mode aggressive   # concentrated, bigger, more active
    python -m trading_agent.mode balanced     # the default middle setting
    python -m trading_agent.mode sloth        # no NEW buys; exits stay live

Profiles only change SIZING and ACTIVITY. They never change the risk governor,
the daily caps, or the arming switches — arming stays a separate deliberate act.

IMPORTANT: 'sloth' pauses buying, NOT selling. Exits (momentum rank drops,
RSI(2) targets, stop-losses, Berkshire drops) keep running in every mode. You
must always be able to get out of a position.
"""
from __future__ import annotations

import os
import sys

from .env_file import env_path, load_env_file, update_env_values

load_env_file()

PROFILES = {
    "aggressive": {
        "TRADING_PAUSE_BUYS": "0",
        "MOMENTUM_TOP_N": "3",       # max concentration (backtest +34.8% CAGR)
        "MOMENTUM_DOLLARS": "700",
        "RSI2_MAX_POS": "5",
        "RSI2_DOLLARS": "400",
    },
    "balanced": {
        "TRADING_PAUSE_BUYS": "0",
        "MOMENTUM_TOP_N": "5",       # Mom5 (backtest +31.7% CAGR)
        "MOMENTUM_DOLLARS": "400",
        "RSI2_MAX_POS": "3",
        "RSI2_DOLLARS": "300",
    },
    "sloth": {
        "TRADING_PAUSE_BUYS": "1",   # no new entries; exits unaffected
        "MOMENTUM_TOP_N": "5",
        "MOMENTUM_DOLLARS": "200",
        "RSI2_MAX_POS": "0",
        "RSI2_DOLLARS": "200",
    },
}

DESCRIPTIONS = {
    "aggressive": "3 concentrated momentum names @ $700 + up to 5 RSI2 swings @ $400",
    "balanced":   "5 momentum names @ $400 + up to 3 RSI2 swings @ $300",
    "sloth":      "NO new buys. Exits still fire. Existing positions ride.",
}


def buys_paused() -> bool:
    """Checked by every bot before placing a BUY. Never gates sells."""
    return os.environ.get("TRADING_PAUSE_BUYS") == "1"


def status() -> None:
    keys = ["TRADING_PAUSE_BUYS", "MOMENTUM_TOP_N", "MOMENTUM_DOLLARS",
            "RSI2_MAX_POS", "RSI2_DOLLARS"]
    cur = {k: os.environ.get(k, "") for k in keys}
    match = [n for n, p in PROFILES.items()
             if all(cur.get(k, "") == v for k, v in p.items())]
    print(f"Current mode: {match[0].upper() if match else 'CUSTOM'}")
    if match:
        print(f"  {DESCRIPTIONS[match[0]]}")
    print()
    for k in keys:
        print(f"  {k:<20} {cur[k] or '(default)'}")
    print(f"\n  buys paused: {buys_paused()}   (exits always run)")
    print(f"  config file: {env_path()}")
    print("\nSwitch with: python -m trading_agent.mode "
          "[aggressive|balanced|sloth]")


def apply(name: str) -> None:
    if name not in PROFILES:
        raise SystemExit(f"Unknown mode '{name}'. "
                         f"Choose: {', '.join(PROFILES)}")
    update_env_values(PROFILES[name])
    print(f"Mode set: {name.upper()}")
    print(f"  {DESCRIPTIONS[name]}")
    for k, v in PROFILES[name].items():
        print(f"  {k:<20} {v}")
    print("\nTakes effect on the next scheduled bot run (within 15 min).")
    if name == "sloth":
        print("  NOTE: exits/stop-losses remain fully active.")


def main() -> None:
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    status() if arg == "status" else apply(arg)


if __name__ == "__main__":
    main()
