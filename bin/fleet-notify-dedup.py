#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-notify-dedup.py
Intelligent Notification Deduplicator, Throttler & Delivery Engine for Hermes Fleet Controller.

Features:
- Fingerprints messages by stripping dynamic transient jitter (timestamps, short CIDs, pings, IPs).
- Enforces configurable cooldowns on identical/same status responses (default: 6 hours).
- Enforces rate-limiting gap between automatic background notifications (default: 15s).
- Tracks suppressed message counts and reports deduplication statistics.
- Supports --force to allow immediate delivery for interactive user requests (e.g. /report, /pair).
"""

import os
import sys
import re
import json
import time
import hashlib
import argparse
import urllib.request
import urllib.parse

STATE_DIR = "/opt/fleet/run"
STATE_FILE = os.path.join(STATE_DIR, "telegram_notify_state.json")
SECRETS_FILE = os.environ.get("FLEET_SECRETS_FILE", "/opt/fleet/secrets.env")
LOG_FILE = "/opt/fleet/logs/telegram-notify.log"

DEFAULT_COOLDOWN_SECONDS = 21600   # 6 hours for identical status messages
DEFAULT_RATE_LIMIT_SECONDS = 15     # 15s gap between automatic notifications


def ensure_dirs():
    global STATE_DIR, STATE_FILE, LOG_FILE
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    except PermissionError:
        STATE_DIR = "/tmp/fleet-notify"
        STATE_FILE = os.path.join(STATE_DIR, "telegram_notify_state.json")
        LOG_FILE = os.path.join(STATE_DIR, "telegram-notify.log")
        os.makedirs(STATE_DIR, exist_ok=True)


def log_event(message: str):
    ensure_dirs()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def load_credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    allowed = os.environ.get("TELEGRAM_ALLOWED_USERS")

    if not token or not allowed:
        if os.path.exists(SECRETS_FILE):
            try:
                with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TELEGRAM_BOT_TOKEN="):
                            token = line.split("=", 1)[1].strip("\"' ")
                        elif line.startswith("TELEGRAM_ALLOWED_USERS="):
                            allowed = line.split("=", 1)[1].strip("\"' ")
            except Exception as e:
                log_event(f"Error reading secrets file: {e}")

    chat_ids = [uid.strip() for uid in (allowed or "").split(",") if uid.strip()]
    return token, chat_ids


def normalize_content(text: str) -> str:
    """
    Strips ephemeral timestamps, latencies, container IDs, and IP addresses
    so identical status responses produce the exact same fingerprint.
    """
    norm = text

    # 1. Normalize ISO timestamps and date-times: 2026-09-27T16:15:32Z, 2026-09-27 09:15:32
    norm = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b", "<TIMESTAMP>", norm)
    # Formatted human dates: Sun Sep 27 09:15:00 2026, 2026-09-27
    norm = re.sub(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d+.*?\d{4}\b", "<DATE>", norm, flags=re.I)
    norm = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<DATE>", norm)

    # 2. Normalize 12-char hex container IDs: (f78a555f5a4b)
    norm = re.sub(r"\b[0-9a-f]{12}\b", "<CID>", norm)

    # 3. Normalize IP addresses: 10.88.41.96, 192.168.1.1
    norm = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>", norm)

    # 4. Normalize ephemeral network pings & latencies: ping: 12.8ms, 0.5ms
    norm = re.sub(r"\b\d+(?:\.\d+)?\s*ms\b", "<LATENCY>", norm, flags=re.I)

    # 5. Normalize slight RAM jitter: 4.985MB, 12.3 MB
    norm = re.sub(r"\b\d+(?:\.\d+)?\s*(?:MB|GB|KB)\b", "<MEM>", norm, flags=re.I)

    # 6. Normalize load averages: load: 0.15, 0.22, 0.18
    norm = re.sub(r"load:\s*\d+\.\d+,\s*\d+\.\d+,\s*\d+\.\d+", "load: <LOAD>", norm, flags=re.I)

    # 7. Normalize tunnel URL tokens: ?key=xyz
    norm = re.sub(r"\?key=[A-Za-z0-9_-]+", "?key=<KEY>", norm)

    # 8. Collapse redundant whitespace
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def compute_fingerprint(text: str) -> str:
    normalized = normalize_content(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_state() -> dict:
    ensure_dirs()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_sent_global": 0,
        "total_sent": 0,
        "total_suppressed": 0,
        "messages": {}
    }


def save_state(state: dict):
    ensure_dirs()
    # Prune old fingerprints older than 48 hours to keep state lean
    now = time.time()
    pruned_msgs = {}
    for h, d in state.get("messages", {}).items():
        if now - d.get("last_sent", 0) < 172800: # 48 hours
            pruned_msgs[h] = d
    state["messages"] = pruned_msgs

    tmp_file = f"{STATE_FILE}.tmp.{os.getpid()}"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_file, STATE_FILE)
    except Exception as e:
        log_event(f"Error writing state file: {e}")


def send_to_telegram(token: str, chat_ids: list, message: str) -> bool:
    if not token or not chat_ids:
        log_event("Cannot send: Missing token or chat IDs")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    success = True
    for cid in chat_ids:
        try:
            payload = {
                "chat_id": cid,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    # Fallback to plain text if Markdown fails parsing
                    payload.pop("parse_mode", None)
                    req2 = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"}
                    )
                    urllib.request.urlopen(req2, timeout=10)
        except Exception as e:
            # Fallback retry without Markdown
            try:
                plain_payload = {
                    "chat_id": cid,
                    "text": message,
                    "disable_web_page_preview": True
                }
                req3 = urllib.request.Request(
                    url,
                    data=json.dumps(plain_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req3, timeout=10)
            except Exception as e2:
                log_event(f"Failed delivering to chat_id {cid}: {e2}")
                success = False

    return success


def process_notification(message: str, force: bool = False, cooldown: int = DEFAULT_COOLDOWN_SECONDS, rate_limit: int = DEFAULT_RATE_LIMIT_SECONDS, tag: str = None) -> bool:
    message = message.strip()
    if not message:
        return True

    token, chat_ids = load_credentials()
    if not token or not chat_ids:
        print("[-] Error: Missing Telegram credentials.", file=sys.stderr)
        return False

    fp = compute_fingerprint(message)
    state = load_state()
    now = time.time()

    msg_info = state["messages"].get(fp, {
        "first_sent": 0,
        "last_sent": 0,
        "suppressed_count": 0,
        "sample": message[:120].replace("\n", " "),
        "tag": tag or "general"
    })

    time_since_same = now - msg_info.get("last_sent", 0)
    time_since_global = now - state.get("last_sent_global", 0)

    # 1. Deduplication check: Has the same status response been sent recently?
    if not force and time_since_same < cooldown and msg_info["last_sent"] > 0:
        msg_info["suppressed_count"] = msg_info.get("suppressed_count", 0) + 1
        state["total_suppressed"] = state.get("total_suppressed", 0) + 1
        state["messages"][fp] = msg_info
        save_state(state)
        
        hours_left = round((cooldown - time_since_same) / 3600, 1)
        log_msg = f"[SUPPRESSED] Duplicate '{tag or fp[:8]}' (seen {msg_info['suppressed_count']}x, cooldown remaining: {hours_left}h)"
        log_event(log_msg)
        print(log_msg)
        return True # Handled safely without sending

    # 2. Rate limit check: Prevent automated message bursts
    if not force and time_since_global < rate_limit and state["last_sent_global"] > 0:
        msg_info["suppressed_count"] = msg_info.get("suppressed_count", 0) + 1
        state["total_suppressed"] = state.get("total_suppressed", 0) + 1
        state["messages"][fp] = msg_info
        save_state(state)

        log_msg = f"[RATE_LIMITED] Rapid burst suppressed ({time_since_global:.1f}s < {rate_limit}s threshold)"
        log_event(log_msg)
        print(log_msg)
        return True

    # 3. Deliver message
    suppressed_prior = msg_info.get("suppressed_count", 0)
    send_text = message
    if suppressed_prior > 1:
        send_text += f"\n\n_(ℹ️ {suppressed_prior} duplicate status updates suppressed)_"

    ok = send_to_telegram(token, chat_ids, send_text)
    if ok:
        state["last_sent_global"] = now
        state["total_sent"] = state.get("total_sent", 0) + 1
        msg_info["last_sent"] = now
        if msg_info["first_sent"] == 0:
            msg_info["first_sent"] = now
        msg_info["suppressed_count"] = 0
        state["messages"][fp] = msg_info
        save_state(state)
        log_event(f"[DELIVERED] Tag: '{tag or 'general'}', FP: {fp[:8]}, Len: {len(message)}")
        print(f"[✓] Notification delivered (FP: {fp[:8]}).")
        return True
    else:
        log_event(f"[ERROR] Delivery failed for FP: {fp[:8]}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Hermes Fleet Telegram Notification Deduplicator")
    parser.add_argument("message", nargs="?", default="", help="Notification message text (or read from stdin)")
    parser.add_argument("--force", "-f", action="store_true", help="Bypass deduplication and rate limits")
    parser.add_argument("--cooldown", "-c", type=int, default=DEFAULT_COOLDOWN_SECONDS, help="Cooldown in seconds for duplicate messages (default: 21600 / 6h)")
    parser.add_argument("--rate-limit", "-r", type=int, default=DEFAULT_RATE_LIMIT_SECONDS, help="Rate limit in seconds between consecutive messages (default: 15)")
    parser.add_argument("--tag", "-t", type=str, default="", help="Category tag for logging and grouping")
    parser.add_argument("--status", action="store_true", help="Print deduplication statistics and exit")
    parser.add_argument("--reset", action="store_true", help="Clear deduplication state cache and exit")

    args = parser.parse_args()

    if args.status:
        state = load_state()
        print(f"Total Sent      : {state.get('total_sent', 0)}")
        print(f"Total Suppressed: {state.get('total_suppressed', 0)}")
        print(f"Tracked Hashes  : {len(state.get('messages', {}))}")
        for fp, d in list(state.get("messages", {}).items())[:10]:
            print(f"  • {fp[:8]} [{d.get('tag', 'general')}]: last_sent={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d.get('last_sent', 0)))}, suppressed={d.get('suppressed_count', 0)}x | {d.get('sample', '')[:60]}")
        sys.exit(0)

    if args.reset:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("[✓] Deduplication state cache cleared.")
        sys.exit(0)

    msg = args.message
    if not msg:
        if not sys.stdin.isatty():
            msg = sys.stdin.read()
    
    if not msg.strip():
        print("[-] Error: No message provided.", file=sys.stderr)
        sys.exit(1)

    success = process_notification(
        message=msg,
        force=args.force,
        cooldown=args.cooldown,
        rate_limit=args.rate_limit,
        tag=args.tag
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
