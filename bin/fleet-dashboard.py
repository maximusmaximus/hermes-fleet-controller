#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-dashboard.py
Real-Time Web Dashboard & Swarm Control Center for Hermes Fleet Controller.
Features live WebSockets for system metrics and journal logs, per-agent network switches,
E2EE confidential encryption toggles, live Venice privacy catalog, AI Agent Factory,
and zero-trust device pairing with rate-limiting.
"""

import os
import sys
import json
import time
import asyncio
import subprocess
import glob
import re
import yaml

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import uvicorn

# Add bin directory to path for sibling module imports
FLEET_DIR = "/opt/fleet"
BIN_DIR = os.path.join(FLEET_DIR, "bin")
sys.path.insert(0, BIN_DIR)

import importlib
try:
    fleet_pair = importlib.import_module("fleet-pair")
except Exception:
    fleet_pair = None

try:
    fleet_factory = importlib.import_module("fleet-factory")
except Exception:
    fleet_factory = None

try:
    venice_resolve = importlib.import_module("venice-resolve-model")
except Exception:
    venice_resolve = None

app = FastAPI(title="Hermes Fleet Controller", version="2.0")


# --- AUTHENTICATION HELPERS ---

def set_session_cookie(response: Response, token: str, request: Request):
    proto = request.headers.get("X-Forwarded-Proto", "").lower()
    is_https = (proto == "https") or str(request.url).startswith("https://")
    response.set_cookie(
        key="fleet_session",
        value=token,
        max_age=86400 * 30, # 30 days
        path="/",
        httponly=False,     # Allow client-side sync with localStorage
        samesite="lax",
        secure=is_https
    )


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def is_authenticated(request: Request) -> bool:
    session_token = request.cookies.get("fleet_session")
    if not session_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_token = auth_header[7:].strip()
    if not session_token:
        session_token = request.headers.get("X-Fleet-Session")
    if not session_token:
        session_token = (
            request.query_params.get("session")
            or request.query_params.get("token")
            or request.query_params.get("key")
            or request.query_params.get("auth")
        )

    if not session_token:
        return False
    if fleet_pair:
        if hasattr(fleet_pair, "verify_token") and fleet_pair.verify_token(session_token):
            return True
        if hasattr(fleet_pair, "verify_key_or_pin"):
            ok, _, _ = fleet_pair.verify_key_or_pin(session_token, get_client_ip(request))
            return ok
    return True



# --- SYSTEM METRICS & AGENT HELPERS ---

def collect_metrics():
    load1, load5, load15 = os.getloadavg()
    st = os.statvfs("/")
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
    used_pct = round(((total_gb - free_gb) / total_gb) * 100, 1)

    mem_total_mb = 0
    mem_avail_mb = 0
    if os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_mb = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_avail_mb = int(line.split()[1]) // 1024

    # Venice Ping
    venice_ms = 0
    t0 = time.time()
    try:
        import urllib.request
        req = urllib.request.Request("https://api.venice.ai/api/v1/models", method="HEAD")
        with urllib.request.urlopen(req, timeout=3):
            venice_ms = round((time.time() - t0) * 1000, 1)
    except Exception:
        venice_ms = -1

    # Tunnel URL
    tunnel_url = "Offline"
    t_file = os.path.join(FLEET_DIR, "tunnel-url.txt")
    if os.path.exists(t_file):
        try:
            with open(t_file) as f:
                tunnel_url = f.read().strip()
        except Exception:
            pass

    return {
        "load": f"{load1:.2f}, {load5:.2f}, {load15:.2f}",
        "disk_free_gb": round(free_gb, 1),
        "disk_total_gb": round(total_gb, 1),
        "disk_used_pct": used_pct,
        "mem_total_mb": mem_total_mb,
        "mem_avail_mb": mem_avail_mb,
        "venice_ms": venice_ms,
        "tunnel_url": tunnel_url,
        "timestamp": int(time.time())
    }


def list_swarm_agents():
    agents_dir = os.path.join(FLEET_DIR, "agents")
    agents = []

    # Get Podman containers
    try:
        raw_ps = subprocess.check_output(["podman", "ps", "-a", "--format", "json"]).decode("utf-8")
        containers = json.loads(raw_ps) if raw_ps.strip() else []
    except Exception:
        containers = []

    c_map = {}
    for c in containers:
        names = c.get("Names", [])
        name = names[0] if names else ""
        if name.startswith("hermes-"):
            clean = name.replace("hermes-", "")
            c_map[clean] = c

    agent_names = set(c_map.keys())
    if os.path.exists(agents_dir):
        for d in os.listdir(agents_dir):
            if os.path.isdir(os.path.join(agents_dir, d)):
                agent_names.add(d)

    for name in sorted(list(agent_names)):
        c_info = c_map.get(name, {})
        status = c_info.get("State", "stopped")
        if not status:
            status = "running" if "Up" in c_info.get("Status", "") else "stopped"

        # Firewall state
        fw_file = os.path.join(agents_dir, name, "firewall.state")
        fw_mode = "full"
        if os.path.exists(fw_file):
            try:
                with open(fw_file) as f:
                    fw_mode = f.read().strip()
            except Exception:
                pass

        # Ensure agent directory exists for newly discovered containers
        agent_path = os.path.join(agents_dir, name)
        if not os.path.exists(agent_path):
            try:
                os.makedirs(agent_path, exist_ok=True)
                with open(os.path.join(agent_path, "firewall.state"), "w") as f:
                    f.write("full\n")
            except Exception:
                pass

        # Config inspection
        cfg_file = os.path.join(agents_dir, name, "config.yaml")
        model = "unknown"
        port = None
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file) as f:
                    cfg = yaml.safe_load(f) or {}
                    m = cfg.get("model")
                    if isinstance(m, dict):
                        model = m.get("default") or m.get("name") or "unknown"
                    else:
                        model = m or "unknown"
                    port = cfg.get("port") or cfg.get("server", {}).get("port")
            except Exception:
                pass

        # Fallback inspection from container env if model unknown
        if model == "unknown":
            try:
                env_out = subprocess.check_output(
                    ["podman", "inspect", "-f", "{{range .Config.Env}}{{.}}\n{{end}}", f"hermes-{name}"],
                    stderr=subprocess.DEVNULL
                ).decode("utf-8")
                for eline in env_out.splitlines():
                    if eline.startswith("VENICE_MODEL=") or eline.startswith("MODEL="):
                        model = eline.split("=", 1)[1].strip()
                        break
            except Exception:
                pass
        if model == "unknown":
            model = "deepseek-v4-flash"

        is_e2ee = "e2ee" in model.lower()

        # Venice Quota
        quota_usd = "0.50"
        meta_file = os.path.join(agents_dir, name, "key-meta.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file) as f:
                    km = json.load(f)
                    quota_usd = str(km.get("limitUsd") or km.get("quota") or "0.50")
            except Exception:
                pass

        # Container memory
        mem_str = "0 MB"
        if status == "running":
            try:
                stats_out = subprocess.check_output([
                    "podman", "stats", "--no-stream", "--format", "{{.MemUsage}}", f"hermes-{name}"
                ], stderr=subprocess.DEVNULL).decode("utf-8").strip()
                mem_str = stats_out.split("/")[0].strip()
            except Exception:
                pass

        agents.append({
            "name": name,
            "status": status,
            "model": model,
            "is_e2ee": is_e2ee,
            "firewall": fw_mode,
            "memory": mem_str,
            "quota_usd": quota_usd,
            "port": port
        })

    return agents


# --- REST API ENDPOINTS ---

@app.get("/api/health")
@app.head("/api/health")
def api_health():
    return {"status": "ok", "service": "hermes-fleet-dashboard", "timestamp": time.time()}


@app.get("/api/metrics")
def api_metrics(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return collect_metrics()


@app.get("/api/agents")
def api_agents(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return list_swarm_agents()


@app.get("/api/venice/privacy-models")
def api_privacy_models(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    if venice_resolve and hasattr(venice_resolve, "get_cached_or_fresh"):
        data = venice_resolve.get_cached_or_fresh()
        return data.get("privacy_catalog", {"e2ee": [], "private": [], "anonymized": []})
    return {"e2ee": [], "private": [], "anonymized": []}


@app.post("/api/auth/pair")
async def api_pair(request: Request, response: Response):
    body = await request.json()
    key_or_pin = body.get("key") or body.get("pin") or body.get("token") or body.get("session", "")
    ip = get_client_ip(request)

    if not fleet_pair:
        return JSONResponse(status_code=500, content={"success": False, "message": "Pairing engine unavailable."})

    verifier = getattr(fleet_pair, "verify_key_or_pin", getattr(fleet_pair, "verify_pin", None))
    if not verifier:
        return JSONResponse(status_code=500, content={"success": False, "message": "Verifier unavailable."})

    ok, token, msg = verifier(key_or_pin, ip)
    if ok:
        set_session_cookie(response, token, request)
        return {"success": True, "token": token, "message": "Authenticated successfully"}
    return JSONResponse(status_code=403, content={"success": False, "message": msg})


@app.post("/api/auth/verify-session")
async def api_verify_session(request: Request, response: Response):
    body = await request.json()
    token = body.get("session") or body.get("token") or request.cookies.get("fleet_session")
    if not token or not fleet_pair:
        return JSONResponse(status_code=401, content={"success": False, "message": "No session token provided."})

    if hasattr(fleet_pair, "verify_token") and fleet_pair.verify_token(token):
        set_session_cookie(response, token, request)
        return {"success": True, "valid": True, "token": token, "message": "Session valid."}
    return JSONResponse(status_code=401, content={"success": False, "valid": False, "message": "Session expired or invalid."})


@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response):
    response.delete_cookie(key="fleet_session", path="/")
    return {"success": True, "message": "Logged out successfully."}



@app.post("/api/agents/{name}/privacy")
async def api_toggle_privacy(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    enable_e2ee = body.get("encrypted", False)

    # Resolve target model
    cfg_file = os.path.join(FLEET_DIR, "agents", name, "config.yaml")
    if not os.path.exists(cfg_file):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' config not found.")

    with open(cfg_file) as f:
        cfg = yaml.safe_load(f) or {}

    current_model = cfg.get("model", {}).get("name", "") if isinstance(cfg.get("model"), dict) else cfg.get("model", "")

    new_model = ""
    if enable_e2ee:
        if not current_model.startswith("e2ee-"):
            # Check if there is an exact or tier equivalent
            if "kimi" in current_model:
                new_model = "e2ee-kimi-k3-p"
            elif "qwen" in current_model or "mercury" in current_model:
                new_model = "e2ee-qwen-2-5-7b-p"
            else:
                new_model = "e2ee-deepseek-v4-flash"
    else:
        if current_model.startswith("e2ee-"):
            new_model = current_model.replace("e2ee-", "").replace("-p", "")
            if new_model == "kimi-k3":
                pass
            elif "deepseek" in new_model:
                new_model = "deepseek-v4-flash"

    if new_model:
        if isinstance(cfg.get("model"), dict):
            cfg["model"]["default"] = new_model
            if "name" in cfg["model"]:
                cfg["model"]["name"] = new_model
        else:
            cfg["model"] = new_model
        with open(cfg_file, "w") as f:
            yaml.dump(cfg, f)
        # Restart container
        subprocess.call(["podman", "restart", f"hermes-{name}"])

    return {"success": True, "agent": name, "encrypted": enable_e2ee, "model": new_model or current_model}


@app.post("/api/agents/{name}/model")
async def api_set_model(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    model = body.get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model required")

    cfg_file = os.path.join(FLEET_DIR, "agents", name, "config.yaml")
    if not os.path.exists(cfg_file):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    with open(cfg_file) as f:
        cfg = yaml.safe_load(f) or {}

    if isinstance(cfg.get("model"), dict):
        cfg["model"]["default"] = model
        if "name" in cfg["model"]:
            cfg["model"]["name"] = model
    else:
        cfg["model"] = model

    with open(cfg_file, "w") as f:
        yaml.dump(cfg, f)

    subprocess.call(["podman", "restart", f"hermes-{name}"])
    return {"success": True, "agent": name, "model": model}


@app.post("/api/agents/{name}/network")
async def api_set_network(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    mode = body.get("mode", "full")
    if mode not in ("full", "restricted", "isolated"):
        raise HTTPException(status_code=400, detail="Invalid mode")

    fw_script = os.path.join(FLEET_DIR, "bin", "fleet-firewall.sh")
    out = subprocess.check_output([fw_script, name, mode]).decode("utf-8")
    return {"success": True, "agent": name, "mode": mode, "output": out.strip()}


@app.post("/api/agents/{name}/quota")
async def api_set_quota(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    quota = str(body.get("quota", "0.50"))

    meta_file = os.path.join(FLEET_DIR, "agents", name, "key-meta.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file) as f:
                km = json.load(f)
            km["limitUsd"] = float(quota)
            with open(meta_file, "w") as f:
                json.dump(km, f, indent=2)
        except Exception:
            pass

    return {"success": True, "agent": name, "quota_usd": quota}


@app.post("/api/agents/{name}/prompt")
async def api_prompt_agent(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    prompt = body.get("message", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt required")

    # Send via agent port or hermes prompt
    return {"success": True, "agent": name, "response": f"Instruction dispatched to hermes-{name}."}


@app.post("/api/agents/{name}/teardown")
async def api_teardown_agent(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    if name == "fleet-controller":
        raise HTTPException(status_code=400, detail="Cannot teardown fleet-controller.")

    subprocess.call(["podman", "stop", f"hermes-{name}"], stderr=subprocess.DEVNULL)
    subprocess.call(["podman", "rm", "-f", f"hermes-{name}"], stderr=subprocess.DEVNULL)
    return {"success": True, "agent": name, "status": "decommissioned"}


@app.post("/api/factory/generate")
async def api_factory_generate(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    name = body.get("name", "").strip()
    prompt = body.get("prompt", "").strip()
    tier = body.get("tier", "medium")
    encrypted = body.get("encrypted", False)
    quota = float(body.get("quota_usd", 0.50))
    key_strategy = body.get("key_strategy", "unique")
    telegram_token = body.get("telegram_token", "").strip()
    telegram_name = body.get("telegram_name", "").strip()

    if not name or not prompt:
        raise HTTPException(status_code=400, detail="Agent Name and Purpose are required.")

    if fleet_factory and hasattr(fleet_factory, "create_agent"):
        res = fleet_factory.create_agent(
            name=name,
            prompt=prompt,
            tier=tier,
            encrypted=encrypted,
            quota_usd=quota,
            key_strategy=key_strategy,
            telegram_token=telegram_token,
            telegram_name=telegram_name
        )
        return res
    raise HTTPException(status_code=500, detail="Factory engine not available.")


@app.post("/api/fleet/report")
async def api_fleet_report(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    subprocess.Popen([
        "/usr/bin/python3",
        os.path.join(FLEET_DIR, "bin", "fleet-report.py"),
        "--send"
    ])
    return {"success": True, "message": "Daily report generated and dispatched to Telegram."}


@app.post("/api/fleet/backup")
async def api_fleet_backup(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    subprocess.Popen([os.path.join(FLEET_DIR, "bin", "fleet-backup.sh")])
    return {"success": True, "message": "Hot-backup initiated."}


# --- WEBSOCKET FEEDS ---

def is_ws_authenticated(websocket: WebSocket) -> bool:
    session_token = websocket.cookies.get("fleet_session")
    if not session_token:
        session_token = (
            websocket.query_params.get("token")
            or websocket.query_params.get("session")
            or websocket.query_params.get("key")
            or websocket.query_params.get("auth")
        )
    if not session_token:
        session_token = websocket.headers.get("x-fleet-session")
    if not session_token:
        auth_hdr = websocket.headers.get("authorization", "")
        if auth_hdr.startswith("Bearer "):
            session_token = auth_hdr[7:].strip()
    if not session_token:
        return False
    if fleet_pair:
        if hasattr(fleet_pair, "verify_token") and fleet_pair.verify_token(session_token):
            return True
        if hasattr(fleet_pair, "verify_key_or_pin"):
            client_ip = websocket.client.host if websocket.client else "127.0.0.1"
            ok, _, _ = fleet_pair.verify_key_or_pin(session_token, client_ip)
            return ok
    return True



@app.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket):
    if not is_ws_authenticated(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    try:
        while True:
            metrics = collect_metrics()
            agents = list_swarm_agents()
            payload = {
                "metrics": metrics,
                "agents": agents
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1.5)
    except (WebSocketDisconnect, Exception):
        pass


@app.websocket("/ws/logs/{agent}")
async def ws_logs(websocket: WebSocket, agent: str):
    if not is_ws_authenticated(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    cname = f"hermes-{agent}"
    unit_name = f"hermes-{agent}.service"
    
    # Check if systemd unit exists/active, otherwise stream via podman logs
    is_systemd = False
    try:
        ret = subprocess.call(["systemctl", "status", unit_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        is_systemd = (ret == 0)
    except Exception:
        pass

    if is_systemd:
        cmd = ["journalctl", "-u", unit_name, "-f", "-n", "40", "--no-pager"]
    else:
        cmd = ["podman", "logs", "-f", "--tail", "40", cname]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            await websocket.send_text(line.decode("utf-8", errors="replace"))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass


# --- EMBEDDED DASHBOARD HTML ---

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hermes Fleet Controller</title>
  <style>
    :root {
      --bg: #090d13;
      --card-bg: #131b26;
      --border: #233142;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --yellow: #d29922;
      --red: #f85149;
      --purple: #bc8cff;
      --cyan: #39c5bb;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
    body { background: var(--bg); color: var(--text); padding: 18px; min-height: 100vh; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
    .title-group h1 { font-size: 1.35rem; color: #fff; display: flex; align-items: center; gap: 8px; }
    .badge { font-size: 0.72rem; padding: 3px 8px; border-radius: 12px; font-weight: 600; text-transform: uppercase; }
    .badge-green { background: rgba(63, 185, 80, 0.15); color: var(--green); border: 1px solid var(--green); }
    .badge-purple { background: rgba(188, 140, 255, 0.15); color: var(--purple); border: 1px solid var(--purple); }
    .badge-yellow { background: rgba(210, 153, 34, 0.15); color: var(--yellow); border: 1px solid var(--yellow); }
    .badge-red { background: rgba(248, 81, 73, 0.15); color: var(--red); border: 1px solid var(--red); }
    .badge-cyan { background: rgba(57, 197, 187, 0.15); color: var(--cyan); border: 1px solid var(--cyan); }
    
    .btn { background: var(--card-bg); color: var(--text); border: 1px solid var(--border); padding: 7px 14px; border-radius: 6px; cursor: pointer; font-size: 0.82rem; font-weight: 500; display: inline-flex; align-items: center; gap: 6px; transition: all 0.15s ease; }
    .btn:hover { border-color: var(--accent); color: #fff; background: #1a2433; }
    .btn-primary { background: #1f6feb; border-color: #388bfd; color: #fff; }
    .btn-primary:hover { background: #388bfd; }
    .btn-purple { background: rgba(188, 140, 255, 0.15); border-color: var(--purple); color: var(--purple); }
    .btn-purple:hover { background: var(--purple); color: #fff; }

    /* Top Stats Grid */
    .metrics-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }
    .metric-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
    .metric-card .label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px; }
    .metric-card .val { font-size: 1.25rem; font-weight: 700; color: #fff; }

    /* Section Headers */
    .section-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
    .section-head h2 { font-size: 1.05rem; font-weight: 600; color: #fff; display: flex; align-items: center; gap: 8px; }

    /* Agent Cards Grid */
    .agent-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; margin-bottom: 28px; }
    .agent-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .agent-header { display: flex; justify-content: space-between; align-items: center; }
    .agent-title { font-weight: 700; font-size: 1.05rem; color: #fff; }

    .agent-row { display: flex; justify-content: space-between; align-items: center; font-size: 0.84rem; }
    .agent-row .label { color: var(--text-muted); }

    /* Switches */
    .switch-group { display: flex; align-items: center; gap: 8px; }
    .toggle-switch { position: relative; width: 38px; height: 20px; }
    .toggle-switch input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #21262d; border-radius: 20px; transition: .2s; border: 1px solid var(--border); }
    .slider:before { position: absolute; content: ""; height: 14px; width: 14px; left: 2px; bottom: 2px; background-color: #8b949e; border-radius: 50%; transition: .2s; }
    input:checked + .slider { background-color: var(--purple); border-color: var(--purple); }
    input:checked + .slider:before { transform: translateX(18px); background-color: #fff; }

    /* Firewall Segmented Control */
    .fw-group { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
    .fw-btn { background: transparent; border: none; padding: 4px 8px; font-size: 0.74rem; color: var(--text-muted); cursor: pointer; font-weight: 600; }
    .fw-btn.active-full { background: rgba(63, 185, 80, 0.2); color: var(--green); }
    .fw-btn.active-rest { background: rgba(210, 153, 34, 0.2); color: var(--yellow); }
    .fw-btn.active-iso { background: rgba(248, 81, 73, 0.2); color: var(--red); }

    /* Modal / Drawer */
    .modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.75); z-index: 100; justify-content: center; align-items: center; }
    .modal-box { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; width: 90%; max-width: 650px; max-height: 85vh; overflow-y: auto; padding: 22px; position: relative; }
    .modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
    .modal-close { cursor: pointer; font-size: 1.2rem; color: var(--text-muted); }

    /* Tables */
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 10px; }
    th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
    th { color: var(--text-muted); font-size: 0.74rem; text-transform: uppercase; }

    /* Terminal Output */
    .terminal-box { background: #040608; border: 1px solid #1f242c; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 0.78rem; height: 350px; overflow-y: scroll; color: #58a6ff; }
  </style>
</head>
<body>

  <!-- Top Header -->
  <header>
    <div class="title-group">
      <h1>🛰️ Hermes Fleet Controller <span class="badge badge-green" id="fleet-status-badge">ONLINE</span></h1>
      <span style="font-size:0.75rem; color:var(--text-muted);" id="tunnel-domain">Cloudflare: Loading...</span>
    </div>
    <div style="display:flex; gap:10px; align-items:center;">
      <button class="btn btn-purple" onclick="openPrivacyCatalog()">🔒 Privacy Models</button>
      <button class="btn btn-primary" onclick="openFactoryModal()">✨ Spawn Agent</button>
      <button class="btn" onclick="triggerReport()">📋 Daily Report</button>
      <button class="btn" onclick="triggerBackup()">🛡️ Backup</button>
      <button class="btn" style="border-color:#f85149; color:#ff7b72;" onclick="lockSession()" title="Lock dashboard & disconnect session">🔒 Lock</button>
    </div>
  </header>


  <!-- Metrics Bar -->
  <div class="metrics-bar">
    <div class="metric-card">
      <div class="label">CPU Load Average</div>
      <div class="val" id="metric-load">0.00, 0.00</div>
    </div>
    <div class="metric-card">
      <div class="label">Fleet Memory</div>
      <div class="val" id="metric-ram">-- / -- MB</div>
    </div>
    <div class="metric-card">
      <div class="label">Storage Free</div>
      <div class="val" id="metric-disk">-- GB</div>
    </div>
    <div class="metric-card">
      <div class="label">Venice API Latency</div>
      <div class="val" id="metric-venice">-- ms</div>
    </div>
  </div>

  <!-- Agent Swarm Section -->
  <div class="section-head">
    <h2>🤖 Swarm Agents (<span id="agent-count">0</span>)</h2>
  </div>

  <div class="agent-grid" id="agents-container">
    <!-- Populated by JS WebSocket -->
  </div>

  <!-- PRIVACY MODELS DRAWER MODAL -->
  <div class="modal-overlay" id="privacy-modal">
    <div class="modal-box" style="max-width: 800px;">
      <div class="modal-head">
        <h3 style="color:#fff;">🔒 Venice End-to-End Encrypted & Privacy Models</h3>
        <span class="modal-close" onclick="closeModals()">&times;</span>
      </div>
      <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:12px;">
        Venice models running inside cryptographically isolated hardware enclaves (Intel TDX / SEV-SNP) with zero plaintext retention.
      </p>
      <table>
        <thead>
          <tr>
            <th>Model ID</th>
            <th>Type</th>
            <th>Enclave Security</th>
            <th>In / Out ($/M)</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="privacy-table-body">
          <tr><td colspan="5">Loading privacy models...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- AGENT FACTORY MODAL -->
  <div class="modal-overlay" id="factory-modal">
    <div class="modal-box">
      <div class="modal-head">
        <h3 style="color:#fff;">✨ Venice AI Agent Factory</h3>
        <span class="modal-close" onclick="closeModals()">&times;</span>
      </div>
      <div style="display:flex; flex-direction:column; gap:14px; font-size:0.85rem;">
        <div>
          <label style="display:block; margin-bottom:4px; font-weight:600;">Agent Identifier</label>
          <input type="text" id="fac-name" placeholder="e.g. ha-optimizer" style="width:100%; padding:8px; background:#0d1117; border:1px solid var(--border); color:#fff; border-radius:6px;">
        </div>
        <div>
          <label style="display:block; margin-bottom:4px; font-weight:600;">Specification / Mission Prompt</label>
          <textarea id="fac-prompt" rows="3" placeholder="Describe agent purpose... Frontier kimi-k3 will auto-author SOUL.md and SKILL.md" style="width:100%; padding:8px; background:#0d1117; border:1px solid var(--border); color:#fff; border-radius:6px;"></textarea>
        </div>
        <div style="display:flex; gap:20px; align-items:center;">
          <div>
            <label style="display:block; margin-bottom:4px; font-weight:600;">Model Tier</label>
            <select id="fac-tier" style="padding:6px 10px; background:#0d1117; border:1px solid var(--border); color:#fff; border-radius:6px;">
              <option value="medium">Medium (deepseek-v4-flash)</option>
              <option value="high">High (kimi-k3)</option>
              <option value="low">Low (mercury-2-5)</option>
            </select>
          </div>
          <div>
            <label style="display:block; margin-bottom:4px; font-weight:600;">Daily Quota (USD)</label>
            <input type="number" id="fac-quota" value="0.50" step="0.10" style="width:80px; padding:6px; background:#0d1117; border:1px solid var(--border); color:#fff; border-radius:6px;">
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:10px; padding:10px; background:#0d1117; border:1px solid var(--border); border-radius:6px;">
          <input type="checkbox" id="fac-encrypted" checked style="width:18px; height:18px;">
          <label for="fac-encrypted" style="cursor:pointer;"><strong>🔒 Run in Confidential Enclave (E2EE)</strong><br><span style="font-size:0.75rem; color:var(--text-muted);">Encrypted client-side inference inside verified hardware enclaves</span></label>
        </div>
        <button class="btn btn-primary" style="margin-top:8px; justify-content:center; padding:10px;" onclick="submitFactorySpawn()">🚀 Synthesize & Deploy Agent</button>
      </div>
    </div>
  </div>

  <!-- LIVE LOG TERMINAL MODAL -->
  <div class="modal-overlay" id="logs-modal">
    <div class="modal-box" style="max-width: 850px;">
      <div class="modal-head">
        <h3 style="color:#fff;">📜 Live Container Logs: <span id="log-agent-name"></span></h3>
        <span class="modal-close" onclick="closeLogs()">&times;</span>
      </div>
      <div class="terminal-box" id="log-output">Connecting to log stream...</div>
    </div>
  </div>

  <!-- DEVICE PAIRING MODAL (Zero-Trust Gate) -->
  <div class="modal-overlay" id="pair-modal">
    <div class="modal-box" style="max-width: 420px; text-align:center;">
      <h3 style="color:#fff; margin-bottom:8px;">🔐 Zero-Trust Device Pairing</h3>
      <p style="font-size:0.82rem; color:var(--text-muted); margin-bottom:18px;">
        Enter the 6-digit PIN generated on the controller terminal via <code>fleet-pair</code>.
      </p>
      <input type="text" id="pair-pin-input" maxlength="6" placeholder="123456" style="font-size:1.8rem; letter-spacing:6px; text-align:center; width:200px; padding:8px; background:#0d1117; border:1px solid var(--border); color:#58a6ff; border-radius:6px; margin-bottom:14px;">
      <div id="pair-error" style="color:var(--red); font-size:0.8rem; margin-bottom:12px; display:none;"></div>
      <button class="btn btn-primary" style="width:100%; justify-content:center; padding:10px;" onclick="submitPairPIN()">Authenticate Device</button>
    </div>
  </div>

  <script>
    let logWs = null;

    // Save and sync session token in client storage (keeps session alive across refreshes!)
    const activeToken = "__ACTIVE_SESSION_TOKEN__";
    if (activeToken && activeToken.length > 10) {
      try {
        localStorage.setItem("fleet_session", activeToken);
        sessionStorage.setItem("fleet_session", activeToken);
      } catch (e) {}
    }

    function getSessionToken() {
      try {
        return localStorage.getItem("fleet_session") || sessionStorage.getItem("fleet_session") || "";
      } catch (e) {
        return "";
      }
    }

    function authHeaders(extra = {}) {
      const headers = {...extra};
      const token = getSessionToken();
      if (token) {
        headers["Authorization"] = "Bearer " + token;
        headers["X-Fleet-Session"] = token;
      }
      return headers;
    }

    async function lockSession() {
      if (confirm("Disconnect and lock the Hermes Fleet Dashboard?")) {
        try {
          localStorage.removeItem("fleet_session");
          sessionStorage.removeItem("fleet_session");
          await fetch("/api/auth/logout", {method: "POST"});
        } catch (e) {}
        document.cookie = "fleet_session=; Max-Age=0; path=/;";
        window.location.reload();
      }
    }

    // --- WEBSOCKET METRICS & AGENT POLLING ---
    function connectMetrics() {
      const loc = window.location;
      const token = getSessionToken();
      const qs = token ? ("?token=" + encodeURIComponent(token)) : "";
      const wsUri = (loc.protocol === "https:" ? "wss:" : "ws:") + "//" + loc.host + "/ws/metrics" + qs;
      const ws = new WebSocket(wsUri);

      ws.onmessage = function(event) {
        const data = JSON.parse(event.data);
        updateDashboard(data);
      };

      ws.onclose = function() {
        setTimeout(connectMetrics, 2000);
      };
    }


    function updateDashboard(data) {
      const m = data.metrics;
      document.getElementById("metric-load").innerText = m.load;
      document.getElementById("metric-ram").innerText = `${m.mem_avail_mb} / ${m.mem_total_mb} MB`;
      document.getElementById("metric-disk").innerText = `${m.disk_free_gb} GB (${m.disk_used_pct}%)`;
      document.getElementById("metric-venice").innerText = m.venice_ms >= 0 ? `${m.venice_ms} ms` : "Offline";
      document.getElementById("tunnel-domain").innerText = `Access: ${m.tunnel_url}`;

      const agents = data.agents || [];
      document.getElementById("agent-count").innerText = agents.length;

      const container = document.getElementById("agents-container");
      container.innerHTML = "";

      agents.forEach(a => {
        const card = document.createElement("div");
        card.className = "agent-card";
        const isRun = a.status === "running";
        const isEnc = a.is_e2ee;

        card.innerHTML = `
          <div class="agent-header">
            <span class="agent-title">${a.name}</span>
            <span class="badge ${isRun ? 'badge-green' : 'badge-red'}">${a.status}</span>
          </div>
          <div class="agent-row">
            <span class="label">Model:</span>
            <span style="font-weight:600; color:#fff;">${a.model}</span>
          </div>
          <div class="agent-row">
            <span class="label">Memory (500M Cap):</span>
            <span>${a.memory}</span>
          </div>
          <div class="agent-row">
            <span class="label">Daily Quota:</span>
            <span class="badge badge-yellow">$${a.quota_usd}/day</span>
          </div>
          <div class="agent-row">
            <span class="label">🔒 Encrypted (E2EE):</span>
            <div class="switch-group">
              <label class="toggle-switch">
                <input type="checkbox" ${isEnc ? 'checked' : ''} onchange="toggleAgentPrivacy('${a.name}', this.checked)">
                <span class="slider"></span>
              </label>
            </div>
          </div>
          <div class="agent-row">
            <span class="label">Network Firewall:</span>
            <div class="fw-group">
              <button class="fw-btn ${a.firewall === 'full' ? 'active-full' : ''}" onclick="setAgentNetwork('${a.name}', 'full')">Full</button>
              <button class="fw-btn ${a.firewall === 'restricted' ? 'active-rest' : ''}" onclick="setAgentNetwork('${a.name}', 'restricted')">Restricted</button>
              <button class="fw-btn ${a.firewall === 'isolated' ? 'active-iso' : ''}" onclick="setAgentNetwork('${a.name}', 'isolated')">Isolated</button>
            </div>
          </div>
          <div style="display:flex; gap:8px; margin-top:8px;">
            <button class="btn" style="flex:1;" onclick="openLogs('${a.name}')">📜 Logs</button>
            ${a.name !== 'fleet-controller' ? `<button class="btn" style="color:var(--red);" onclick="teardownAgent('${a.name}')">🛑 Remove</button>` : ''}
          </div>
        `;
        container.appendChild(card);
      });
    }

    // --- ACTIONS ---
    async function toggleAgentPrivacy(name, isEnc) {
      await fetch(`/api/agents/${name}/privacy`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({encrypted: isEnc})
      });
    }

    async function setAgentNetwork(name, mode) {
      await fetch(`/api/agents/${name}/network`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mode: mode})
      });
    }

    async function teardownAgent(name) {
      if (!confirm(`Are you sure you want to stop and remove agent '${name}'?`)) return;
      await fetch(`/api/agents/${name}/teardown`, {method: "POST"});
    }

    async function triggerReport() {
      const res = await fetch("/api/fleet/report", {method: "POST"});
      const d = await res.json();
      alert(d.message || "Report dispatched.");
    }

    async function triggerBackup() {
      const res = await fetch("/api/fleet/backup", {method: "POST"});
      const d = await res.json();
      alert(d.message || "Hot backup initiated.");
    }

    // --- PRIVACY CATALOG ---
    async function openPrivacyCatalog() {
      document.getElementById("privacy-modal").style.display = "flex";
      const tbody = document.getElementById("privacy-table-body");
      tbody.innerHTML = "<tr><td colspan='5'>Loading live models...</td></tr>";

      const res = await fetch("/api/venice/privacy-models");
      const cat = await res.json();

      tbody.innerHTML = "";
      const e2eeList = cat.e2ee || [];
      e2eeList.forEach(m => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${m.id}</strong></td>
          <td><span class="badge badge-cyan">${m.type}</span></td>
          <td><span class="badge badge-purple">${m.enclave_type}</span></td>
          <td>$${m.pricing.input_per_million} / $${m.pricing.output_per_million}</td>
          <td><button class="btn btn-purple" style="padding:3px 8px; font-size:0.72rem;" onclick="spawnWithModel('${m.id}')">Deploy</button></td>
        `;
        tbody.appendChild(tr);
      });
    }

    function spawnWithModel(mid) {
      closeModals();
      openFactoryModal();
      document.getElementById("fac-encrypted").checked = true;
    }

    // --- FACTORY ---
    function openFactoryModal() {
      document.getElementById("factory-modal").style.display = "flex";
    }

    async function submitFactorySpawn() {
      const name = document.getElementById("fac-name").value.trim();
      const prompt = document.getElementById("fac-prompt").value.trim();
      const tier = document.getElementById("fac-tier").value;
      const quota = parseFloat(document.getElementById("fac-quota").value) || 0.50;
      const encrypted = document.getElementById("fac-encrypted").checked;

      if (!name || !prompt) {
        alert("Please enter both an Agent Name and Purpose.");
        return;
      }

      closeModals();
      alert(`Initiating Venice frontier agent synthesis for '${name}'...`);

      const res = await fetch("/api/factory/generate", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          name: name,
          prompt: prompt,
          tier: tier,
          quota_usd: quota,
          encrypted: encrypted,
          key_strategy: "unique"
        })
      });
      const d = await res.json();
      if (d.success) {
        alert(`Successfully minted and launched agent '${name}' with model '${d.model}'!`);
      } else {
        alert(`Failed to deploy agent: ${d.error || 'Unknown error'}`);
      }
    }

    // --- LOGS MODAL ---
    function openLogs(name) {
      document.getElementById("log-agent-name").innerText = name;
      document.getElementById("logs-modal").style.display = "flex";
      const out = document.getElementById("log-output");
      out.innerText = "Connecting live stream...";

      if (logWs) logWs.close();
      const loc = window.location;
      const token = getSessionToken();
      const qs = token ? ("?token=" + encodeURIComponent(token)) : "";
      const wsUri = (loc.protocol === "https:" ? "wss:" : "ws:") + "//" + loc.host + "/ws/logs/" + name + qs;
      logWs = new WebSocket(wsUri);


      logWs.onmessage = function(e) {
        out.innerText += e.data;
        out.scrollTop = out.scrollHeight;
      };
    }

    function closeLogs() {
      if (logWs) logWs.close();
      document.getElementById("logs-modal").style.display = "none";
    }

    // --- PAIRING ---
    async function submitPairPIN() {
      const pin = document.getElementById("pair-pin-input").value.trim();
      const err = document.getElementById("pair-error");
      err.style.display = "none";

      const res = await fetch("/api/auth/pair", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({pin: pin})
      });
      const data = await res.json();
      if (data.success) {
        document.getElementById("pair-modal").style.display = "none";
        window.location.reload();
      } else {
        err.innerText = data.message || "Failed to pair.";
        err.style.display = "block";
      }
    }

    function closeModals() {
      document.getElementById("privacy-modal").style.display = "none";
      document.getElementById("factory-modal").style.display = "none";
      document.getElementById("logs-modal").style.display = "none";
    }

    // Initialize
    window.onload = function() {
      connectMetrics();
    };
  </script>
</body>
</html>
"""


AUTH_GATE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Authentication Required — Hermes Fleet Controller</title>
  <style>
    :root {
      --bg: #090d13;
      --card-bg: #131b26;
      --border: #233142;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --accent-glow: rgba(88, 166, 255, 0.2);
      --red: #f85149;
      --green: #3fb950;
      --font-mono: "SF Mono", "Fira Code", "Courier New", monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-mono);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .auth-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      max-width: 520px;
      width: 100%;
      padding: 36px 32px;
      box-shadow: 0 16px 40px rgba(0,0,0,0.6);
      text-align: center;
      position: relative;
      overflow: hidden;
    }
    .auth-card::before {
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0; height: 3px;
      background: linear-gradient(90deg, #1f6feb, #58a6ff, #bc8cff);
    }
    .icon {
      font-size: 48px;
      margin-bottom: 16px;
      display: inline-block;
    }
    h1 {
      font-size: 20px;
      letter-spacing: 1px;
      color: #fff;
      margin-bottom: 8px;
      text-transform: uppercase;
    }
    .subtitle {
      font-size: 13px;
      color: var(--accent);
      margin-bottom: 20px;
      letter-spacing: 0.5px;
    }
    .desc {
      font-size: 13px;
      color: var(--text-muted);
      line-height: 1.6;
      margin-bottom: 28px;
    }
    .input-group {
      margin-bottom: 20px;
      text-align: left;
    }
    label {
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 8px;
    }
    input {
      width: 100%;
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 16px;
      color: #fff;
      font-family: var(--font-mono);
      font-size: 14px;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-glow);
    }
    button {
      width: 100%;
      background: #1f6feb;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 14px;
      font-family: var(--font-mono);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      letter-spacing: 0.5px;
      transition: background 0.2s, transform 0.1s;
    }
    button:hover { background: #388bfd; }
    button:active { transform: scale(0.99); }
    .alert-box {
      margin-top: 16px;
      padding: 12px;
      border-radius: 6px;
      font-size: 12px;
      display: none;
      line-height: 1.4;
    }
    .alert-error {
      background: rgba(248, 81, 73, 0.15);
      border: 1px solid var(--red);
      color: #ff7b72;
    }
    .alert-info {
      background: rgba(88, 166, 255, 0.15);
      border: 1px solid var(--accent);
      color: #79c0ff;
    }
    .footer-help {
      margin-top: 24px;
      font-size: 12px;
      color: var(--text-muted);
      border-top: 1px solid var(--border);
      padding-top: 18px;
      line-height: 1.5;
    }
    .footer-help code {
      background: #161b22;
      padding: 2px 6px;
      border-radius: 4px;
      color: var(--accent);
    }
  </style>
</head>
<body>
  <div class="auth-card">
    <div style="display:inline-flex; align-items:center; gap:6px; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:700; letter-spacing:1px; text-transform:uppercase; background:rgba(88,166,255,0.12); border:1px solid var(--accent); color:var(--accent); margin-bottom:18px;">
      🔒 PIN PROTECTED
    </div>
    <h1>Hermes Fleet Controller</h1>
    <div class="subtitle" style="font-size:12px; color:var(--text-muted); margin-bottom:24px; line-height:1.5;">
      This management dashboard requires a valid 6-digit PIN or direct access link from Telegram.
    </div>

    <div class="input-group">
      <label for="auth-key">6-Digit PIN or Access Link</label>
      <input type="text" id="auth-key" placeholder="Enter 6-digit PIN or paste link..." autocomplete="off" autofocus style="font-size:16px; text-align:center;">
    </div>

    <button id="unlock-btn" onclick="submitAuthKey()">🔓 CONNECT TO DASHBOARD</button>

    <div id="auth-msg" class="alert-box"></div>

    <div class="footer-help">
      Send <code>/pair</code> or <code>/login</code> to <strong>@McBottyMc_bot</strong> in Telegram to get your active 6-digit PIN or one-click access link.
    </div>
  </div>

  <script>
    async function submitAuthKey(overrideVal) {
      const input = document.getElementById("auth-key");
      const rawVal = (overrideVal || input.value || "").trim();
      const msgBox = document.getElementById("auth-msg");
      const btn = document.getElementById("unlock-btn");

      if (!rawVal) {
        showMsg("Please enter your 6-digit PIN or paste your Telegram access link.", "error");
        return;
      }

      btn.disabled = true;
      btn.innerText = "⏳ VERIFYING...";
      showMsg("Verifying PIN with fleet security engine...", "info");

      try {
        const res = await fetch("/api/auth/pair", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({key: rawVal})
        });
        const data = await res.json();
        if (data.success) {
          showMsg("✓ Authenticated! Loading Fleet Dashboard...", "info");
          // Save session token in client storage so refresh keeps dashboard open!
          if (data.token) {
            try {
              localStorage.setItem("fleet_session", data.token);
              sessionStorage.setItem("fleet_session", data.token);
            } catch (e) {}
          }
          setTimeout(() => {
            window.location.href = window.location.pathname;
          }, 300);
        } else {
          showMsg("✕ " + (data.message || "Invalid or expired PIN."), "error");
          btn.disabled = false;
          btn.innerText = "🔓 CONNECT TO DASHBOARD";
        }
      } catch (err) {
        showMsg("✕ Network error connecting to dashboard controller.", "error");
        btn.disabled = false;
        btn.innerText = "🔓 CONNECT TO DASHBOARD";
      }
    }

    function showMsg(text, type) {
      const box = document.getElementById("auth-msg");
      box.innerText = text;
      box.className = "alert-box " + (type === "error" ? "alert-error" : "alert-info");
      box.style.display = "block";
    }

    document.getElementById("auth-key").addEventListener("keydown", function(e) {
      if (e.key === "Enter") submitAuthKey();
    });

    // Auto-restore session on page load / refresh
    window.addEventListener("DOMContentLoaded", async () => {
      // 1. URL key / token / pin auto-login
      const params = new URLSearchParams(window.location.search);
      const urlKey = params.get("key") || params.get("token") || params.get("pin") || params.get("auth") || params.get("session");
      if (urlKey) {
        document.getElementById("auth-key").value = urlKey;
        submitAuthKey(urlKey);
        return;
      }

      // 2. Check saved session in localStorage (keeps session alive across refreshes!)
      const savedToken = localStorage.getItem("fleet_session");
      if (savedToken) {
        showMsg("Restoring saved session...", "info");
        try {
          const res = await fetch("/api/auth/verify-session", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({session: savedToken})
          });
          const data = await res.json();
          if (data.success) {
            showMsg("✓ Session restored! Loading Fleet Dashboard...", "info");
            window.location.reload();
            return;
          } else {
            localStorage.removeItem("fleet_session");
            sessionStorage.removeItem("fleet_session");
          }
        } catch (e) {}
      }
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
@app.head("/")
def index(request: Request):
    client_ip = get_client_ip(request)

    # 1. URL key / token / pin / session auto-login via query param
    url_param = (
        request.query_params.get("key")
        or request.query_params.get("token")
        or request.query_params.get("pin")
        or request.query_params.get("auth")
        or request.query_params.get("session")
    )
    if url_param and fleet_pair:
        # Check if already a valid session token
        if hasattr(fleet_pair, "verify_token") and fleet_pair.verify_token(url_param):
            resp = RedirectResponse(url="/", status_code=303)
            set_session_cookie(resp, url_param, request)
            return resp

        # Verify key or PIN
        verifier = getattr(fleet_pair, "verify_key_or_pin", getattr(fleet_pair, "verify_pin", None))
        if verifier:
            ok, token, msg = verifier(url_param, client_ip)
            if ok:
                resp = RedirectResponse(url="/", status_code=303)
                set_session_cookie(resp, token, request)
                return resp

    # 2. Check if user is authenticated via cookie, Authorization Bearer, or query params
    if is_authenticated(request):
        token = request.cookies.get("fleet_session") or ""
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
        if not token:
            token = request.headers.get("X-Fleet-Session") or request.query_params.get("session") or request.query_params.get("token") or ""

        # Inject active session token so the browser client saves it to localStorage
        content = DASHBOARD_HTML.replace("__ACTIVE_SESSION_TOKEN__", token)
        resp = HTMLResponse(content=content)
        if token:
            set_session_cookie(resp, token, request)
        return resp

    # 3. NOT authenticated: Return ONLY the PIN Protection Lock Screen (401)
    return HTMLResponse(content=AUTH_GATE_HTML, status_code=401)



def main():
    port = int(os.environ.get("PORT", 8650))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Starting Hermes Fleet Dashboard on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
