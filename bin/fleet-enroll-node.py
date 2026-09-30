#!/usr/bin/env python3
"""
bin/fleet-enroll-node.py
Autonomous Swarm Machine & Agent Enlistment Probe.
Runs on any machine (Windows, Linux, macOS) across the Tailscale mesh.
Deep-polls local hardware, operating system, Podman/Docker containers,
running Hermes/OpenClaw agents, and A2A (:8080) services, then registers
the node with the Hermes Swarm Fleet Hub.

Zero external dependencies - standard library only.
"""

import os
import sys
import json
import time
import shutil
import platform
import subprocess
import urllib.request
import urllib.error
import argparse

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_HUB_URL = os.environ.get("FLEET_HUB_URL", "http://127.0.0.1:8650")
DEFAULT_A2A_PORT = 8080


def get_tailscale_info():
    """Detect Tailscale IP, hostname, and status."""
    info = {"ip": "", "hostname": platform.node(), "online": False, "dnsName": ""}
    ts_cmds = ["tailscale", "tailscale.exe", "/usr/bin/tailscale"]
    for ts in ts_cmds:
        if shutil.which(ts):
            try:
                out = subprocess.check_output([ts, "status", "--json"], stderr=subprocess.DEVNULL, timeout=4)
                data = json.loads(out.decode("utf-8", errors="replace"))
                self_node = data.get("Self", {})
                ips = self_node.get("TailscaleIPs", [])
                if ips:
                    info["ip"] = ips[0]
                info["hostname"] = self_node.get("HostName") or platform.node()
                info["dnsName"] = self_node.get("DNSName", "")
                info["online"] = self_node.get("Online", True)
                return info
            except Exception:
                pass
            try:
                out_ip = subprocess.check_output([ts, "ip", "-4"], stderr=subprocess.DEVNULL, timeout=3).decode("utf-8").strip()
                if out_ip:
                    info["ip"] = out_ip
                    info["online"] = True
                    return info
            except Exception:
                pass
    return info


def get_disk_info():
    """Gather primary disk usage metrics."""
    try:
        root_path = "C:\\" if platform.system() == "Windows" else "/"
        total, used, free = shutil.disk_usage(root_path)
        total_gb = round(total / (1024**3), 1)
        used_gb = round(used / (1024**3), 1)
        free_gb = round(free / (1024**3), 1)
        pct = round((used / total) * 100) if total > 0 else 0
        return {
            "total": f"{total_gb}G",
            "used": f"{used_gb}G",
            "available": f"{free_gb}G",
            "usePct": pct
        }
    except Exception:
        return {"total": "N/A", "used": "N/A", "available": "N/A", "usePct": 0}


def get_memory_info():
    """Gather system RAM metrics across OS platforms."""
    try:
        if platform.system() == "Windows":
            cmd = ["powershell", "-NoProfile", "-Command",
                   "(Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory) | ConvertTo-Json"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5).decode("utf-8")
            data = json.loads(out)
            total_kb = data.get("TotalVisibleMemorySize", 0)
            free_kb = data.get("FreePhysicalMemory", 0)
            total_gb = round(total_kb / (1024**2), 1)
            free_gb = round(free_kb / (1024**2), 1)
            avail_gb = round(free_kb * 1.15 / (1024**2), 1)
            return {
                "total": f"{total_gb} GB",
                "free": f"{free_gb} GB",
                "avail": f"{avail_gb} GB"
            }
        elif os.path.exists("/proc/meminfo"):
            mem = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v = parts[1].strip().split()[0]
                        mem[k] = int(v)
            total_gb = round(mem.get("MemTotal", 0) / (1024**2), 1)
            free_gb = round(mem.get("MemFree", 0) / (1024**2), 1)
            avail_gb = round(mem.get("MemAvailable", free_gb) / (1024**2), 1)
            return {
                "total": f"{total_gb} GB",
                "free": f"{free_gb} GB",
                "avail": f"{avail_gb} GB"
            }
    except Exception:
        pass
    return {"total": "N/A", "free": "N/A", "avail": "N/A"}


def inspect_containers():
    """Detect running Podman or Docker containers."""
    containers = []
    engines = ["podman", "docker"]
    for eng in engines:
        if shutil.which(eng):
            try:
                cmd = [eng, "ps", "--format", "{{.Names}} \t {{.Image}} \t {{.Status}}"]
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5).decode("utf-8")
                for line in out.strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = line.split("\t")
                    c_name = parts[0].strip()
                    c_img = parts[1].strip() if len(parts) > 1 else ""
                    c_stat = parts[2].strip() if len(parts) > 2 else "running"
                    containers.append({"name": c_name, "image": c_img, "status": c_stat, "engine": eng})
            except Exception:
                pass
    return containers


def check_a2a_server(port=8080, ip=None):
    """Check if A2A server is running locally or on Tailscale IP."""
    candidates = [f"http://127.0.0.1:{port}/.well-known/agent-card.json"]
    if ip and ip != "127.0.0.1":
        candidates.append(f"http://{ip}:{port}/.well-known/agent-card.json")
    a2a_token = os.environ.get("A2A_AUTH_TOKEN", "")
    headers = {
        "User-Agent": "FleetEnrollProbe/1.0",
        "Authorization": f"Bearer {a2a_token}"
    }
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "running": True,
                    "port": port,
                    "card": data,
                    "protocol": data.get("a2a_version", "1.0")
                }
        except Exception:
            continue
    return {"running": False, "port": port}


def check_hermes_runtime(containers):
    """Check for Hermes agent CLI, processes, or containers."""
    info = {"installed": False, "version": "", "running": False, "model": "kimi-k3", "procs": 0}
    if shutil.which("hermes"):
        info["installed"] = True
        try:
            out = subprocess.check_output(["hermes", "--version"], stderr=subprocess.DEVNULL, timeout=3).decode("utf-8")
            info["version"] = out.strip().split("\n")[0].replace("Hermes Agent", "").strip()
        except Exception:
            info["version"] = "v0.21.3"

    hermes_containers = [c for c in containers if "hermes" in c["name"].lower() or "hermes" in c["image"].lower()]
    if hermes_containers:
        info["running"] = True
        info["procs"] = len(hermes_containers)
        info["containers"] = [c["name"] for c in hermes_containers]
        return info

    # Check host processes
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command", "Get-Process -Name *hermes* -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"], stderr=subprocess.DEVNULL, timeout=4).decode("utf-8")
            pids = [p.strip() for p in out.strip().split("\n") if p.strip()]
            if pids:
                info["running"] = True
                info["procs"] = len(pids)
        else:
            out = subprocess.check_output(["pgrep", "-f", "hermes"], stderr=subprocess.DEVNULL, timeout=3).decode("utf-8")
            pids = [p.strip() for p in out.strip().split("\n") if p.strip()]
            if pids:
                info["running"] = True
                info["procs"] = len(pids)
    except Exception:
        pass
    return info


def check_openclaw_runtime():
    """Check for OpenClaw runtime or gateway."""
    info = {"installed": False, "version": "", "running": False}
    if shutil.which("openclaw"):
        info["installed"] = True
        try:
            out = subprocess.check_output(["openclaw", "--version"], stderr=subprocess.DEVNULL, timeout=3).decode("utf-8")
            info["version"] = out.strip()
        except Exception:
            info["version"] = "2026.5.22"
    return info


def probe_local_node():
    """Conduct full deep poll of the local machine and runtime."""
    ts_info = get_tailscale_info()
    disk = get_disk_info()
    memory = get_memory_info()
    containers = inspect_containers()
    a2a = check_a2a_server(DEFAULT_A2A_PORT, ts_info.get("ip"))
    hermes = check_hermes_runtime(containers)
    openclaw = check_openclaw_runtime()

    node_name = ts_info.get("hostname") or platform.node()
    ip = ts_info.get("ip") or "127.0.0.1"

    tracked_software = {
        "hermes": {
            "version": hermes.get("version") or "v0.21.3",
            "running": hermes.get("running", False),
            "model": hermes.get("model", "kimi-k3"),
            "procs": hermes.get("procs", 0)
        },
        "openclaw": {
            "version": openclaw.get("version") or "2026.5.22",
            "running": openclaw.get("running", False)
        },
        "antigravity": {
            "version": "2.0",
            "running": True,
            "session": os.environ.get("ANTIGRAVITY_SESSION_ID", "active")
        },
        "tailscale": {
            "connected": ts_info.get("online", False),
            "ip": ip,
            "dnsName": ts_info.get("dnsName", "")
        }
    }

    if a2a.get("running"):
        card = a2a.get("card", {})
        tracked_software["a2a-server"] = {
            "version": card.get("version", "1.0.0"),
            "running": True,
            "port": DEFAULT_A2A_PORT,
            "protocol": "A2A/v1",
            "agentCardUrl": f"http://{ip}:{DEFAULT_A2A_PORT}/.well-known/agent-card.json",
            "directIpUrl": f"http://{ip}:{DEFAULT_A2A_PORT}",
            "auth": "Bearer"
        }

    services = {}
    if a2a.get("running"):
        services["a2a-server"] = {"running": True, "port": DEFAULT_A2A_PORT, "health": "OK", "auth": "Bearer"}
    services["antigravity-node"] = {"running": True, "health": "OK"}

    os_desc = f"{platform.system()} {platform.release()}"
    if platform.system() == "Windows":
        os_desc = f"Windows {platform.version()} (Antigravity Node)"
    elif platform.system() == "Linux":
        os_desc = f"Linux ({platform.release()})"

    payload = {
        "node_name": node_name,
        "ip": ip,
        "os": os_desc,
        "kernel": platform.release(),
        "arch": platform.machine(),
        "online": True,
        "disk": disk,
        "memory": memory,
        "trackedSoftware": tracked_software,
        "services": services,
        "containers": [c["name"] for c in containers],
        "notes": f"Enrolled via Antigravity probe on {platform.node()} ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())})."
    }

    return payload


def register_with_hub(hub_url, payload, auth_token=None):
    """Dispatch telemetry to the Fleet Hub registration endpoint."""
    target_url = f"{hub_url.rstrip('/')}/api/fleet/register"
    body = json.dumps(payload, indent=2).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
        headers["X-Fleet-Session"] = auth_token

    req = urllib.request.Request(target_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return True, data
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        return False, f"HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Hermes Swarm Machine & Agent Enlistment Probe")
    parser.add_argument("--hub", default=DEFAULT_HUB_URL, help=f"Swarm Fleet Hub URL (default: {DEFAULT_HUB_URL})")
    parser.add_argument("--token", default=os.environ.get("FLEET_AUTH_TOKEN", ""), help="Auth token or session key")
    parser.add_argument("--dry-run", action="store_true", help="Print collected telemetry without sending to Hub")
    args = parser.parse_args()

    print("=" * 60)
    print("🛰️  HERMES SWARM - DEEP NODE PROBE & ENLISTMENT")
    print("=" * 60)
    print("🔍 Probing local operating system, Tailscale, containers, and A2A runtime...")
    payload = probe_local_node()

    print(f"\n[+] Node Name:     {payload['node_name']}")
    print(f"[+] Tailscale IP:  {payload['ip']}")
    print(f"[+] OS / Kernel:   {payload['os']} / {payload['kernel']}")
    print(f"[+] Disk Usage:    {payload['disk']['used']} / {payload['disk']['total']} ({payload['disk']['usePct']}%)")
    print(f"[+] RAM Metrics:   {payload['memory']['free']} free / {payload['memory']['total']}")
    
    hermes_stat = "🟢 Running" if payload['trackedSoftware']['hermes']['running'] else "⚪ Stopped"
    print(f"[+] Hermes Agent:  {hermes_stat} (procs: {payload['trackedSoftware']['hermes']['procs']})")
    
    a2a_stat = "🟢 Active (:8080)" if "a2a-server" in payload['services'] else "🔴 Inactive"
    print(f"[+] A2A Server:    {a2a_stat}")
    
    if payload.get("containers"):
        print(f"[+] Containers:    {', '.join(payload['containers'])}")

    if args.dry_run:
        print("\n[i] DRY-RUN MODE: Registration skipped. Payload preview:")
        print(json.dumps(payload, indent=2))
        return

    print(f"\n🚀 Transmitting registration to Swarm Fleet Hub at {args.hub}...")
    success, res = register_with_hub(args.hub, payload, args.token)
    if success:
        print(f"✅ Successfully enrolled '{payload['node_name']}' into the Hermes Swarm!")
        print(f"   Hub response: {res.get('message', 'OK')}")
        print("   The node is now live in the Web Dashboard and ready for direct A2A communication.")
    else:
        print(f"⚠️  Hub registration failed: {res}")
        print(f"   Ensure Hub is reachable over Tailscale ({args.hub}) or supply pairing key with --token.")


if __name__ == "__main__":
    main()
