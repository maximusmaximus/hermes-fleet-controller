#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-pair.py
Zero-Trust device pairing & access key engine for the Hermes Fleet Controller Web Dashboard.
Manages time-limited access keys, 6-digit PINs, IP rate-limiting, and signed HMAC-SHA256 session tokens.
Enforces that the dashboard interface cannot be loaded without a valid Telegram-generated key or PIN.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import secrets

AUTH_DIR = "/opt/fleet/shared-workspace" if os.path.isdir("/opt/fleet/shared-workspace") else "/opt/fleet"
AUTH_FILE = os.environ.get("FLEET_AUTH_FILE", os.path.join(AUTH_DIR, "web-auth.json"))
TUNNEL_FILE = "/opt/fleet/tunnel-url.txt"
PIN_EXPIRY_SECONDS = 86400     # 24 hours
LOCKOUT_THRESHOLD = 5          # 5 attempts
LOCKOUT_DURATION_SECONDS = 900 # 15 minutes


def load_auth_db():
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    # Initialize default structure
    secret_key = secrets.token_hex(32)
    db = {
        "secret_key": secret_key,
        "active_pin": None,
        "active_key": None,
        "pin_expires_at": 0,
        "failed_attempts": {},  # ip: {"count": int, "locked_until": int}
        "valid_sessions": {}   # token: {"created_at": int, "client_ip": str}
    }
    save_auth_db(db)
    return db


def save_auth_db(db):
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    temp_file = AUTH_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(db, f, indent=2)
    try:
        os.chmod(temp_file, 0o666)
    except Exception:
        pass
    os.replace(temp_file, AUTH_FILE)



def generate_credentials():
    db = load_auth_db()
    pin = f"{secrets.randbelow(900000) + 100000}"
    key = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + PIN_EXPIRY_SECONDS
    db["active_pin"] = pin
    db["active_key"] = key
    db["pin_expires_at"] = expires_at
    save_auth_db(db)
    return pin, key, expires_at


def generate_pin():
    pin, _, expires_at = generate_credentials()
    return pin, expires_at


def verify_key_or_pin(attempt, client_ip):
    if not attempt:
        return False, None, "Access key or PIN required."
    attempt = str(attempt).strip()

    # If full URL or query string was pasted, extract key/pin/token
    if "?" in attempt:
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(attempt)
            qs = urllib.parse.parse_qs(parsed.query)
            extracted = qs.get("key", [""])[0] or qs.get("token", [""])[0] or qs.get("pin", [""])[0] or qs.get("auth", [""])[0] or qs.get("session", [""])[0]
            if extracted:
                attempt = extracted
        except Exception:
            pass

    # Clean numeric PIN (allow 123-456 or 123 456)
    clean_numeric = attempt.replace(" ", "").replace("-", "")
    if clean_numeric.isdigit() and len(clean_numeric) == 6:
        attempt = clean_numeric

    # If attempt is already an active session token, accept immediately
    if "." in attempt and verify_token(attempt):
        return True, attempt, "Success"

    db = load_auth_db()
    now = int(time.time())

    # Check IP lockout
    ip_record = db.get("failed_attempts", {}).get(client_ip, {"count": 0, "locked_until": 0})
    if ip_record.get("locked_until", 0) > now:
        remaining = ip_record["locked_until"] - now
        return False, None, f"IP is locked due to too many failed attempts. Try again in {remaining}s."

    # Check key / PIN validity
    active_pin = db.get("active_pin")
    active_key = db.get("active_key")
    pin_expiry = db.get("pin_expires_at", 0)

    if now > pin_expiry or (not active_pin and not active_key):
        return False, None, "Access key or PIN has expired or has not been generated."


    match_pin = bool(active_pin and hmac.compare_digest(attempt, str(active_pin).strip()))
    match_key = bool(active_key and hmac.compare_digest(attempt, str(active_key).strip()))

    if not (match_pin or match_key):
        # Record failure
        ip_record["count"] = ip_record.get("count", 0) + 1
        if ip_record["count"] >= LOCKOUT_THRESHOLD:
            ip_record["locked_until"] = now + LOCKOUT_DURATION_SECONDS
            ip_record["count"] = 0
            db.setdefault("failed_attempts", {})[client_ip] = ip_record
            save_auth_db(db)
            return False, None, f"Too many failed attempts. Locked out for {LOCKOUT_DURATION_SECONDS // 60} minutes."
        db.setdefault("failed_attempts", {})[client_ip] = ip_record
        save_auth_db(db)
        remaining_attempts = LOCKOUT_THRESHOLD - ip_record["count"]
        return False, None, f"Invalid access key or PIN. {remaining_attempts} attempt(s) remaining."

    # Successful verification! Reset failed attempts
    db.setdefault("failed_attempts", {}).pop(client_ip, None)

    # Generate cryptographically signed session token
    raw_token = secrets.token_hex(24)
    signature = hmac.new(
        db["secret_key"].encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    session_token = f"{raw_token}.{signature}"

    db.setdefault("valid_sessions", {})[session_token] = {
        "created_at": now,
        "client_ip": client_ip
    }
    save_auth_db(db)
    return True, session_token, "Success"


def verify_pin(pin_attempt, client_ip):
    return verify_key_or_pin(pin_attempt, client_ip)


def verify_token(token):
    if not token or "." not in token:
        return False
    db = load_auth_db()
    raw_token, signature = token.rsplit(".", 1)
    expected_sig = hmac.new(
        db["secret_key"].encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return False

    return token in db.get("valid_sessions", {})


def get_tunnel_url():
    candidates = [
        TUNNEL_FILE,
        "/opt/fleet/shared-workspace/tunnel-url.txt"
    ]
    for cand in candidates:
        if os.path.exists(cand):
            try:
                with open(cand) as f:
                    u = f.read().strip()
                    if u:
                        return u
            except Exception:
                pass
    return "https://worship-him-knight-jul.trycloudflare.com"



def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--verify", "-v"):
        if len(sys.argv) < 4:
            print("Usage: fleet-pair.py --verify <key_or_pin> <client_ip>")
            sys.exit(1)
        ok, token, msg = verify_key_or_pin(sys.argv[2], sys.argv[3])
        print(json.dumps({"success": ok, "token": token, "message": msg}))
        sys.exit(0 if ok else 1)

    if len(sys.argv) > 1 and sys.argv[1] in ("--check-token", "-c"):
        if len(sys.argv) < 3:
            print("Usage: fleet-pair.py --check-token <token>")
            sys.exit(1)
        valid = verify_token(sys.argv[2])
        print("valid" if valid else "invalid")
        sys.exit(0 if valid else 1)

    # Generate new credentials
    pin, key, expires_at = generate_credentials()
    now = int(time.time())
    ttl = expires_at - now
    tunnel_url = get_tunnel_url()
    direct_login_url = f"{tunnel_url}/?key={key}"

    if len(sys.argv) > 1 and sys.argv[1] in ("--json", "-j"):
        print(json.dumps({
            "pin": pin,
            "key": key,
            "expires_at": expires_at,
            "ttl_seconds": ttl,
            "url": tunnel_url,
            "direct_login_url": direct_login_url
        }, indent=2))
        sys.exit(0)

    ttl_str = f"{ttl // 3600} hours" if ttl >= 3600 else f"{ttl // 60} minutes"
    tg_text = (
        "🔐 *HERMES FLEET DASHBOARD ACCESS*\n\n"
        f"⚡ *One-Click Auto-Login Link*:\n{direct_login_url}\n\n"
        f"🔑 *Telegram Access Key* (for pasting):\n`{key}`\n\n"
        f"🔢 *Short PIN*: `{pin}`\n"
        f"⏳ *Valid For*: {ttl_str}\n"
        "🛡️ *Security*: The dashboard interface is PIN-protected and requires this key or PIN.\n\n"
        "👉 Tap the link above to immediately unlock the dashboard, or enter PIN into the login gate."
    )


    if len(sys.argv) > 1 and sys.argv[1] in ("--tg", "--telegram"):
        print(tg_text)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] in ("--send-tg", "-s"):
        notify_sh = os.path.join(os.path.dirname(AUTH_FILE), "bin", "fleet-telegram-notify.sh")
        if os.path.exists(notify_sh):
            import subprocess
            subprocess.call([notify_sh, tg_text])
        print(tg_text)
        sys.exit(0)

    print("=" * 60)
    print("       🔐 HERMES FLEET CONTROLLER - ACCESS KEY")
    print("=" * 60)
    print(f"\n  One-Click Direct Login URL:  \033[1;36m{direct_login_url}\033[0m")
    print(f"  Access Key (long string):    \033[1;32m{key}\033[0m")
    print(f"  Short 6-Digit PIN:           \033[1;33m{pin}\033[0m")
    print(f"  Valid for:                   {ttl // 60} minutes ({ttl} seconds)")
    print(f"  Rate-limit protection:       5 failed attempts = 15m lockout")
    print("\n  The Web Dashboard is strictly locked until this key or PIN is submitted.\n")
    print("=" * 60)


if __name__ == "__main__":
    main()
