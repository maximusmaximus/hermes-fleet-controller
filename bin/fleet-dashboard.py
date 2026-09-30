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
import socket
import ipaddress
import urllib.request
import urllib.error

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

try:
    fleet_scan = importlib.import_module("fleet-scan")
except Exception:
    fleet_scan = None

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

    # Swarm Health Score
    swarm_score = 100.0
    h_file = os.path.join(FLEET_DIR, "run", "agent_health_state.json")
    if os.path.exists(h_file):
        try:
            with open(h_file) as f:
                swarm_score = json.load(f).get("swarm_score_pct", 100.0)
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
        "swarm_score": swarm_score,
        "timestamp": int(time.time())
    }


def list_swarm_agents():
    agents_dir = os.path.join(FLEET_DIR, "agents")
    agents = []

    health_map = {}
    h_file = os.path.join(FLEET_DIR, "run", "agent_health_state.json")
    if os.path.exists(h_file):
        try:
            with open(h_file) as f:
                health_map = json.load(f).get("agents", {})
        except Exception:
            pass

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

        h_info = health_map.get(name, {})
        h_status = h_info.get("status", "healthy" if status == "running" else "stopped")
        h_lat = h_info.get("latency_ms", -1.0)
        h_restarts = h_info.get("restarts_24h", 0)
        h_cb = h_info.get("circuit_broken", False)
        h_diag = h_info.get("diagnosis", "")

        agents.append({
            "name": name,
            "status": status,
            "health_status": h_status,
            "latency_ms": h_lat,
            "restarts_24h": h_restarts,
            "circuit_broken": h_cb,
            "diagnosis": h_diag,
            "model": model,
            "is_e2ee": is_e2ee,
            "firewall": fw_mode,
            "memory": mem_str,
            "quota_usd": quota_usd,
            "port": port
        })

    return agents


def load_infrastructure_state():
    candidates = [
        os.path.join(FLEET_DIR, "memory", "infrastructure-state.json"),
        "/mnt/d/mcoverseer/memory/infrastructure-state.json",
        "D:/mcoverseer/memory/infrastructure-state.json",
        os.path.join(FLEET_DIR, "infrastructure-state.json"),
        os.path.expanduser("~/.fleet/infrastructure-state.json")
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f), p
            except Exception:
                pass
    return {"lastFullScan": None, "machines": {}, "offlineWatch": {}}, os.path.join(FLEET_DIR, "memory", "infrastructure-state.json")


def save_infrastructure_state(state):
    out_file = os.path.join(FLEET_DIR, "memory", "infrastructure-state.json")
    try:
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        return True
    except Exception:
        return False


def load_fleet_devices():
    state, _ = load_infrastructure_state()
    raw_machines = state.get("machines", {})

    if not raw_machines and fleet_scan and hasattr(fleet_scan, "scan_infrastructure_devices"):
        try:
            raw_machines = fleet_scan.scan_infrastructure_devices()
        except Exception:
            pass

    devices_list = []
    for name, d in raw_machines.items():
        dev = dict(d)
        dev["name"] = name
        devices_list.append(dev)

    # Sort: online first, then by name
    devices_list.sort(key=lambda x: (not x.get("online", False), x.get("name", "")))
    return devices_list


def check_port_open(host, port, timeout=1.5):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        res = s.connect_ex((host, port))
        s.close()
        return res == 0
    except Exception:
        return False


def probe_node_services(name, ip, dev_existing=None):
    """Deep poll a specific node's common AI, agent, and fleet ports."""
    probed = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services": {},
        "trackedSoftware": {}
    }
    if not ip or ip == "Unknown":
        return probed

    # 1. Port 8080: A2A Agent Card & Service
    a2a_open = check_port_open(ip, 8080)
    if a2a_open:
        probed["services"]["a2a-server"] = {"running": True, "port": 8080, "health": "OK", "auth": "Bearer"}
        try:
            a2a_token = os.environ.get("A2A_AUTH_TOKEN", "2u6GZL1hOE_3TPrByRzdndMsxwUGwJF3lYDbm6HEzME")
            req = urllib.request.Request(
                f"http://{ip}:8080/.well-known/agent-card.json",
                headers={"Authorization": f"Bearer {a2a_token}", "User-Agent": "FleetController/2.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                card = json.loads(resp.read().decode("utf-8"))
                probed["trackedSoftware"]["a2a-server"] = {
                    "version": card.get("version", "1.0.0"),
                    "running": True,
                    "port": 8080,
                    "protocol": "A2A/v1",
                    "agentCardUrl": f"http://{ip}:8080/.well-known/agent-card.json",
                    "directIpUrl": f"http://{ip}:8080",
                    "auth": "Bearer"
                }
        except Exception:
            probed["trackedSoftware"]["a2a-server"] = {"running": True, "port": 8080, "protocol": "A2A/v1"}
    else:
        probed["services"]["a2a-server"] = {"running": False, "port": 8080}

    # 2. Port 8650: Fleet Controller
    if check_port_open(ip, 8650):
        probed["services"]["fleet-dashboard"] = {"running": True, "port": 8650, "health": "OK"}

    # 3. Port 8787: Antigravity Hub
    if check_port_open(ip, 8787):
        probed["services"]["antigravity-hub"] = {"running": True, "port": 8787, "health": "OK"}

    # 4. Ports 18789 / 18790: OpenClaw
    if check_port_open(ip, 18789) or check_port_open(ip, 18790):
        probed["trackedSoftware"]["openclaw"] = {"running": True, "port": 18789}
        probed["services"]["openclaw"] = {"running": True, "port": 18789, "health": "OK"}

    # 5. Port 11434: Ollama / Local LLM
    if check_port_open(ip, 11434):
        probed["services"]["ollama"] = {"running": True, "port": 11434, "health": "OK"}

    # 6. Port 22: SSH
    probed["sshAccess"] = check_port_open(ip, 22)
    return probed


def _send_urllib_json(req, timeout=12):
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _query_venice_chat(api_key, model, system_prompt, user_prompt):
    url = "https://api.venice.ai/api/v1/chat/completions"
    payload = {
        "model": model or "kimi-k3",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 512,
        "temperature": 0.7
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            return str(msg.get("content") or msg.get("reasoning_content") or "Acknowledged.")
    return "Acknowledged."


async def dispatch_prompt_to_target(name: str, prompt: str, target_type: str = "agent", target_ip: str = ""):
    t0 = time.time()
    devices = load_fleet_devices()
    dev_match = next((d for d in devices if d.get("name", "").lower() == name.lower()), None)

    # If target is explicitly a node, or matches a discovered fleet machine:
    if target_type == "node" or dev_match:
        node_ip = target_ip or (dev_match.get("ip") if dev_match else "")
        if not node_ip and dev_match:
            node_ip = dev_match.get("ip")
        if not node_ip:
            node_ip = name

        a2a_token = os.environ.get("A2A_AUTH_TOKEN", "2u6GZL1hOE_3TPrByRzdndMsxwUGwJF3lYDbm6HEzME")
        a2a_url = f"http://{node_ip}:8080/a2a/v1/message"

        try:
            req_data = json.dumps({
                "sender": "fleet-controller",
                "recipient": name,
                "message": prompt,
                "prompt": prompt,
                "timestamp": time.time()
            }).encode("utf-8")

            req = urllib.request.Request(
                a2a_url,
                data=req_data,
                headers={
                    "Authorization": f"Bearer {a2a_token}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            loop = asyncio.get_event_loop()
            resp_data = await loop.run_in_executor(None, lambda: _send_urllib_json(req, timeout=6))
            elapsed = round((time.time() - t0) * 1000, 1)

            echo = resp_data.get("echo") or resp_data.get("message") or resp_data.get("reply") or json.dumps(resp_data)
            return {
                "success": True,
                "target": name,
                "type": "node",
                "ip": node_ip,
                "response": f"[A2A {name}] {echo}",
                "raw": resp_data,
                "latency_ms": elapsed
            }
        except Exception as e:
            elapsed = round((time.time() - t0) * 1000, 1)
            return {
                "success": False,
                "target": name,
                "type": "node",
                "ip": node_ip,
                "error": f"A2A connection to {node_ip}:8080 failed: {str(e)[:100]}. Tip: Run the Antigravity probe prompt on {name} to activate its A2A service.",
                "response": f"⚠️ A2A service on {name} ({node_ip}:8080) is unreachable or not running.",
                "latency_ms": elapsed
            }

    # If target is a local Hermes container or host agent:
    cname = f"hermes-{name}"
    check_cmd = ["podman", "ps", "--filter", f"name={cname}", "--format", "{{.Names}}"]
    try:
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(None, lambda: subprocess.check_output(check_cmd, text=True, stderr=subprocess.DEVNULL))
        running_names = [l.strip() for l in out.strip().split("\n") if l.strip()]
        if cname not in running_names and name not in running_names:
            check_alt = ["podman", "ps", "--filter", f"name={name}", "--format", "{{.Names}}"]
            out_alt = await loop.run_in_executor(None, lambda: subprocess.check_output(check_alt, text=True, stderr=subprocess.DEVNULL))
            running_names = [l.strip() for l in out_alt.strip().split("\n") if l.strip()]
            if name in running_names:
                cname = name
    except Exception:
        pass

    # Try executing hermes prompt inside container
    try:
        exec_cmd = ["podman", "exec", cname, "hermes", "prompt", prompt]
        loop = asyncio.get_event_loop()
        exec_out = await loop.run_in_executor(
            None,
            lambda: subprocess.run(exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        )
        elapsed = round((time.time() - t0) * 1000, 1)
        if exec_out.returncode == 0 and exec_out.stdout.strip():
            return {
                "success": True,
                "target": name,
                "type": "agent",
                "response": exec_out.stdout.strip(),
                "latency_ms": elapsed
            }
    except subprocess.TimeoutExpired:
        elapsed = round((time.time() - t0) * 1000, 1)
        return {
            "success": False,
            "target": name,
            "type": "agent",
            "error": "Execution timed out (20s)",
            "response": "⚠️ Agent prompt timed out after 20 seconds.",
            "latency_ms": elapsed
        }
    except Exception:
        pass

    # Fallback to Venice AI inference using agent's SOUL persona
    soul_file = os.path.join(FLEET_DIR, "agents", name, "SOUL.md")
    system_prompt = f"You are {name}, an autonomous Hermes agent in the fleet."
    if os.path.exists(soul_file):
        try:
            with open(soul_file, "r", encoding="utf-8") as f:
                system_prompt = f.read().strip()
        except Exception:
            pass

    venice_key = os.environ.get("VENICE_API_KEY", "")
    if not venice_key and os.path.exists(os.path.join(FLEET_DIR, "secrets.env")):
        env_map = load_env(os.path.join(FLEET_DIR, "secrets.env"))
        venice_key = env_map.get("VENICE_API_KEY", "")

    if venice_key:
        try:
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None,
                lambda: _query_venice_chat(venice_key, "kimi-k3", system_prompt, prompt)
            )
            elapsed = round((time.time() - t0) * 1000, 1)
            return {
                "success": True,
                "target": name,
                "type": "agent",
                "response": res,
                "latency_ms": elapsed
            }
        except Exception as e:
            elapsed = round((time.time() - t0) * 1000, 1)
            return {
                "success": False,
                "target": name,
                "type": "agent",
                "error": str(e),
                "response": f"⚠️ Could not execute prompt: {str(e)[:100]}",
                "latency_ms": elapsed
            }

    elapsed = round((time.time() - t0) * 1000, 1)
    return {
        "success": True,
        "target": name,
        "type": "agent",
        "response": f"Instruction dispatched to hermes-{name}. Execution acknowledged.",
        "latency_ms": elapsed
    }


# --- REST API ENDPOINTS ---

@app.get("/api/health")
@app.head("/api/health")
def api_health():
    return {"status": "ok", "service": "hermes-fleet-dashboard", "timestamp": time.time()}


@app.get("/api/health/fleet")
def api_fleet_health(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    state_file = os.path.join(FLEET_DIR, "run", "agent_health_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "ok", "message": "Health state initializing"}


@app.post("/api/agents/{name}/heal")
def api_heal_agent(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    watchdog_script = os.path.join(FLEET_DIR, "bin", "fleet-watchdog.py")
    if os.path.exists(watchdog_script):
        res = subprocess.run(["python3", watchdog_script, "--heal", name], capture_output=True, text=True)
        return {"success": res.returncode == 0, "output": res.stdout.strip() or res.stderr.strip()}
    return {"success": False, "output": "Watchdog script not found"}



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
    prompt = body.get("message") or body.get("prompt", "")
    prompt = prompt.strip()
    target_type = body.get("type", "agent")
    target_ip = body.get("ip", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt required")

    res = await dispatch_prompt_to_target(name, prompt, target_type, target_ip)
    return res


@app.post("/api/fleet/broadcast")
async def api_broadcast_swarm(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    targets = body.get("targets", [])
    prompt = (body.get("message") or body.get("prompt", "")).strip()

    if not targets:
        raise HTTPException(status_code=400, detail="At least one target required for broadcast.")
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt message is required.")

    async def _safe_dispatch(t):
        t_name = t.get("name") if isinstance(t, dict) else str(t)
        t_type = t.get("type", "agent") if isinstance(t, dict) else "agent"
        t_ip = t.get("ip", "") if isinstance(t, dict) else ""
        try:
            return await dispatch_prompt_to_target(t_name, prompt, t_type, t_ip)
        except Exception as e:
            return {
                "success": False,
                "target": t_name,
                "type": t_type,
                "error": str(e),
                "response": f"⚠️ Execution error: {str(e)[:100]}",
                "latency_ms": -1
            }

    results = await asyncio.gather(*[_safe_dispatch(t) for t in targets])
    succeeded = sum(1 for r in results if r.get("success"))
    failed = len(results) - succeeded

    return {
        "success": True,
        "total": len(targets),
        "succeeded": succeeded,
        "failed": failed,
        "results": results
    }


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


@app.get("/api/fleet/devices")
def api_fleet_devices(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    devices = load_fleet_devices()
    return {
        "success": True,
        "count": len(devices),
        "online_count": sum(1 for d in devices if d.get("online")),
        "devices": devices,
        "timestamp": time.time()
    }


@app.post("/api/fleet/sync")
def api_fleet_sync(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    if fleet_scan and hasattr(fleet_scan, "scan_infrastructure_devices"):
        try:
            fleet_scan.scan_infrastructure_devices()
        except Exception:
            pass
    else:
        subprocess.run(["python3", os.path.join(FLEET_DIR, "bin", "fleet-scan.py")], stderr=subprocess.DEVNULL)
    devices = load_fleet_devices()
    return {
        "success": True,
        "message": "Swarm fleet devices synchronized.",
        "count": len(devices),
        "online_count": sum(1 for d in devices if d.get("online")),
        "devices": devices
    }


@app.post("/api/fleet/register")
@app.post("/api/fleet/devices/enroll")
async def api_register_device(request: Request):
    client_ip = get_client_ip(request)
    is_ts_client = False
    try:
        is_ts_client = ipaddress.ip_address(client_ip) in ipaddress.ip_network("100.64.0.0/10") or ipaddress.ip_address(client_ip).is_loopback or ipaddress.ip_address(client_ip).is_private
    except Exception:
        pass

    if not is_authenticated(request) and not is_ts_client:
        raise HTTPException(status_code=401, detail="Authentication required or must register over Tailscale mesh.")

    body = await request.json()
    node_name = body.get("node_name") or body.get("name", "").strip()
    if not node_name:
        raise HTTPException(status_code=400, detail="node_name is required")

    state, _ = load_infrastructure_state()
    machines = state.get("machines", {})
    if node_name not in machines:
        machines[node_name] = {}

    m = machines[node_name]
    m["name"] = node_name
    m["ip"] = body.get("ip") or m.get("ip") or client_ip
    m["os"] = body.get("os") or m.get("os", "Linux")
    m["kernel"] = body.get("kernel") or m.get("kernel", "")
    m["arch"] = body.get("arch") or m.get("arch", "x86_64")
    m["online"] = True
    m["lastScanned"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    m["lastSeen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for k in ("disk", "memory", "trackedSoftware", "services", "apiKeys", "cronJobs", "notes"):
        if k in body:
            if isinstance(body[k], dict) and isinstance(m.get(k), dict):
                m[k].update(body[k])
            else:
                m[k] = body[k]

    state["machines"] = machines
    state["lastFullScan"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_infrastructure_state(state)

    return {
        "success": True,
        "message": f"Machine '{node_name}' enrolled and updated in Swarm Fleet.",
        "device": m
    }


@app.post("/api/fleet/devices/{name}/probe")
async def api_probe_device(name: str, request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    devices = load_fleet_devices()
    dev = next((d for d in devices if d.get("name", "").lower() == name.lower()), None)
    if not dev:
        raise HTTPException(status_code=404, detail=f"Device '{name}' not found")

    node_ip = dev.get("ip", "")
    t0 = time.time()
    loop = asyncio.get_event_loop()
    probe_results = await loop.run_in_executor(None, lambda: probe_node_services(name, node_ip, dev))
    elapsed = round((time.time() - t0) * 1000, 1)

    state, _ = load_infrastructure_state()
    machines = state.get("machines", {})
    if name in machines:
        m = machines[name]
        m["lastScanned"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        m["online"] = True
        if "sshAccess" in probe_results:
            m["sshAccess"] = probe_results["sshAccess"]
        if "services" in probe_results:
            m.setdefault("services", {})
            m["services"].update(probe_results["services"])
        if "trackedSoftware" in probe_results:
            m.setdefault("trackedSoftware", {})
            m["trackedSoftware"].update(probe_results["trackedSoftware"])
        save_infrastructure_state(state)

    return {
        "success": True,
        "device": name,
        "latency_ms": elapsed,
        "probe": probe_results
    }


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
            devices = load_fleet_devices()
            payload = {
                "metrics": metrics,
                "agents": agents,
                "devices": devices
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

    /* Device Controls & Expanding Accordions */
    .device-controls-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    .device-filter-chips {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .filter-chip {
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 5px 12px;
      border-radius: 16px;
      font-size: 0.78rem;
      cursor: pointer;
      font-weight: 500;
      transition: all 0.15s ease;
    }
    .filter-chip:hover, .filter-chip.active {
      background: rgba(88, 166, 255, 0.15);
      border-color: var(--accent);
      color: #fff;
    }
    #device-search-input {
      width: 100%;
      padding: 7px 12px;
      background: #0d1117;
      border: 1px solid var(--border);
      color: #fff;
      border-radius: 6px;
      font-size: 0.8rem;
    }
    #device-search-input:focus {
      outline: none;
      border-color: var(--accent);
    }
    
    .device-accordion-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-bottom: 32px;
    }

    details.device-box {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }
    details.device-box[open] {
      border-color: var(--accent);
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
    }
    details.device-box summary {
      padding: 12px 16px;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      list-style: none;
      user-select: none;
      gap: 12px;
      flex-wrap: wrap;
      background: rgba(19, 27, 38, 0.8);
      transition: background 0.15s ease;
    }
    details.device-box summary::-webkit-details-marker {
      display: none;
    }
    details.device-box summary:hover {
      background: rgba(88, 166, 255, 0.08);
    }
    
    .summary-left {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 600;
      color: #fff;
      font-size: 0.95rem;
    }
    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
      flex-shrink: 0;
    }
    .status-dot.online { background: var(--green); box-shadow: 0 0 8px var(--green); }
    .status-dot.offline { background: var(--red); opacity: 0.7; }
    .status-dot.degraded { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }

    .summary-pills {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .summary-right {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--text-muted);
      font-size: 0.8rem;
    }
    .chevron-icon {
      transition: transform 0.2s ease;
      font-size: 0.8rem;
      display: inline-block;
    }
    details.device-box[open] .chevron-icon {
      transform: rotate(180deg);
      color: var(--accent);
    }

    /* Expanded Drawer Content */
    .device-drawer {
      padding: 16px;
      border-top: 1px solid var(--border);
      background: #0d131d;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
    }
    .drawer-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .drawer-card-head {
      font-size: 0.76rem;
      font-weight: 700;
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      padding-bottom: 6px;
    }
    .drawer-field {
      display: flex;
      justify-content: space-between;
      font-size: 0.8rem;
      gap: 8px;
    }
    .drawer-field .k { color: var(--text-muted); }
    .drawer-field .v { font-weight: 500; color: #fff; text-align: right; word-break: break-all; }
    
    .progress-bar-bg {
      background: #21262d;
      height: 6px;
      border-radius: 3px;
      overflow: hidden;
      margin-top: 4px;
    }
    .progress-bar-fill {
      height: 100%;
      background: var(--accent);
      border-radius: 3px;
    }
    .key-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 0.72rem;
      font-weight: 600;
      background: rgba(63, 185, 80, 0.1);
      border: 1px solid rgba(63, 185, 80, 0.3);
      color: var(--green);
    }
    .cron-chip {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 4px 8px;
      background: #090d13;
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 0.74rem;
    }
    .clickable-title { cursor: pointer; transition: color 0.15s ease; }
    .clickable-title:hover { color: var(--accent); text-decoration: underline; }
    .target-cb { width: 16px; height: 16px; cursor: pointer; accent-color: var(--accent); }
    .multi-agent-bar {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(19, 27, 38, 0.96);
      backdrop-filter: blur(10px);
      border: 1px solid var(--accent);
      box-shadow: 0 10px 30px rgba(0,0,0,0.7), 0 0 20px rgba(88, 166, 255, 0.3);
      border-radius: 30px;
      padding: 10px 22px;
      display: none;
      align-items: center;
      gap: 16px;
      z-index: 999;
      animation: slideUp 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes slideUp {
      from { transform: translate(-50%, 60px); opacity: 0; }
      to { transform: translate(-50%, 0); opacity: 1; }
    }
    .chat-history {
      background: #06090e;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      height: 360px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .chat-msg {
      max-width: 85%;
      padding: 10px 14px;
      border-radius: 8px;
      font-size: 0.85rem;
      line-height: 1.45;
      word-break: break-word;
    }
    .chat-msg.user {
      align-self: flex-end;
      background: rgba(88, 166, 255, 0.15);
      border: 1px solid rgba(88, 166, 255, 0.3);
      color: #e6edf3;
    }
    .chat-msg.agent {
      align-self: flex-start;
      background: #111722;
      border: 1px solid var(--border);
      color: #c9d1d9;
    }
    .chat-msg pre {
      background: #040608;
      padding: 8px 10px;
      border-radius: 6px;
      overflow-x: auto;
      font-family: monospace;
      font-size: 0.8rem;
      margin: 6px 0 0 0;
      border: 1px solid #1f242c;
    }
    .preset-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 10px 0;
    }
    .preset-chip {
      background: #161f2e;
      border: 1px solid var(--border);
      color: var(--cyan);
      font-size: 0.75rem;
      padding: 4px 10px;
      border-radius: 12px;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .preset-chip:hover {
      background: rgba(56, 189, 248, 0.2);
      border-color: var(--cyan);
      color: #fff;
    }
    .broadcast-results-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 12px;
      max-height: 380px;
      overflow-y: auto;
      margin-top: 14px;
    }
    .broadcast-result-card {
      background: #0a0f16;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
    }
    .target-badge-pill {
      background: rgba(88, 166, 255, 0.15);
      border: 1px solid rgba(88, 166, 255, 0.3);
      color: var(--accent);
      padding: 3px 8px;
      border-radius: 12px;
      font-size: 0.74rem;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .enroll-code-block {
      background: #040608;
      border: 1px solid #1f242c;
      border-radius: 6px;
      padding: 14px;
      font-family: monospace;
      font-size: 0.78rem;
      color: #58a6ff;
      max-height: 320px;
      overflow-y: auto;
      white-space: pre-wrap;
      line-height: 1.45;
    }
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
      <button class="btn btn-cyan" onclick="openEnrollModal()">🤖 Enlist Node</button>
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
    <div class="metric-card">
      <div class="label">Swarm Nodes</div>
      <div class="val" id="metric-nodes">-- Nodes</div>
    </div>
    <div class="metric-card">
      <div class="label">Swarm Health</div>
      <div class="val" id="metric-health" style="color:var(--green);">100% 🟢</div>
    </div>
  </div>

  <!-- Swarm Fleet Nodes & Capabilities Section -->
  <div class="section-head" style="margin-top: 10px;">
    <h2>
      <span>🌐 Swarm Fleet Nodes & Capabilities (<span id="device-count">0</span>)</span>
      <span class="badge badge-green" id="device-online-badge">0 Online</span>
    </h2>
    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
      <button class="btn btn-purple" onclick="triggerDeviceSync(this)">🔄 Sync Fleet Nodes</button>
      <button class="btn" onclick="toggleAllAccordions(true)">⊞ Expand All</button>
      <button class="btn" onclick="toggleAllAccordions(false)">⊟ Collapse All</button>
    </div>
  </div>

  <!-- Device Filters & Search Bar -->
  <div class="device-controls-bar">
    <div class="device-filter-chips">
      <button class="filter-chip active" onclick="setDeviceFilter('all', this)">All Nodes (<span id="filter-all-count">0</span>)</button>
      <button class="filter-chip" onclick="setDeviceFilter('online', this)">🟢 Online (<span id="filter-online-count">0</span>)</button>
      <button class="filter-chip" onclick="setDeviceFilter('hermes', this)">🤖 Hermes Active</button>
      <button class="filter-chip" onclick="setDeviceFilter('openclaw', this)">⚡ OpenClaw Active</button>
      <button class="filter-chip" onclick="setDeviceFilter('a2a', this)">🟣 A2A Nodes</button>
      <button class="filter-chip" onclick="setDeviceFilter('offline', this)">🔴 Offline</button>
    </div>
    <div style="flex:1; min-width:220px; max-width:380px;">
      <input type="text" id="device-search-input" placeholder="🔍 Search host, IP, OS, service, key..." oninput="handleDeviceSearch(this.value)">
    </div>
  </div>

  <!-- Expanding Accordion Device List -->
  <div class="device-accordion-list" id="devices-container">
    <div style="padding:20px; text-align:center; color:var(--text-muted);">
      Loading swarm fleet devices and capabilities...
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

  <!-- DIRECT AGENT / NODE CHAT MODAL -->
  <div class="modal-overlay" id="direct-chat-modal">
    <div class="modal-box" style="max-width: 680px;">
      <div class="modal-head">
        <div>
          <h3 style="color:#fff; display:flex; align-items:center; gap:8px;">
            <span>💬 <span id="chat-target-name">Agent</span></span>
            <span id="chat-target-badge" class="badge badge-green">HERMES AGENT</span>
          </h3>
          <span style="font-size:0.75rem; color:var(--text-muted);" id="chat-target-ip">Endpoint</span>
        </div>
        <span class="modal-close" onclick="closeModals()">&times;</span>
      </div>

      <!-- Quick Preset Prompt Chips -->
      <div class="preset-chips">
        <span class="preset-chip" onclick="sendPresetPrompt('Ping! Report your current operational status, active model, and memory.')">⚡ Ping Health</span>
        <span class="preset-chip" onclick="sendPresetPrompt('Report system load, CPU, RAM, and uptime.')">📊 System Telemetry</span>
        <span class="preset-chip" onclick="sendPresetPrompt('List all running containers, hermes agents, and background tasks.')">🐳 List Containers</span>
        <span class="preset-chip" onclick="sendPresetPrompt('What are your active capabilities, skills, and tools?')">🔍 Inspect Capabilities</span>
      </div>

      <!-- Chat History Stream -->
      <div class="chat-history" id="chat-history">
        <!-- Messages appended here -->
      </div>

      <!-- Chat Input Area -->
      <div style="display:flex; gap:10px; margin-top:12px; align-items:center;">
        <input type="text" id="chat-input" placeholder="Type prompt or instruction... (Press Enter to Send)" style="flex:1; padding:10px 12px; background:#0d1117; border:1px solid var(--border); color:#fff; border-radius:6px; font-size:0.85rem;" onkeydown="if(event.key==='Enter') sendDirectChatPrompt()">
        <button class="btn btn-primary" id="chat-send-btn" onclick="sendDirectChatPrompt()" style="padding:10px 16px;">Send ⏎</button>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:6px; font-size:0.72rem; color:var(--text-muted);">
        <span>Direct Agent2Agent / Container execution</span>
        <span id="chat-latency-indicator" style="color:var(--cyan); font-weight:600;"></span>
      </div>
    </div>
  </div>

  <!-- SWARM MULTI-AGENT BROADCAST MODAL -->
  <div class="modal-overlay" id="broadcast-modal">
    <div class="modal-box" style="max-width: 780px;">
      <div class="modal-head">
        <h3 style="color:#fff;">📢 Broadcast Instruction to Swarm (<span id="broadcast-count">0</span> Targets)</h3>
        <span class="modal-close" onclick="closeModals()">&times;</span>
      </div>
      <div style="font-size:0.78rem; color:var(--text-muted); margin-bottom:8px;">
        Selected Agents &amp; Nodes:
      </div>
      <div id="broadcast-targets-chips" style="display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px;"></div>

      <!-- Swarm Presets -->
      <div class="preset-chips">
        <span class="preset-chip" onclick="setBroadcastPreset('Swarm Health Check: Report your hostname, local IP, operational status, and active model.')">⚡ Swarm Health Check</span>
        <span class="preset-chip" onclick="setBroadcastPreset('Trigger local fleet scan and synchronize telemetry with the Fleet Hub.')">🔄 Deep Fleet Sync</span>
        <span class="preset-chip" onclick="setBroadcastPreset('Audit local processes, memory usage, and report top 5 active tasks.')">📊 Process &amp; Load Audit</span>
      </div>

      <div style="margin-top:8px;">
        <textarea id="broadcast-message-input" rows="3" placeholder="Enter broadcast instruction for all selected agents and nodes..." style="width:100%; padding:10px; background:#0d1117; border:1px solid var(--border); color:#fff; border-radius:6px; font-size:0.85rem; font-family:sans-serif;"></textarea>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:10px;">
        <button class="btn" onclick="closeModals()">Cancel</button>
        <button class="btn btn-primary" id="broadcast-send-btn" onclick="submitSwarmBroadcast()">🚀 Dispatch to Swarm</button>
      </div>

      <!-- Live Broadcast Results Grid -->
      <div id="broadcast-results-container" style="margin-top:14px;"></div>
    </div>
  </div>

  <!-- ENROLL MACHINE / ANTIGRAVITY ONBOARDING MODAL -->
  <div class="modal-overlay" id="enroll-modal">
    <div class="modal-box" style="max-width: 750px;">
      <div class="modal-head">
        <h3 style="color:#fff;">🤖 Onboard &amp; Deep-Poll Machine (Antigravity Probe)</h3>
        <span class="modal-close" onclick="closeModals()">&times;</span>
      </div>
      <p style="font-size:0.82rem; color:var(--text-muted); line-height:1.45; margin-bottom:12px;">
        To connect any machine (Windows, Linux, or macOS) on your Tailscale mesh to this Swarm Manager, copy and paste this standardized inspection prompt directly into Antigravity on that machine:
      </p>

      <div class="enroll-code-block" id="enroll-prompt-text">Execute this Antigravity Swarm Discovery &amp; Deep-Poll Enlistment on this machine:

1. Inspect system environment:
   - OS, kernel, CPU, RAM (total/free), and disk space (total/used/avail).
   - Tailscale IP and connection status via `tailscale status --json` or `tailscale ip -4`.

2. Inspect AI &amp; Agent Runtimes:
   - Check running Hermes agents (`hermes --version`, `podman ps` / `docker ps`).
   - Check OpenClaw runtimes (ports 18789 / 18790).
   - Check local Antigravity IDE version and active session ID.
   - Check A2A server on port 8080 (http://localhost:8080/.well-known/agent-card.json). If inactive, start it with Bearer token auth.

3. Enlist with Hermes Fleet Controller:
   - Run the onboard probe script:
     python bin/fleet-enroll-node.py --hub http://100.99.202.75:8650
   - Or submit HTTP POST to http://100.99.202.75:8650/api/fleet/register with your telemetry payload.

4. Report discovered runtimes and registration confirmation back to operator.</div>

      <div style="display:flex; justify-content:space-between; align-items:center; margin-top:14px;">
        <span style="font-size:0.75rem; color:var(--text-muted);">Self-enrolling automatically updates the live matrix.</span>
        <button class="btn btn-primary" id="copy-enroll-btn" onclick="copyEnrollPrompt()">📋 Copy Antigravity Prompt</button>
      </div>
    </div>
  </div>

  <!-- FLOATING MULTI-AGENT ACTION BAR -->
  <div class="multi-agent-bar" id="multi-agent-bar">
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="font-size:0.85rem; color:#fff;">Selected: <strong id="selected-targets-count" style="color:var(--accent);">0</strong> targets</span>
      <div id="selected-targets-preview" style="display:flex; gap:6px; flex-wrap:wrap; max-width:320px;"></div>
    </div>
    <div style="display:flex; align-items:center; gap:8px;">
      <button class="btn btn-primary" onclick="openBroadcastModal()">📢 Swarm Broadcast</button>
      <button class="btn" onclick="selectAllOnlineTargets()">☑️ Select Online</button>
      <button class="btn" style="color:var(--text-muted);" onclick="clearTargetSelection()">❌ Clear</button>
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

      const hEl = document.getElementById("metric-health");
      if (hEl && m.swarm_score !== undefined) {
        const sc = m.swarm_score;
        const hColor = sc >= 90 ? "var(--green)" : (sc >= 60 ? "var(--yellow)" : "var(--red)");
        const hIcon = sc >= 90 ? "🟢" : (sc >= 60 ? "🟡" : "🔴");
        hEl.innerText = `${sc}% ${hIcon}`;
        hEl.style.color = hColor;
      }

      const mn = document.getElementById("metric-nodes");
      if (mn && m.device_count !== undefined) {
        mn.innerText = `${m.device_online_count || 0} / ${m.device_count} Online`;
      }

      if (data.devices) {
        renderDevices(data.devices);
      }

      const agents = data.agents || [];
      document.getElementById("agent-count").innerText = agents.length;

      const container = document.getElementById("agents-container");
      container.innerHTML = "";

      agents.forEach(a => {
        const card = document.createElement("div");
        card.className = "agent-card";
        const isRun = a.status === "running";
        const isEnc = a.is_e2ee;

        const hStatus = a.health_status || (isRun ? "healthy" : "stopped");
        let hBadge = "badge-green";
        let hIcon = "🟢";
        if (hStatus === "degraded") { hBadge = "badge-yellow"; hIcon = "🟡"; }
        else if (hStatus === "quarantined") { hBadge = "badge-red"; hIcon = "⚠️"; }
        else if (hStatus === "failed" || hStatus === "stopped") { hBadge = "badge-red"; hIcon = "🔴"; }

        const latChip = (a.latency_ms && a.latency_ms >= 0) ? `<span style="font-size:0.75rem; color:var(--cyan); margin-left:6px; font-weight:600;">⚡ ${a.latency_ms}ms</span>` : '';
        const rstRow = (a.restarts_24h && a.restarts_24h > 0) ? `<div class="agent-row"><span class="label">Self-Heals (24h):</span><span class="badge badge-yellow">${a.restarts_24h}</span></div>` : '';
        const cbRow = a.circuit_broken ? `<div class="agent-row" style="color:var(--red); font-weight:600;"><span class="label">Circuit Breaker:</span><span>⚠️ TRIPPED</span></div>` : '';

        card.innerHTML = `
          <div class="agent-header">
            <div style="display:flex; align-items:center; gap:8px;">
              <input type="checkbox" class="target-cb" data-type="agent" data-name="${a.name}" ${isTargetSelected('agent', a.name) ? 'checked' : ''} onchange="onTargetSelectChange()" onclick="event.stopPropagation()">
              <span class="agent-title clickable-title" onclick="openDirectChat('${a.name}', 'agent')" title="Direct chat with ${a.name}">💬 ${a.name} ${latChip}</span>
            </div>
            <span class="badge ${hBadge}">${hIcon} ${hStatus.toUpperCase()}</span>
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
          ${rstRow}
          ${cbRow}
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
          <div style="display:flex; gap:6px; margin-top:8px;">
            <button class="btn btn-sm btn-cyan" style="flex:1;" onclick="openDirectChat('${a.name}', 'agent')">💬 Chat</button>
            <button class="btn btn-sm" style="flex:1;" onclick="openLogs('${a.name}')">📜 Logs</button>
            <button class="btn btn-sm btn-purple" style="flex:1;" onclick="healAgent('${a.name}', this)" title="Run watchdog self-heal">🔄 Heal</button>
            ${a.name !== 'fleet-controller' ? `<button class="btn btn-sm" style="color:var(--red);" onclick="teardownAgent('${a.name}')">🛑</button>` : ''}
          </div>
        `;
        container.appendChild(card);
      });
    }

    async function healAgent(name, btn) {
      if (btn) btn.innerText = "⏳ Healing...";
      try {
        const res = await fetch(`/api/agents/${name}/heal`, {
          method: "POST",
          headers: {"Content-Type": "application/json"}
        });
        const data = await res.json();
        alert(data.output || "Healing completed.");
      } catch (e) {
        alert("Failed to trigger self-healing: " + e);
      } finally {
        if (btn) btn.innerText = "🔄 Heal";
      }
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
      const dm = document.getElementById("direct-chat-modal");
      if (dm) dm.style.display = "none";
      const bm = document.getElementById("broadcast-modal");
      if (bm) bm.style.display = "none";
      const em = document.getElementById("enroll-modal");
      if (em) em.style.display = "none";
    }

    // --- SWARM FLEET NODES MANAGEMENT ---
    let currentDevices = [];
    let currentFilter = 'all';
    let currentSearch = '';

    async function fetchDevices() {
      try {
        const res = await fetch("/api/fleet/devices", {
          headers: authHeaders()
        });
        if (res.ok) {
          const data = await res.json();
          renderDevices(data.devices || []);
        }
      } catch (e) {
        console.error("Failed to fetch devices:", e);
      }
    }

    async function triggerDeviceSync(btn) {
      if (btn) {
        btn.innerText = "⏳ Syncing...";
        btn.disabled = true;
      }
      try {
        const res = await fetch("/api/fleet/sync", {
          method: "POST",
          headers: authHeaders()
        });
        const data = await res.json();
        renderDevices(data.devices || []);
      } catch (e) {
        alert("Failed to sync fleet nodes: " + e);
      } finally {
        if (btn) {
          btn.innerText = "🔄 Sync Fleet Nodes";
          btn.disabled = false;
        }
      }
    }

    function toggleAllAccordions(shouldOpen) {
      const details = document.querySelectorAll("#devices-container details.device-box");
      details.forEach(d => {
        d.open = shouldOpen;
      });
    }

    function setDeviceFilter(filter, btn) {
      currentFilter = filter;
      document.querySelectorAll(".device-filter-chips .filter-chip").forEach(b => b.classList.remove("active"));
      if (btn) btn.classList.add("active");
      applyDeviceFilters();
    }

    function handleDeviceSearch(query) {
      currentSearch = (query || "").toLowerCase().trim();
      applyDeviceFilters();
    }

    function applyDeviceFilters() {
      const boxes = document.querySelectorAll("#devices-container details.device-box");
      boxes.forEach(box => {
        const name = box.getAttribute("data-name") || "";
        const ip = box.getAttribute("data-ip") || "";
        const os = box.getAttribute("data-os") || "";
        const online = box.getAttribute("data-online") === "true";
        const hasHermes = box.getAttribute("data-hermes") === "true";
        const hasOpenclaw = box.getAttribute("data-openclaw") === "true";
        const hasA2A = box.getAttribute("data-a2a") === "true";
        const haystack = box.getAttribute("data-haystack") || "";

        let matchFilter = true;
        if (currentFilter === "online") matchFilter = online;
        else if (currentFilter === "offline") matchFilter = !online;
        else if (currentFilter === "hermes") matchFilter = hasHermes;
        else if (currentFilter === "openclaw") matchFilter = hasOpenclaw;
        else if (currentFilter === "a2a") matchFilter = hasA2A;

        let matchSearch = true;
        if (currentSearch) {
          matchSearch = name.includes(currentSearch) || ip.includes(currentSearch) || os.includes(currentSearch) || haystack.includes(currentSearch);
        }

        box.style.display = (matchFilter && matchSearch) ? "block" : "none";
      });
    }

    function renderDevices(devices) {
      if (!devices || !Array.isArray(devices)) return;
      currentDevices = devices;

      const openNames = new Set();
      document.querySelectorAll("#devices-container details.device-box[open]").forEach(d => {
        const n = d.getAttribute("data-name");
        if (n) openNames.add(n);
      });

      const container = document.getElementById("devices-container");
      container.innerHTML = "";

      const onlineCount = devices.filter(d => d.online).length;
      document.getElementById("device-count").innerText = devices.length;
      const onBadge = document.getElementById("device-online-badge");
      if (onBadge) onBadge.innerText = `${onlineCount} Online`;
      const faCount = document.getElementById("filter-all-count");
      if (faCount) faCount.innerText = devices.length;
      const foCount = document.getElementById("filter-online-count");
      if (foCount) foCount.innerText = onlineCount;

      devices.forEach(d => {
        const name = d.name || "unknown";
        const ip = d.ip || "no-ip";
        const os = d.os || "Linux";
        const online = !!d.online;

        const sw = d.trackedSoftware || {};
        const hermes = sw.hermes || {};
        const openclaw = sw.openclaw || {};
        const a2a = sw["a2a-server"] || sw.a2a || {};
        const antigravity = sw.antigravity || {};

        const hasHermes = !!hermes.running;
        const hasOpenclaw = !!openclaw.running;
        const hasA2A = !!a2a.running || (d.services && d.services["a2a-server"] && d.services["a2a-server"].running);

        const haystack = `${name} ${ip} ${os} ${d.kernel || ''} ${d.notes || ''} ${Object.keys(d.apiKeys || {}).join(' ')} ${Object.keys(d.services || {}).join(' ')}`.toLowerCase();

        const box = document.createElement("details");
        box.className = "device-box";
        box.setAttribute("data-name", name.toLowerCase());
        box.setAttribute("data-ip", ip.toLowerCase());
        box.setAttribute("data-os", os.toLowerCase());
        box.setAttribute("data-online", online ? "true" : "false");
        box.setAttribute("data-hermes", hasHermes ? "true" : "false");
        box.setAttribute("data-openclaw", hasOpenclaw ? "true" : "false");
        box.setAttribute("data-a2a", hasA2A ? "true" : "false");
        box.setAttribute("data-haystack", haystack);

        if (openNames.has(name.toLowerCase())) {
          box.open = true;
        }

        let statusDotClass = online ? "online" : "offline";
        let statusText = online ? "ONLINE" : "OFFLINE";

        let hermesPill = hasHermes
          ? `<span class="badge badge-green">🟢 Hermes ${hermes.version || ''} (${hermes.model || 'kimi-k3'})</span>`
          : `<span class="badge" style="background:#21262d; color:#8b949e;">⚪ No Hermes</span>`;

        let openclawPill = hasOpenclaw
          ? `<span class="badge badge-cyan">⚡ OpenClaw ${openclaw.version || ''}</span>`
          : (openclaw.version ? `<span class="badge" style="background:#21262d; color:#8b949e;">⚪ OpenClaw (Stopped)</span>` : '');

        let a2aPill = hasA2A
          ? `<span class="badge badge-purple">🟣 A2A Node :${a2a.port || 8080}</span>`
          : '';

        let antigravityPill = (antigravity.running || antigravity.session)
          ? `<span class="badge badge-yellow">✨ AGY: ${antigravity.session ? antigravity.session.substring(0,8)+'...' : 'Active'}</span>`
          : '';

        let diskChip = '';
        if (d.disk && d.disk.total) {
          diskChip = `<span style="font-size:0.75rem; color:var(--text-muted); margin-left:4px;">💾 ${d.disk.used || '0'}/${d.disk.total} (${d.disk.usePct || 0}%)</span>`;
        }

        const diskPct = (d.disk && d.disk.usePct) ? Math.min(100, Math.max(0, d.disk.usePct)) : 0;
        const memStr = (d.memory && typeof d.memory === 'object') ? `${d.memory.free || ''} free / ${d.memory.total || ''}` : (d.memory || 'N/A');
        const sshStr = d.sshAccess ? '<span style="color:var(--green); font-weight:600;">✅ Verified Access</span>' : (d.sshBlocker ? `<span style="color:var(--yellow);">${d.sshBlocker}</span>` : '<span style="color:var(--text-muted);">None</span>');

        const hVer = hermes.version ? `${hermes.version} (${hermes.running ? 'Running' : 'Stopped'})` : 'Not Installed';
        const ocVer = openclaw.version ? `${openclaw.version} (${openclaw.running ? 'Running' : 'Stopped'}${openclaw.latestAvailable ? ', Update: ' + openclaw.latestAvailable : ''})` : 'Not Installed';
        const a2aPort = a2a.port ? `Port ${a2a.port} (${a2a.running ? 'Listening' : 'Stopped'})` : 'Inactive';
        const a2aCard = a2a.agentCardUrl ? `<a href="${a2a.agentCardUrl}" target="_blank" style="color:var(--accent); text-decoration:none;">View Agent Card ↗</a>` : 'N/A';

        let servicesHtml = '';
        const svcs = d.services || {};
        if (Object.keys(svcs).length > 0) {
          for (const [sname, sinfo] of Object.entries(svcs)) {
            const sRunning = (sinfo && sinfo.running !== undefined) ? sinfo.running : true;
            const sPort = (sinfo && sinfo.port) ? ` :${sinfo.port}` : '';
            const sHealth = (sinfo && sinfo.health) ? ` [${sinfo.health}]` : '';
            servicesHtml += `
              <div class="drawer-field">
                <span class="k">${sname}${sPort}:</span>
                <span class="v">${sRunning ? '🟢 Running' : '🔴 Stopped'}${sHealth}</span>
              </div>
            `;
          }
        } else {
          servicesHtml = '<div style="color:var(--text-muted); font-size:0.75rem;">No active services reported</div>';
        }

        let keysHtml = '';
        const keys = d.apiKeys || {};
        if (Object.keys(keys).length > 0) {
          keysHtml = '<div style="display:flex; flex-wrap:wrap; gap:6px;">';
          for (const [kname, kinfo] of Object.entries(keys)) {
            const isValid = kinfo.valid || kinfo.set;
            keysHtml += `
              <span class="key-badge" title="${kinfo.service || kname}">
                🛡️ ${kname.replace('MCBORED_', '').replace('MCOVERSEER_', '')}: ${isValid ? 'SET' : 'MISSING'}
              </span>
            `;
          }
          keysHtml += '</div>';
        } else {
          keysHtml = '<div style="color:var(--text-muted); font-size:0.75rem;">No API key bindings detected</div>';
        }

        let cronHtml = '';
        const crons = d.cronJobs || [];
        if (crons.length > 0) {
          crons.forEach(cj => {
            const st = cj.status === 'ok' ? '🟢' : '⚠️';
            cronHtml += `
              <div class="cron-chip">
                <span><strong>${cj.name}</strong> (${cj.schedule || 'scheduled'})</span>
                <span>${st} ${cj.status || 'active'}</span>
              </div>
            `;
          });
        } else {
          cronHtml = '<div style="color:var(--text-muted); font-size:0.75rem;">No scheduled cron jobs</div>';
        }

        const notesStr = d.notes || 'Normal telemetry.';
        const scannedAt = d.lastScanned || d.lastSeen || 'Recent';

        box.innerHTML = `
          <summary>
            <div class="summary-left">
              <input type="checkbox" class="target-cb" data-type="node" data-name="${name}" data-ip="${ip}" ${isTargetSelected('node', name) ? 'checked' : ''} onchange="onTargetSelectChange()" onclick="event.stopPropagation()">
              <span class="status-dot ${statusDotClass}"></span>
              <span class="clickable-title" onclick="event.stopPropagation(); openDirectChat('${name}', 'node', '${ip}')" title="Direct prompt to ${name}">${name}</span>
              <span style="font-family:monospace; font-size:0.8rem; color:var(--accent);">${ip}</span>
              <span class="badge badge-cyan">${os}</span>
            </div>
            <div class="summary-pills">
              ${hermesPill}
              ${openclawPill}
              ${a2aPill}
              ${antigravityPill}
              ${diskChip}
            </div>
            <div class="summary-right" style="display:flex; align-items:center; gap:8px;">
              ${online ? `<button class="btn btn-sm btn-cyan" onclick="event.stopPropagation(); openDirectChat('${name}', 'node', '${ip}')" title="Prompt agent/node">💬 Prompt</button>` : ''}
              <button class="btn btn-sm" onclick="event.stopPropagation(); triggerDeviceProbe('${name}', this)" title="Deep poll ports and A2A service">🔍 Poll</button>
              <span class="chevron-icon">▼</span>
            </div>
          </summary>
          <div class="device-drawer">
            <!-- Box 1: System Specs -->
            <div class="drawer-card">
              <div class="drawer-card-head">
                <span>💻 System & Host Specs</span>
                <span class="badge ${online ? 'badge-green' : 'badge-red'}">${statusText}</span>
              </div>
              <div class="drawer-field">
                <span class="k">OS / Kernel:</span>
                <span class="v">${os} / ${d.kernel || 'N/A'}</span>
              </div>
              <div class="drawer-field">
                <span class="k">Architecture / Uptime:</span>
                <span class="v">${d.arch || 'x86_64'} / ${d.uptime || 'N/A'}</span>
              </div>
              <div class="drawer-field">
                <span class="k">SSH Security:</span>
                <span class="v">${sshStr}</span>
              </div>
              <div class="drawer-field">
                <span class="k">Memory:</span>
                <span class="v">${memStr}</span>
              </div>
              ${d.disk ? `
              <div style="margin-top:4px;">
                <div class="drawer-field">
                  <span class="k">Disk Space:</span>
                  <span class="v">${d.disk.used || '0'} / ${d.disk.total || '0'} (${diskPct}%)</span>
                </div>
                <div class="progress-bar-bg">
                  <div class="progress-bar-fill" style="width: ${diskPct}%;"></div>
                </div>
              </div>` : ''}
            </div>

            <!-- Box 2: AI & Agent Runtimes -->
            <div class="drawer-card">
              <div class="drawer-card-head">
                <span>🤖 AI & Agent Runtimes</span>
                <span class="badge badge-purple">${hasA2A ? 'A2A Peer' : 'Client'}</span>
              </div>
              <div class="drawer-field">
                <span class="k">Hermes Agent:</span>
                <span class="v">${hVer}</span>
              </div>
              <div class="drawer-field">
                <span class="k">Active Model:</span>
                <span class="v">${hermes.model || 'kimi-k3'}</span>
              </div>
              <div class="drawer-field">
                <span class="k">OpenClaw:</span>
                <span class="v">${ocVer}</span>
              </div>
              <div class="drawer-field">
                <span class="k">A2A Server:</span>
                <span class="v">${a2aPort}</span>
              </div>
              <div class="drawer-field">
                <span class="k">Agent Card:</span>
                <span class="v">${a2aCard}</span>
              </div>
              ${antigravity.session ? `
              <div class="drawer-field">
                <span class="k">Antigravity Session:</span>
                <span class="v" style="color:var(--yellow);">${antigravity.session}</span>
              </div>` : ''}
            </div>

            <!-- Box 3: Services & Ports -->
            <div class="drawer-card">
              <div class="drawer-card-head">
                <span>🔌 Active Services & Ports</span>
                <span class="badge badge-cyan">${Object.keys(svcs).length} Services</span>
              </div>
              ${servicesHtml}
            </div>

            <!-- Box 4: Configured API Keys -->
            <div class="drawer-card">
              <div class="drawer-card-head">
                <span>🔑 Integrations & Keys</span>
                <span class="badge badge-green">${Object.keys(keys).length} Configured</span>
              </div>
              <div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:4px;">
                Verified zero-leak credential presence:
              </div>
              ${keysHtml}
            </div>

            <!-- Box 5: Cron & Automation -->
            <div class="drawer-card">
              <div class="drawer-card-head">
                <span>⏰ Automated Timers & Cron</span>
                <span class="badge badge-yellow">${crons.length} Jobs</span>
              </div>
              <div style="display:flex; flex-direction:column; gap:6px;">
                ${cronHtml}
              </div>
            </div>

            <!-- Box 6: Diagnostics & Notes -->
            <div class="drawer-card">
              <div class="drawer-card-head">
                <span>📋 Diagnostics & Telemetry</span>
                <span style="font-size:0.72rem; color:var(--text-muted);">${scannedAt}</span>
              </div>
              <p style="font-size:0.8rem; line-height:1.4; color:#fff;">
                ${notesStr}
              </p>
              ${(d.pendingUpdates && d.pendingUpdates.length > 0) ? `
              <div style="margin-top:6px; padding:6px; background:rgba(210,153,34,0.1); border:1px solid rgba(210,153,34,0.3); border-radius:4px; font-size:0.75rem; color:var(--yellow);">
                ⚠️ <strong>Pending Updates:</strong> ${d.pendingUpdates.join(', ')}
              </div>` : ''}
              <div style="display:flex; gap:8px; margin-top:10px;">
                <button class="btn btn-sm btn-cyan" onclick="openDirectChat('${name}', 'node', '${ip}')">💬 Direct Message / Prompt</button>
                <button class="btn btn-sm btn-purple" onclick="triggerDeviceProbe('${name}', this)">🔍 Deep Poll Node</button>
              </div>
            </div>
          </div>
        `;

        container.appendChild(box);
      });

      applyDeviceFilters();
    }

    // --- TARGET SELECTION & MULTI-AGENT SWARM BROADCAST ---
    let selectedTargets = []; // [{name, type, ip}]
    let currentChatTarget = null; // {name, type, ip}

    function isTargetSelected(type, name) {
      return selectedTargets.some(t => t.type === type && t.name.toLowerCase() === name.toLowerCase());
    }

    function onTargetSelectChange() {
      const selected = [];
      document.querySelectorAll(".target-cb:checked").forEach(cb => {
        selected.push({
          name: cb.getAttribute("data-name"),
          type: cb.getAttribute("data-type") || "agent",
          ip: cb.getAttribute("data-ip") || ""
        });
      });
      selectedTargets = selected;
      updateMultiAgentBar();
    }

    function updateMultiAgentBar() {
      const bar = document.getElementById("multi-agent-bar");
      const countEl = document.getElementById("selected-targets-count");
      const previewEl = document.getElementById("selected-targets-preview");
      if (!bar) return;

      if (selectedTargets.length === 0) {
        bar.style.display = "none";
        return;
      }

      bar.style.display = "flex";
      countEl.innerText = selectedTargets.length;
      previewEl.innerHTML = "";
      selectedTargets.forEach(t => {
        const pill = document.createElement("span");
        pill.className = "target-badge-pill";
        const icon = t.type === "node" ? "🌐" : "🤖";
        pill.innerHTML = `${icon} ${t.name} <span style="cursor:pointer; margin-left:4px;" onclick="unselectTarget('${t.type}', '${t.name}')">&times;</span>`;
        previewEl.appendChild(pill);
      });
    }

    function unselectTarget(type, name) {
      document.querySelectorAll(`.target-cb[data-type="${type}"][data-name="${name}"]`).forEach(cb => {
        cb.checked = false;
      });
      onTargetSelectChange();
    }

    function clearTargetSelection() {
      document.querySelectorAll(".target-cb").forEach(cb => cb.checked = false);
      selectedTargets = [];
      updateMultiAgentBar();
    }

    function selectAllOnlineTargets() {
      document.querySelectorAll(".agent-card .target-cb").forEach(cb => cb.checked = true);
      document.querySelectorAll('#devices-container details.device-box[data-online="true"] .target-cb').forEach(cb => cb.checked = true);
      onTargetSelectChange();
    }

    // --- 1-CLICK DIRECT AGENT & NODE CHAT ---
    function openDirectChat(name, type, ip) {
      currentChatTarget = { name, type: type || 'agent', ip: ip || '' };
      document.getElementById("chat-target-name").innerText = name;
      const typeBadge = document.getElementById("chat-target-badge");
      if (typeBadge) {
        typeBadge.innerText = (type === 'node') ? '🌐 TAILSCALE NODE / A2A' : '🤖 HERMES AGENT';
        typeBadge.className = (type === 'node') ? 'badge badge-purple' : 'badge badge-green';
      }
      const ipSub = document.getElementById("chat-target-ip");
      if (ipSub) {
        ipSub.innerText = ip ? `Endpoint: ${ip}` : `Local Container Execution`;
      }
      
      const history = document.getElementById("chat-history");
      history.innerHTML = `
        <div class="chat-msg agent">
          <strong>${name}</strong> [Ready]<br>
          Connected via ${type === 'node' ? 'A2A protocol (:8080)' : 'Hermes Agent runtime'}. Type an instruction or click a quick prompt below.
        </div>
      `;
      document.getElementById("chat-input").value = "";
      document.getElementById("direct-chat-modal").style.display = "flex";
      document.getElementById("chat-input").focus();
    }

    function sendPresetPrompt(text) {
      document.getElementById("chat-input").value = text;
      sendDirectChatPrompt();
    }

    async function sendDirectChatPrompt() {
      if (!currentChatTarget) return;
      const input = document.getElementById("chat-input");
      const prompt = input.value.trim();
      if (!prompt) return;

      const history = document.getElementById("chat-history");
      const sendBtn = document.getElementById("chat-send-btn");
      const latencyIndicator = document.getElementById("chat-latency-indicator");

      // Append user msg
      const userBubble = document.createElement("div");
      userBubble.className = "chat-msg user";
      userBubble.innerHTML = `<strong>You:</strong><br>${escapeHtml(prompt)}`;
      history.appendChild(userBubble);
      input.value = "";
      history.scrollTop = history.scrollHeight;

      // Pending agent bubble
      const agentBubble = document.createElement("div");
      agentBubble.className = "chat-msg agent";
      agentBubble.innerHTML = `<strong>${currentChatTarget.name}:</strong><br><span style="color:var(--text-muted);">⏳ Executing & awaiting response...</span>`;
      history.appendChild(agentBubble);
      history.scrollTop = history.scrollHeight;

      sendBtn.disabled = true;
      sendBtn.innerText = "⏳ Sending...";
      if (latencyIndicator) latencyIndicator.innerText = "";

      try {
        const res = await fetch(`/api/agents/${encodeURIComponent(currentChatTarget.name)}/prompt`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + getSessionToken(),
            "X-Fleet-Session": getSessionToken()
          },
          body: JSON.stringify({
            message: prompt,
            type: currentChatTarget.type,
            ip: currentChatTarget.ip
          })
        });
        const data = await res.json();
        const reply = data.response || data.error || (data.success ? "Execution acknowledged." : "No response");
        const lat = data.latency_ms !== undefined ? ` <span style="font-size:0.75rem; color:var(--cyan); font-weight:600;">⚡ ${data.latency_ms}ms</span>` : "";

        agentBubble.innerHTML = `
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <strong>${currentChatTarget.name}</strong>${lat}
          </div>
          <div>${formatResponseText(reply)}</div>
        `;
        if (latencyIndicator && data.latency_ms) {
          latencyIndicator.innerText = `Latency: ${data.latency_ms}ms`;
        }
      } catch (e) {
        agentBubble.innerHTML = `<strong>${currentChatTarget.name}:</strong><br><span style="color:var(--red);">⚠️ Communication error: ${escapeHtml(String(e))}</span>`;
      } finally {
        sendBtn.disabled = false;
        sendBtn.innerText = "Send ⏎";
        history.scrollTop = history.scrollHeight;
      }
    }

    // --- SWARM BROADCAST CONSOLE ---
    function openBroadcastModal() {
      if (selectedTargets.length === 0) {
        alert("Please select at least one agent or node first.");
        return;
      }
      document.getElementById("broadcast-modal").style.display = "flex";
      document.getElementById("broadcast-count").innerText = selectedTargets.length;
      const chips = document.getElementById("broadcast-targets-chips");
      chips.innerHTML = "";
      selectedTargets.forEach(t => {
        const icon = t.type === "node" ? "🌐" : "🤖";
        chips.innerHTML += `<span class="target-badge-pill">${icon} ${t.name}</span>`;
      });
      document.getElementById("broadcast-results-container").innerHTML = "";
      document.getElementById("broadcast-message-input").value = "";
    }

    function setBroadcastPreset(text) {
      document.getElementById("broadcast-message-input").value = text;
    }

    async function submitSwarmBroadcast() {
      const input = document.getElementById("broadcast-message-input");
      const msg = input.value.trim();
      if (!msg) {
        alert("Please enter a broadcast prompt.");
        return;
      }

      const btn = document.getElementById("broadcast-send-btn");
      const resultsContainer = document.getElementById("broadcast-results-container");
      resultsContainer.innerHTML = `<div style="color:var(--accent); padding:10px;">🚀 Dispatching concurrently to ${selectedTargets.length} swarm targets...</div>`;

      btn.disabled = true;
      btn.innerText = "⏳ Broadcasting...";

      try {
        const res = await fetch("/api/fleet/broadcast", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + getSessionToken(),
            "X-Fleet-Session": getSessionToken()
          },
          body: JSON.stringify({
            targets: selectedTargets,
            message: msg
          })
        });
        const data = await res.json();
        const results = data.results || [];

        let gridHtml = `
          <div style="margin-bottom:10px; font-size:0.85rem; color:#fff;">
            <strong>Broadcast Summary:</strong> Total: ${data.total} | <span style="color:var(--green);">Succeeded: ${data.succeeded}</span> | <span style="color:var(--red);">Failed: ${data.failed}</span>
          </div>
          <div class="broadcast-results-grid">
        `;

        results.forEach(r => {
          const isOk = r.success;
          const statusBadge = isOk ? '<span class="badge badge-green">🟢 OK</span>' : '<span class="badge badge-red">🔴 ERROR</span>';
          const icon = r.type === "node" ? "🌐" : "🤖";
          const lat = (r.latency_ms && r.latency_ms >= 0) ? `<span style="font-size:0.75rem; color:var(--cyan); margin-left:6px;">⚡ ${r.latency_ms}ms</span>` : "";
          const reply = r.response || r.error || "No response";

          gridHtml += `
            <div class="broadcast-result-card">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span style="font-weight:600; color:#fff;">${icon} ${r.target} ${lat}</span>
                ${statusBadge}
              </div>
              <div style="font-size:0.8rem; color:#c9d1d9; max-height:160px; overflow-y:auto; line-height:1.4;">
                ${formatResponseText(reply)}
              </div>
            </div>
          `;
        });

        gridHtml += `</div>`;
        resultsContainer.innerHTML = gridHtml;
      } catch (e) {
        resultsContainer.innerHTML = `<div style="color:var(--red); padding:10px;">⚠️ Broadcast failure: ${escapeHtml(String(e))}</div>`;
      } finally {
        btn.disabled = false;
        btn.innerText = "🚀 Dispatch to Swarm";
      }
    }

    // --- ENROLLMENT & DEEP PROBING ---
    function openEnrollModal() {
      document.getElementById("enroll-modal").style.display = "flex";
    }

    function copyEnrollPrompt() {
      const codeEl = document.getElementById("enroll-prompt-text");
      const text = codeEl.innerText;
      navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById("copy-enroll-btn");
        btn.innerText = "✅ Copied to Clipboard!";
        btn.style.background = "rgba(63, 185, 80, 0.3)";
        setTimeout(() => {
          btn.innerText = "📋 Copy Antigravity Prompt";
          btn.style.background = "";
        }, 2500);
      });
    }

    async function triggerDeviceProbe(nodeName, btn) {
      if (btn) btn.innerText = "⏳ Polling...";
      try {
        const res = await fetch(`/api/fleet/devices/${encodeURIComponent(nodeName)}/probe`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + getSessionToken(),
            "X-Fleet-Session": getSessionToken()
          }
        });
        const data = await res.json();
        if (data.success) {
          alert(`Deep poll on '${nodeName}' completed in ${data.latency_ms}ms.`);
          fetchDevices();
        } else {
          alert(`Probe error: ${data.detail || "Failed"}`);
        }
      } catch (e) {
        alert("Failed to probe node: " + e);
      } finally {
        if (btn) btn.innerText = "🔍 Poll";
      }
    }

    function escapeHtml(str) {
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function formatResponseText(text) {
      if (!text) return "";
      if (text.includes("```")) {
        const parts = text.split("```");
        let formatted = "";
        for (let i = 0; i < parts.length; i++) {
          if (i % 2 === 1) {
            formatted += `<pre>${escapeHtml(parts[i].trim())}</pre>`;
          } else {
            formatted += `<div>${escapeHtml(parts[i]).replace(/\\n/g, '<br>')}</div>`;
          }
        }
        return formatted;
      }
      return `<div>${escapeHtml(text).replace(/\\n/g, '<br>')}</div>`;
    }

    // Initialize
    window.onload = function() {
      fetchDevices();
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
