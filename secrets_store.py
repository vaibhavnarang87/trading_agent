"""
Secret handling — keep credentials out of plaintext on disk.

Two kinds of secret, handled the right way for each:

  - Console sign-in password: only ever COMPARED, never re-sent. Stored as a
    salted PBKDF2-SHA256 hash (in the env file, under TICKET_APP_PASSWORD_HASH).
    A hash is safe at rest — it can't be reversed to the password.

  - Robinhood password: must be handed to robin_stocks at login, so it has to
    be recoverable. Stored in the macOS Keychain (encrypted at rest, ACL-gated)
    via the `security` CLI — never in the plaintext env file.

migrate_plaintext_secrets() moves any secrets still sitting in the env file
into these stores and blanks the plaintext, verifying the move first so a
failure never loses your credentials. All of this is automatic on startup —
no extra steps for the user.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import subprocess

from .env_file import env_path, update_env_values

KEYCHAIN_SERVICE = "trading_agent"
_PBKDF2_ROUNDS = 240_000


# ---------- console password: salted hash ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------- macOS Keychain (via the `security` CLI, no dependency) ----------

def _keychain_available() -> bool:
    return os.uname().sysname == "Darwin" and os.path.exists("/usr/bin/security")


def keychain_set(account: str, value: str) -> bool:
    if not _keychain_available():
        return False
    # Delete any existing entry first so there's exactly one (duplicates make
    # find-generic-password non-deterministic). -T trusts the security binary
    # so later reads by this same tool don't trigger a GUI prompt.
    keychain_delete(account)
    r = subprocess.run(
        ["/usr/bin/security", "add-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", account, "-w", value,
         "-T", "/usr/bin/security"],
        capture_output=True, text=True)
    return r.returncode == 0


def keychain_get(account: str) -> str | None:
    if not _keychain_available():
        return None
    r = subprocess.run(
        ["/usr/bin/security", "find-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\n")


def keychain_delete(account: str) -> None:
    if _keychain_available():
        subprocess.run(
            ["/usr/bin/security", "delete-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", account],
            capture_output=True, text=True)


# ---------- typed accessors ----------

def set_console_password(password: str) -> None:
    h = hash_password(password)
    update_env_values({"TICKET_APP_PASSWORD_HASH": h, "TICKET_APP_PASSWORD": ""})
    # keep the running process in sync with what we just wrote to disk
    os.environ["TICKET_APP_PASSWORD_HASH"] = h
    os.environ.pop("TICKET_APP_PASSWORD", None)


def console_password_hash() -> str | None:
    return os.environ.get("TICKET_APP_PASSWORD_HASH") or None


def set_rh_password(password: str) -> bool:
    """Store the RH password in the Keychain. Returns True on success."""
    if keychain_set("rh_password", password):
        return keychain_get("rh_password") == password   # verify read-back
    return False


def get_rh_password() -> str | None:
    """RH password: Keychain first, then env fallback (so login never breaks)."""
    return keychain_get("rh_password") or (os.environ.get("RH_PASSWORD") or None)


# ---------- one-time migration of any plaintext still on disk ----------

def migrate_plaintext_secrets() -> list[str]:
    """Move plaintext secrets from the env file into hash/keychain, blanking
    the plaintext only after the move is verified. Idempotent. Returns notes."""
    notes: list[str] = []

    plain_console = os.environ.get("TICKET_APP_PASSWORD", "").strip()
    if plain_console and not os.environ.get("TICKET_APP_PASSWORD_HASH"):
        set_console_password(plain_console)   # updates file + os.environ
        notes.append("console password → salted hash (plaintext removed)")

    plain_rh = os.environ.get("RH_PASSWORD", "").strip()
    if plain_rh:
        if set_rh_password(plain_rh):
            update_env_values({"RH_PASSWORD": ""})
            os.environ.pop("RH_PASSWORD", None)
            notes.append("Robinhood password → macOS Keychain (plaintext removed)")
        else:
            notes.append("WARNING: could not store RH password in Keychain; "
                         "left it in the env file for now")
    return notes
