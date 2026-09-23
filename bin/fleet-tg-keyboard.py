#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-tg-keyboard.py
Manages Telegram Bot UI integration for Hermes Fleet Controller:
1. Pushes the persistent Reply Keyboard ('Kitchen Sink') to authorized Telegram chats.
2. Registers native Telegram slash commands menu via setMyCommands.
3. Configures the chat menu button via setChatMenuButton.
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error

SECRETS_FILE = "/opt/fleet/secrets.env"
TUNNEL_FILE = "/opt/fleet/tunnel-url.txt"


def load_secrets():
    secrets = {}
    if os.path.exists(SECRETS_FILE):
        try:
            with open(SECRETS_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        secrets[k.strip()] = v.strip().strip("\"'")
        except Exception as e:
            print(f"[-] Warning: Failed to read {SECRETS_FILE}: {e}", file=sys.stderr)
    return secrets


def get_tunnel_url():
    if os.path.exists(TUNNEL_FILE):
        try:
            with open(TUNNEL_FILE) as f:
                url = f.read().strip()
                if url:
                    return url
        except Exception:
            pass
    return "https://worship-him-knight-jul.trycloudflare.com"


def tg_request(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"[-] Telegram API Error ({method}): HTTP {e.code} - {err_body}", file=sys.stderr)
        return {"ok": False, "error": err_body}
    except Exception as e:
        print(f"[-] Telegram Network Error ({method}): {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}


def setup_commands(token):
    """Register top fleet commands in Telegram's autocomplete menu."""
    commands = [
        {"command": "pair", "description": "🔐 Generate 6-digit PIN & link for Web Dashboard"},
        {"command": "report", "description": "📋 Swarm daily operations & MCP tools digest"},
        {"command": "fleet", "description": "📊 Check real-time health of all fleet agents"},
        {"command": "privacy", "description": "🔒 List Venice hardware-isolated E2EE models"},
        {"command": "backup", "description": "🛡️ Trigger hot SQLite database snapshot"},
        {"command": "status", "description": "🛰️ Fleet controller system & agent status"},
        {"command": "pull", "description": "🔄 Pull latest code updates from GitHub repo"},
        {"command": "login", "description": "🔑 Quick dashboard login link & pairing PIN"}
    ]
    res = tg_request(token, "setMyCommands", {"commands": commands})
    if res.get("ok"):
        print("[✓] Telegram setMyCommands registered successfully.")
    else:
        print("[-] Failed to register setMyCommands.", file=sys.stderr)
    return res.get("ok", False)


def setup_menu_button(token):
    """Ensure the chat menu button opens the command palette."""
    res = tg_request(token, "setChatMenuButton", {"menu_button": {"type": "commands"}})
    if res.get("ok"):
        print("[✓] Telegram setChatMenuButton configured to commands menu.")
    else:
        print("[-] Failed to configure setChatMenuButton.", file=sys.stderr)
    return res.get("ok", False)


def send_kitchen_sink_keyboard(token, chat_id):
    """Send persistent reply keyboard with the top fleet actions."""
    tunnel_url = get_tunnel_url()
    keyboard = {
        "keyboard": [
            [
                {"text": "🔐 Pair Dashboard"},
                {"text": "📋 Daily Report"}
            ],
            [
                {"text": "📊 Fleet Status"},
                {"text": "🔒 Privacy Models"}
            ],
            [
                {"text": "🔌 MCP Tools"},
                {"text": "🛡️ Hot Backup"}
            ]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }

    text = (
        "🎛️ *Hermes Fleet Controller — Operations Console*\n\n"
        f"🌐 *Web Dashboard Link*:\n{tunnel_url}\n\n"
        "Tap any button below to activate instant fleet operations, "
        "or type `/` to browse slash commands (e.g. `/pair`, `/report`, `/fleet`, `/privacy`):"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": keyboard
    }

    res = tg_request(token, "sendMessage", payload)
    if res.get("ok"):
        print(f"[✓] Persistent reply keyboard sent to chat {chat_id}.")
    else:
        print(f"[-] Failed to send keyboard to chat {chat_id}.", file=sys.stderr)
    return res.get("ok", False)


def remove_keyboard(token, chat_id):
    """Remove custom reply keyboard."""
    payload = {
        "chat_id": chat_id,
        "text": "Reply keyboard removed.",
        "reply_markup": {"remove_keyboard": True}
    }
    return tg_request(token, "sendMessage", payload).get("ok", False)


def main():
    parser = argparse.ArgumentParser(description="Hermes Fleet Telegram Keyboard & Command Setup")
    parser.add_argument("--setup", action="store_true", help="Run full setup (setMyCommands, menu button, and send persistent keyboard)")
    parser.add_argument("--send", action="store_true", help="Send persistent reply keyboard to authorized user(s)")
    parser.add_argument("--remove", action="store_true", help="Remove reply keyboard from chat")
    parser.add_argument("--chat", help="Specific Telegram chat ID (defaults to TELEGRAM_ALLOWED_USERS)")
    parser.add_argument("--token", help="Telegram bot token override")

    args = parser.parse_args()

    secrets = load_secrets()
    token = args.token or os.environ.get("TELEGRAM_BOT_TOKEN") or secrets.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[-] Error: TELEGRAM_BOT_TOKEN not found in secrets.env or environment.", file=sys.stderr)
        sys.exit(1)

    target_chats = []
    if args.chat:
        target_chats = [args.chat]
    else:
        allowed = os.environ.get("TELEGRAM_ALLOWED_USERS") or secrets.get("TELEGRAM_ALLOWED_USERS", "")
        target_chats = [c.strip() for c in allowed.split(",") if c.strip()]

    if not target_chats:
        print("[-] Warning: No target chat IDs found. Defaulting to 8293122782.")
        target_chats = ["8293122782"]

    if args.setup:
        print("[*] Setting up Telegram bot menu and persistent keyboard...")
        c_ok = setup_commands(token)
        m_ok = setup_menu_button(token)
        k_ok = True
        for chat_id in target_chats:
            k_ok = send_kitchen_sink_keyboard(token, chat_id) and k_ok
        if c_ok and m_ok and k_ok:
            print("[✓] All Telegram UI components successfully initialized.")
            sys.exit(0)
        else:
            print("[!] Setup completed with warnings/errors.", file=sys.stderr)
            sys.exit(1)

    if args.send:
        for chat_id in target_chats:
            send_kitchen_sink_keyboard(token, chat_id)
        sys.exit(0)

    if args.remove:
        for chat_id in target_chats:
            remove_keyboard(token, chat_id)
        sys.exit(0)

    # Default action if no flag: run setup
    c_ok = setup_commands(token)
    m_ok = setup_menu_button(token)
    for chat_id in target_chats:
        send_kitchen_sink_keyboard(token, chat_id)


if __name__ == "__main__":
    main()
