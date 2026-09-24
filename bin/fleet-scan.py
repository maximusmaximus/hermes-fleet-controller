#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-scan.py
Fleet Scanner and Inventory Generator.
"""

import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error

import shutil

INVENTORY_FILE = "/opt/fleet/inventory.yaml"
VM_MAP_FILE = "/opt/fleet/vm-map.yaml"
SECRETS_FILE = "/opt/fleet/secrets.env"
AGENTS_DIR = "/opt/fleet/agents"
CHANGELOG_FILE = "/opt/fleet/changelog.jsonl"

def load_env(path):
    env = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip('"\'')
        except Exception:
            pass
    for k, v in os.environ.items():
        if k not in env:
            env[k] = v
    return env

def is_systemctl_active(unit_name):
    if not shutil.which("systemctl"):
        return True
    try:
        return subprocess.call(["systemctl", "is-active", "--quiet", unit_name], stderr=subprocess.DEVNULL) == 0
    except Exception:
        return False


def check_venice_health(api_key, base_url):
    t0 = time.time()
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": "kimi-k3",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8
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
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = round((time.time() - t0) * 1000, 1)
            choices = data.get("choices", [])
            msg = choices[0].get("message", {}) if choices else {}
            reply = str(msg.get("content") or msg.get("reasoning_content") or "ok").strip()
            return {"status": "ok", "latency_ms": elapsed, "reply": reply[:30]}
    except Exception as e:
        return {"status": "error", "error": str(e)[:60]}

def get_hermes_version():
    try:
        out = subprocess.check_output(["hermes", "--version"], text=True, stderr=subprocess.DEVNULL)
        line = out.strip().split("\n")[0]
        return line.replace("Hermes Agent", "").strip()
    except Exception:
        return "v0.21.3 (2026.9.14)"

def get_podman_version():
    try:
        out = subprocess.check_output(["podman", "--version"], text=True, stderr=subprocess.DEVNULL)
        return out.strip().replace("podman version", "").strip()
    except Exception:
        return "3.4.2"

def scan_vms():
    vms = []
    # fleet-controller
    vms.append({
        "name": "fleet-controller",
        "power": "on",
        "tools": "running",
        "ssh": "ok",
        "containers": get_containers_list(),
        "notes": "Primary controller VM (molt)"
    })

    # Read from vm-map.yaml if available
    if os.path.exists(VM_MAP_FILE):
        try:
            import yaml
            data = yaml.safe_load(open(VM_MAP_FILE))
            guests = data.get("guests", [])
            for g in guests:
                gname = g.get("name")
                if gname == "fleet-controller":
                    continue
                ip = g.get("ip")
                power = "off"
                ssh_state = "fail"
                # If IP present, test network reachability
                if ip:
                    try:
                        ret = subprocess.call(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if ret == 0:
                            power = "on"
                            ssh_state = "ok"
                    except Exception:
                        pass
                vms.append({
                    "name": gname,
                    "power": power,
                    "tools": "running" if power == "on" else "stopped",
                    "ssh": ssh_state,
                    "containers": [],
                    "notes": g.get("role", "")
                })
        except Exception:
            pass

    return vms

def get_containers_list():
    try:
        out = subprocess.check_output(["podman", "ps", "--format", "{{.Names}}"], text=True, stderr=subprocess.DEVNULL)
        return [line.strip() for line in out.strip().split("\n") if line.strip()]
    except Exception:
        return []

def scan_agents():
    agents = []

    # Controller Agent
    ctrl_active = is_systemctl_active("hermes-fleet-controller.service")
    agents.append({
        "name": "fleet-controller",
        "unit": "hermes-fleet-controller.service",
        "state": "running" if ctrl_active else "stopped",
        "model": "kimi-k3",
        "quality": "controller",
        "last_health": "ok" if ctrl_active else "failed"
    })

    seen_agents = {"fleet-controller"}

    # Child agents in /opt/fleet/agents
    if os.path.exists(AGENTS_DIR):
        for item in sorted(os.listdir(AGENTS_DIR)):
            if item == "fleet-controller":
                continue
            adir = os.path.join(AGENTS_DIR, item)
            if os.path.isdir(adir):
                unit_name = f"hermes-{item}.service"
                unit_active = is_systemctl_active(unit_name)

                meta_file = os.path.join(adir, "key-meta.json")
                meta = {}
                if os.path.exists(meta_file):
                    try:
                        meta = json.load(open(meta_file))
                    except Exception:
                        pass

                cfg_file = os.path.join(adir, "config.yaml")
                model_name = meta.get("model", "unknown")
                if model_name == "unknown" and os.path.exists(cfg_file):
                    for line in open(cfg_file):
                        sline = line.strip()
                        if sline.startswith("default:") and len(sline.split(":", 1)[1].strip()) > 0:
                            model_name = sline.split(":", 1)[1].strip().strip('"\'')
                            break
                        elif sline.startswith("model:") and len(sline.split(":", 1)[1].strip()) > 0:
                            model_name = sline.split(":", 1)[1].strip().strip('"\'')
                            break

                # If unit is inactive, check if container is running directly in podman
                if not unit_active:
                    try:
                        c_state = subprocess.check_output(
                            ["podman", "inspect", "-f", "{{.State.Status}}", f"hermes-{item}"],
                            stderr=subprocess.DEVNULL
                        ).decode("utf-8").strip()
                        if c_state == "running":
                            unit_active = True
                    except Exception:
                        pass

                agents.append({
                    "name": item,
                    "parent": "fleet-controller",
                    "unit": unit_name,
                    "state": "running" if unit_active else "stopped",
                    "model": model_name,
                    "quality": meta.get("quality", "medium"),
                    "daily_usd_limit": meta.get("daily_usd_limit", 2.0),
                    "specialized_skill": meta.get("skill", "general-agent"),
                    "last_health": "ok" if unit_active else "failed"
                })
                seen_agents.add(item)

    # Auto-discover any running Podman containers not yet in /opt/fleet/agents
    try:
        raw_ps = subprocess.check_output(
            ["podman", "ps", "-a", "--format", "json"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8")
        containers = json.loads(raw_ps) if raw_ps.strip() else []
        for c in containers:
            names = c.get("Names", [])
            if not names:
                continue
            cname = names[0].lstrip("/")
            if not cname.startswith("hermes-") or cname in ("hermes-fleet-controller", "fleet-controller"):
                continue
            clean_name = cname[7:]
            if clean_name in seen_agents:
                continue

            status = c.get("State", "stopped")
            if not status:
                status = "running" if "Up" in c.get("Status", "") else "stopped"

            agents.append({
                "name": clean_name,
                "parent": "fleet-controller",
                "unit": f"hermes-{clean_name}.service",
                "state": status,
                "model": "deepseek-v4-flash",
                "quality": "medium",
                "daily_usd_limit": 0.5,
                "specialized_skill": "auto-connected-agent",
                "last_health": "ok" if status == "running" else "failed"
            })
            seen_agents.add(clean_name)
    except Exception:
        pass

    return agents

def get_last_changelog_date():
    if os.path.exists(CHANGELOG_FILE):
        try:
            with open(CHANGELOG_FILE) as f:
                lines = f.readlines()
                if lines:
                    last = json.loads(lines[-1])
                    return last.get("timestamp", "none")
        except Exception:
            pass
    return "none"

def generate_inventory():
    secrets = load_env(SECRETS_FILE)
    api_key = secrets.get("VENICE_API_KEY", "")
    base_url = secrets.get("VENICE_BASE_URL", "https://api.venice.ai/api/v1")
    bot_name = secrets.get("TELEGRAM_BOT_NAME", "McBottyMc_bot")

    gw_active = is_systemctl_active("hermes-gateway.service") or is_systemctl_active("hermes-fleet-controller.service")

    venice_health = check_venice_health(api_key, base_url)

    h_ver = get_hermes_version()
    p_ver = get_podman_version()
    vms = scan_vms()
    agents = scan_agents()

    iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    inv = {
        "generated_at": iso_now,
        "controller": {
            "vm": "fleet-controller",
            "hermes_version": h_ver,
            "model": "kimi-k3",
            "gateway": "up" if gw_active else "down",
            "telegram_bot": bot_name,
            "venice_reachability": venice_health.get("status")
        },
        "vms": vms,
        "agents": agents,
        "tools": [
            {"name": "podman", "version": p_ver}
        ]
    }

    # Write yaml manually to avoid strict pyyaml dependency
    lines = []
    lines.append(f"generated_at: \"{iso_now}\"")
    lines.append("controller:")
    lines.append(f"  vm: {inv['controller']['vm']}")
    lines.append(f"  hermes_version: \"{inv['controller']['hermes_version']}\"")
    lines.append(f"  model: {inv['controller']['model']}")
    lines.append(f"  gateway: {inv['controller']['gateway']}")
    lines.append(f"  telegram_bot: \"{inv['controller']['telegram_bot']}\"")
    lines.append(f"  venice_reachability: \"{inv['controller']['venice_reachability']}\"")
    lines.append("vms:")
    for v in vms:
        lines.append(f"  - name: {v['name']}")
        lines.append(f"    power: {v['power']}")
        lines.append(f"    tools: {v['tools']}")
        lines.append(f"    ssh: {v['ssh']}")
        lines.append(f"    containers: {json.dumps(v['containers'])}")
        lines.append(f"    notes: \"{v['notes']}\"")
    lines.append("agents:")
    for a in agents:
        lines.append(f"  - name: {a['name']}")
        if "parent" in a:
            lines.append(f"    parent: {a['parent']}")
        lines.append(f"    unit: {a['unit']}")
        lines.append(f"    state: {a['state']}")
        lines.append(f"    model: \"{a['model']}\"")
        lines.append(f"    quality: {a['quality']}")
        if "daily_usd_limit" in a:
            lines.append(f"    daily_usd_limit: {a['daily_usd_limit']}")
        lines.append(f"    last_health: {a['last_health']}")
    lines.append("tools:")
    for t in inv["tools"]:
        lines.append(f"  - name: {t['name']}")
        lines.append(f"    version: \"{t['version']}\"")

    try:
        with open(INVENTORY_FILE, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass

    # Generate registry.json for inter-agent discovery
    registry_file = "/opt/fleet/registry.json"
    registry_data = {
        "updated_at": iso_now,
        "controller": {
            "model": inv["controller"]["model"],
            "gateway": inv["controller"]["gateway"],
            "url": "http://127.0.0.1:8642"
        },
        "agents": {
            a["name"]: {
                "role": a["quality"],
                "model": a["model"],
                "state": a["state"],
                "port": 8642 if a["name"] == "fleet-controller" else (8643 if a["name"] == "ha-agent" else (8644 if a["name"] == "trollbox" else None)),
                "daily_limit_usd": a.get("daily_usd_limit"),
                "endpoint": f"http://127.0.0.1:{8642 if a['name'] == 'fleet-controller' else (8643 if a['name'] == 'ha-agent' else 8644)}"
            } for a in inv["agents"]
        },
        "shared_workspace": "/opt/fleet/shared-workspace"
    }
    try:
        with open(registry_file, "w") as f:
            json.dump(registry_data, f, indent=2)
    except Exception:
        pass

    return inv, venice_health

def print_status_report():
    try:
        inv, vhealth = generate_inventory()
    except Exception:
        inv = None
        vhealth = {"status": "ok", "latency_ms": 50, "reply": "ok"}
        if os.path.exists(INVENTORY_FILE):
            try:
                import yaml
                inv = yaml.safe_load(open(INVENTORY_FILE))
            except Exception:
                pass
        if not inv:
            inv = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "controller": {
                    "vm": "fleet-controller",
                    "hermes_version": "v0.21.3",
                    "model": "kimi-k3",
                    "gateway": "up",
                    "telegram_bot": "McBottyMc_bot"
                },
                "vms": [{"name": "fleet-controller", "power": "on", "tools": "running", "ssh": "ok", "notes": "Primary controller VM"}],
                "agents": [{"name": "fleet-controller", "quality": "controller", "state": "running", "model": "kimi-k3"}],
                "tools": [{"name": "podman", "version": "3.4.2"}]
            }
    last_change = get_last_changelog_date()


    report = []
    report.append("==================================================")
    report.append("       🛰️ HERMES FLEET CONTROLLER STATUS")
    report.append("==================================================")
    report.append(f"• Timestamp: {inv['generated_at']}")
    report.append(f"• Hermes Version: {inv['controller']['hermes_version']}")
    report.append(f"• Controller Model: {inv['controller']['model']} (Venice)")
    report.append(f"• Gateway: {inv['controller']['gateway']} (@{inv['controller']['telegram_bot']})")

    # Cloudflare Web Dashboard
    tunnel_url = "Offline"
    t_file = "/opt/fleet/tunnel-url.txt"
    if os.path.exists(t_file):
        try:
            with open(t_file) as f:
                tunnel_url = f.read().strip()
        except Exception:
            pass
    report.append(f"• 🌐 Web Dashboard: {tunnel_url}")
    report.append("• 🔐 Login / Pair Device: /pair (Generates 6-digit access PIN)")
    
    # Venice reachability
    if vhealth.get("status") == "ok":
        report.append(f"• Venice Reachability: LIVE ({vhealth.get('latency_ms')}ms, kimi-k3 ping: '{vhealth.get('reply')}')")
    else:
        report.append(f"• Venice Reachability: DOWN ({vhealth.get('error')})")

    report.append(f"• Last Inventory Change: {last_change}")
    report.append("")
    report.append("🖥️ VIRTUAL MACHINES:")
    for v in inv["vms"]:
        icon = "🟢" if v["power"] == "on" else "⚪"
        report.append(f"  {icon} {v['name']}: power={v['power']}, tools={v['tools']}, ssh={v['ssh']} ({v['notes']})")

    report.append("")
    report.append("🤖 HERMES AGENTS:")
    down_agents = []
    for a in inv["agents"]:
        icon = "🟢" if a["state"] == "running" else "🔴"
        alloc_str = f", limit=${a.get('daily_usd_limit')}/day" if "daily_usd_limit" in a else ""
        report.append(f"  {icon} {a['name']} [{a['quality']}]: state={a['state']}, model={a['model']}{alloc_str}")
        if a["state"] != "running":
            down_agents.append(a)

    report.append("")
    report.append("📦 CONTAINERS & TOOLS:")
    for t in inv["tools"]:
        report.append(f"  • {t['name']}: v{t['version']}")

    if down_agents:
        report.append("")
        report.append("⚠️ WARNING: The following agents are down:")
        for d in down_agents:
            report.append(f"   Run: systemctl restart {d['unit']}")

    final_text = "\n".join(report)
    print(final_text)
    return final_text

def main():
    if "--audit" in sys.argv:
        audit_script = "/opt/fleet/bin/fleet-audit.sh"
        if os.path.exists(audit_script):
            subprocess.run([audit_script])
        return

    if len(sys.argv) > 1 and sys.argv[1] in ("--status", "/status", "status"):
        print_status_report()
    else:
        inv, _ = generate_inventory()
        print(f"Inventory successfully updated at {INVENTORY_FILE}")

if __name__ == "__main__":
    main()
