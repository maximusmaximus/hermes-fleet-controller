#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-pair.py
Zero-Trust device pairing engine for the Hermes Fleet Controller Web Dashboard.
Manages time-limited 6-digit PINs, IP rate-limiting, and signed HMAC-SHA256 session tokens.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import secrets

AUTH_FILE = "/opt/fleet/web-auth.json"
TUNNEL_FILE = "/opt/fleet/tunnel-url.txt"
PIN_EXPIRY_SECONDS = 600       # 10 minutes
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
        "pin_expires_at": 0,
        "failed_attempts": {},  # ip: {"count": int, "locked_until": int}
        "valid_sessions": {}   # token: {"created_at": int, "device": str}
    }
    save_auth_db(db)
    return db


def save_auth_db(db):
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    temp_file = AUTH_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(db, f, indent=2)
    os.chmod(temp_file, 0o600)
    os.replace(temp_file, AUTH_FILE)


def generate_pin():
    db = load_auth_db()
    pin = f"{secrets.randbelow(900000) + 100000}"
    expires_at = int(time.time()) + PIN_EXPIRY_SECONDS
    db["active_pin"] = pin
    db["pin_expires_at"] = expires_at
    save_auth_db(db)
    return pin, expires_at


def verify_pin(pin_attempt, client_ip):
    db = load_auth_db()
    now = int(time.time())

    # Check IP lockout
    ip_record = db["failed_attempts"].get(client_ip, {"count": 0, "locked_until": 0})
    if ip_record["locked_until"] > now:
        remaining = ip_record["locked_until"] - now
        return False, None, f"IP is locked due to too many failed attempts. Try again in {remaining}s."

    # Check PIN validity
    active_pin = db.get("active_pin")
    pin_expiry = db.get("pin_expires_at", 0)

    if not active_pin or now > pin_expiry:
        return False, None, "Pairing PIN has expired or has not been generated."

    if not hmac.compare_digest(str(pin_attempt).strip(), str(active_pin).strip()):
        # Record failure
        ip_record["count"] += 1
        if ip_record["count"] >= LOCKOUT_THRESHOLD:
            ip_record["locked_until"] = now + LOCKOUT_DURATION_SECONDS
            ip_record["count"] = 0
            db["failed_attempts"][client_ip] = ip_record
            save_auth_db(db)
            return False, None, f"Too many failed attempts. Locked out for {LOCKOUT_DURATION_SECONDS // 60} minutes."
        db["failed_attempts"][client_ip] = ip_record
        save_auth_db(db)
        remaining_attempts = LOCKOUT_THRESHOLD - ip_record["count"]
        return False, None, f"Invalid PIN. {remaining_attempts} attempt(s) remaining."

    # Successful verification! Reset failed attempts
    db["failed_attempts"].pop(client_ip, None)
    # Generate cryptographically signed session token
    raw_token = secrets.token_hex(24)
    signature = hmac.new(
        db["secret_key"].encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    session_token = f"{raw_token}.{signature}"

    db["valid_sessions"][session_token] = {
        "created_at": now,
        "client_ip": client_ip
    }
    save_auth_db(db)
    return True, session_token, "Success"


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
    if os.path.exists(TUNNEL_FILE):
        try:
            with open(TUNNEL_FILE) as f:
                return f.read().strip()
        except Exception:
            pass
    return "http://localhost:8650"


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--verify", "-v"):
        if len(sys.argv) < 4:
            print("Usage: fleet-pair.py --verify <pin> <client_ip>")
            sys.exit(1)
        ok, token, msg = verify_pin(sys.argv[2], sys.argv[3])
        print(json.dumps({"success": ok, "token": token, "message": msg}))
        sys.exit(0 if ok else 1)

    if len(sys.argv) > 1 and sys.argv[1] in ("--check-token", "-c"):
        if len(sys.argv) < 3:
            print("Usage: fleet-pair.py --check-token <token>")
            sys.exit(1)
        valid = verify_token(sys.argv[2])
        print("valid" if valid else "invalid")
        sys.exit(0 if valid else 1)

    # Generate new PIN
    pin, expires_at = generate_pin()
    now = int(time.time())
    ttl = expires_at - now
    tunnel_url = get_tunnel_url()

    if len(sys.argv) > 1 and sys.argv[1] in ("--json", "-j"):
        print(json.dumps({
            "pin": pin,
            "expires_at": expires_at,
            "ttl_seconds": ttl,
            "url": tunnel_url
        }, indent=2))
        sys.exit(0)

    tg_text = (
        f"🔐 *HERMES FLEET DASHBOARD LOGIN*\n\n"
        f"🌐 *Direct Access Link*:\n{tunnel_url}\n\n"
        f"🔑 *One-Time Login PIN*: `{pin}`\n"
        f"⏳ *Valid For*: {ttl // 60} minutes ({ttl}s)\n"
        f"🛡️ *Security*: Rate-limited (5 failed attempts = 15m lockout)\n\n"
        f"👉 Tap the link above on your phone or laptop and enter PIN `{pin}` to authenticate."
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
    print("       🔐 HERMES FLEET CONTROLLER - DEVICE PAIRING")
    print("=" * 60)
    print(f"\n  Active 6-Digit Pairing PIN:   \033[1;32m{pin}\033[0m")
    print(f"  Valid for:                    {ttl // 60} minutes ({ttl} seconds)")
    print(f"  Rate-limit protection:        5 failed attempts = 15m lockout")
    print(f"  Access URL:                   {tunnel_url}")
    print("\n  Open the Access URL on your phone or remote laptop,")
    print(f"  and enter PIN '{pin}' to establish an encrypted session.\n")
    print("=" * 60)


if __name__ == "__main__":
    main()
