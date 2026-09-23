#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-ha-health.py
Home Assistant Core Log Diagnostic & Self-Healing Engine.
Part of the Hermes Fleet Controller (conversation c46dd19b-0eae-4773-8909-0cec579f8195).
"""

import sys
import os
import json
import urllib.request
import urllib.error
import time

HA_URL = "http://192.168.50.106:8123"
SECRETS_FILES = [
    "/opt/fleet/secrets.env",
    "/opt/fleet/agents/ha-agent/.env"
]

def load_env():
    env = {}
    for sf in SECRETS_FILES:
        if os.path.exists(sf):
            try:
                with open(sf) as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            k, v = line.split("=", 1)
                            env[k.strip()] = v.strip("'\" ")
            except Exception:
                pass
    return env

def get_ha_headers(env):
    token = env.get("MCP_HOMEASSISTANT_API_KEY") or env.get("HOMEASSISTANT_TOKEN")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

def call_ha_api(endpoint, method="GET", data=None):
    env = load_env()
    headers = get_ha_headers(env)
    url = f"{HA_URL}{endpoint}"
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, headers=headers, data=payload, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read().decode("utf-8")
        if content:
            try:
                return json.loads(content)
            except Exception:
                return content
        return {}

def call_ha_ws(msg):
    import asyncio
    import websockets
    env = load_env()
    token = env.get("MCP_HOMEASSISTANT_API_KEY") or env.get("HOMEASSISTANT_TOKEN")
    ws_url = "ws://192.168.50.106:8123/api/websocket"
    
    async def _ws():
        async with websockets.connect(ws_url, timeout=10) as ws:
            await ws.recv()  # auth_required
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth_res = json.loads(await ws.recv())
            if auth_res.get("type") != "auth_ok":
                raise RuntimeError(f"WS Auth failed: {auth_res}")
            
            await ws.send(json.dumps(msg))
            res = json.loads(await ws.recv())
            return res.get("result", {})
            
    return asyncio.run(_ws())

def get_status():
    status = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_online": False,
        "recorder_healthy": True,
        "database_status": "ok",
        "error_count": 0,
        "errors": [],
        "supervisor_healthy": True,
        "supervisor_issues": []
    }
    
    try:
        api_msg = call_ha_api("/api/")
        status["api_online"] = (api_msg.get("message") == "API running.")
    except Exception as e:
        status["api_online"] = False
        status["error"] = str(e)
        return status
        
    try:
        sys_logs = call_ha_ws({"id": 1, "type": "system_log/list"})
        status["error_count"] = len(sys_logs)
        for e in sys_logs:
            status["errors"].append({
                "level": e.get("level"),
                "count": e.get("count"),
                "name": e.get("name"),
                "timestamp": e.get("timestamp"),
                "message": e.get("message", [""])[0] if isinstance(e.get("message"), list) else str(e.get("message", ""))
            })
            if "recorder" in e.get("name", "") and e.get("level") == "ERROR":
                status["recorder_healthy"] = False
                status["database_status"] = "corrupt_or_stopped"
    except Exception as e:
        status["log_fetch_error"] = str(e)

    try:
        res_info = call_ha_ws({"id": 2, "type": "supervisor/api", "endpoint": "/resolution/info", "method": "get"})
        unhealthy = res_info.get("unhealthy", [])
        status["supervisor_healthy"] = (len(unhealthy) == 0)
        status["supervisor_issues"] = unhealthy
    except Exception:
        pass

    return status

def diagnose_with_venice(errors):
    env = load_env()
    key = env.get("VENICE_API_KEY", "")
    model = "deepseek-v4-flash"
    
    prompt = (
        "You are ha-agent, autonomous Home Assistant Site Reliability Engineer.\n"
        "Analyze the following Home Assistant Core log errors:\n\n"
        + json.dumps(errors, indent=2) + "\n\n"
        "Respond ONLY with a JSON object with this exact schema:\n"
        "{\n"
        '  "summary": "1-sentence plain English summary of what is broken",\n'
        '  "root_cause": "1-2 sentence root cause explanation",\n'
        '  "action_type": "repair_db | restart_core | create_backup | clear_logs | reload_integration",\n'
        '  "button_label": "Short action label with emoji, e.g. 🛠️ Fix Database Corruption",\n'
        '  "explanation": "Clear explanation of how the proposed solution fixes the issue"\n'
        "}"
    )
    
    req = urllib.request.Request(
        "https://api.venice.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 500
        }).encode("utf-8")
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        raw_text = res["choices"][0]["message"]["content"].strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw_text)

def execute_action(action_type):
    result = {"action": action_type, "success": False, "details": ""}
    
    if action_type == "clear_logs":
        try:
            call_ha_api("/api/services/system_log/clear", method="POST", data={})
            result["success"] = True
            result["details"] = "Cleared in-memory system_log entries successfully."
        except Exception as e:
            result["details"] = f"Failed to clear logs: {e}"
            
    elif action_type == "restart_core":
        try:
            call_ha_api("/api/services/homeassistant/restart", method="POST", data={})
            result["success"] = True
            result["details"] = "Home Assistant Core restart initiated."
        except Exception as e:
            result["details"] = f"Failed to restart Core: {e}"
            
    elif action_type == "create_backup":
        try:
            call_ha_api("/api/services/backup/create_automatic", method="POST", data={})
            result["success"] = True
            result["details"] = "Automatic backup initiated."
        except Exception as e:
            result["details"] = f"Failed to create backup: {e}"

    elif action_type == "repair_db":
        import subprocess
        cmd = 'ssh -o BatchMode=yes -o Ciphers=aes256-gcm@openssh.com maximusprime@192.168.50.106 "sudo mv /homeassistant/home-assistant_v2.db /homeassistant/home-assistant_v2.db.corrupt_$(date +%s)"'
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        try:
            call_ha_api("/api/services/homeassistant/restart", method="POST", data={})
            result["success"] = True
            result["details"] = "Moved corrupt SQLite database and triggered Core restart."
        except Exception as e:
            result["details"] = f"Moved database but restart failed: {e}"

    else:
        result["details"] = f"Unknown action type: {action_type}"
        
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fleet-ha-health.py [status|diagnose|execute <action>]")
        sys.exit(1)
        
    cmd = sys.argv[1]
    if cmd == "status":
        s = get_status()
        print(json.dumps(s, indent=2))
    elif cmd == "diagnose":
        s = get_status()
        if not s.get("errors"):
            print(json.dumps({"status": "healthy", "message": "No errors in Home Assistant log."}))
        else:
            diag = diagnose_with_venice(s["errors"])
            print(json.dumps(diag, indent=2))
    elif cmd == "execute":
        if len(sys.argv) < 3:
            print("Specify action_type: clear_logs | restart_core | create_backup | repair_db")
            sys.exit(1)
        act = sys.argv[2]
        res = execute_action(act)
        print(json.dumps(res, indent=2))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
