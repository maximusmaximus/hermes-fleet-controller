#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-ha-log-watcher.py
Periodic Log Watcher & Automated Telegram Diagnostic Dispatcher.
Part of the Hermes Fleet Controller (conversation c46dd19b-0eae-4773-8909-0cec579f8195).
"""

import sys
import os
import json
import time
import subprocess

SEEN_LOGS_FILE = "/opt/fleet/agents/ha-agent/logs/seen_errors.json"
PENDING_FIX_FILE = "/opt/fleet/agents/ha-agent/logs/pending_fix.json"
HEALTH_SCRIPT = "/opt/fleet/bin/fleet-ha-health.py"
MENU_SCRIPT = "/opt/fleet/bin/telegram-menu.py"
SECRETS_FILE = "/opt/fleet/agents/ha-agent/.env"

def load_seen():
    if os.path.exists(SEEN_LOGS_FILE):
        try:
            with open(SEEN_LOGS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN_LOGS_FILE), exist_ok=True)
    with open(SEEN_LOGS_FILE, "w") as f:
        json.dump(seen, f, indent=2)

def check_and_alert():
    # Run status check
    r = subprocess.run([sys.executable, HEALTH_SCRIPT, "status"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Error checking status: {r.stderr}")
        return
        
    status = json.loads(r.stdout)
    errors = status.get("errors", [])
    if not errors:
        print("System log is completely clean. No action needed.")
        return
        
    seen = load_seen()
    new_errors = []
    
    for err in errors:
        err_sig = f"{err.get('name')}:{err.get('message', '')[:60]}"
        if err_sig not in seen:
            new_errors.append(err)
            seen.append(err_sig)
            
    if not new_errors:
        print("All currently active errors have already been alerted.")
        return

    # Call AI diagnostician (using ha-agent's Venice inference)
    diag_res = subprocess.run([sys.executable, HEALTH_SCRIPT, "diagnose"], capture_output=True, text=True)
    if diag_res.returncode != 0:
        print(f"Error running diagnosis: {diag_res.stderr}")
        return
        
    diag = json.loads(diag_res.stdout)
    summary = diag.get("summary", "Unspecified error")
    root_cause = diag.get("root_cause", "")
    action_type = diag.get("action_type", "restart_core")
    button_label = diag.get("button_label", "🛠️ Apply Fix")
    explanation = diag.get("explanation", "")

    # Save pending fix
    pending = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "action_type": action_type,
        "button_label": button_label,
        "errors": new_errors
    }
    with open(PENDING_FIX_FILE, "w") as f:
        json.dump(pending, f, indent=2)

    # Format Telegram Card
    title = "⚠️ *Home Assistant Core Issue Detected*"
    subtitle = (
        f"• *Issue:* {summary}\n"
        f"• *Root Cause:* {root_cause}\n"
        f"• *Proposed Solution:* {explanation}\n\n"
        f"Tap below to authorize the automated fix using ha-agent inference:"
    )

    # Dispatch to Telegram using telegram-menu.py
    env = {}
    with open(SECRETS_FILE) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip("'\" ")
                
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0]
    
    cmd = [
        sys.executable, MENU_SCRIPT,
        "--token", token,
        "--chat-id", chat_id,
        "--title", title,
        "--subtitle", subtitle,
        "--button", f"{button_label}:fix_{action_type}",
        "--button", "📊 View Status:status",
        "--button", "🧹 Clear Alert:clear_logs"
    ]
    subprocess.run(cmd)
    save_seen(seen)
    print("Interactive diagnostic card dispatched to Telegram successfully.")

if __name__ == "__main__":
    check_and_alert()
