#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-connect-agent.py
Auto-onboarding and manager connection engine for new Hermes agents.
Detects running Hermes containers or host processes, binds them to the
Fleet Manager (/opt/fleet), provisions metadata, synchronizes fleet skills,
attaches network egress monitoring, updates inventory.yaml, and notifies Telegram.
"""

import os
import sys
import json
import time
import argparse
import subprocess
import yaml

FLEET_DIR = "/opt/fleet"
AGENTS_DIR = os.path.join(FLEET_DIR, "agents")
SKILLS_DIR = os.path.join(FLEET_DIR, "skills")
SHARED_WS = os.path.join(FLEET_DIR, "shared-workspace")
CHANGELOG = os.path.join(FLEET_DIR, "changelog.jsonl")
SECRETS_FILE = os.path.join(FLEET_DIR, "secrets.env")
TUNNEL_FILE = os.path.join(FLEET_DIR, "tunnel-url.txt")

EXCLUDED_NAMES = {
    "fleet-controller",
    "hermes-fleet-controller",
    "aqara-rocketmq-bridge"
}


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


def inspect_podman_container(cname_or_id):
    """Inspects a container using podman inspect, returning parsed dict or None."""
    try:
        out = subprocess.check_output(
            ["podman", "inspect", cname_or_id],
            stderr=subprocess.DEVNULL
        ).decode("utf-8")
        data = json.loads(out)
        return data[0] if data else None
    except Exception:
        return None


def is_hermes_container(info):
    """Determines whether a podman container is a Hermes agent."""
    if not info:
        return False
    name = info.get("Name", "").lstrip("/")
    if name in EXCLUDED_NAMES:
        return False
    if name.startswith("hermes-"):
        return True

    image = info.get("Config", {}).get("Image", "")
    if "hermes-agent" in image or "hermes" in image:
        return True

    envs = info.get("Config", {}).get("Env", [])
    for e in envs:
        if e.startswith("HERMES_AGENT_NAME="):
            return True

    cmd = info.get("Config", {}).get("Cmd", []) or []
    if any("hermes" in str(arg) for arg in cmd):
        return True

    return False


def get_container_ip(info):
    if not info:
        return ""
    networks = info.get("NetworkSettings", {}).get("Networks", {})
    for net in networks.values():
        ip = net.get("IPAddress", "")
        if ip:
            return ip
    return info.get("NetworkSettings", {}).get("IPAddress", "")


def get_agent_name(container_name):
    clean = container_name.lstrip("/")
    if clean.startswith("hermes-"):
        clean = clean[7:]
    return clean


def extract_model(info, agent_dir):
    # 1. From existing config
    cfg_file = os.path.join(agent_dir, "config.yaml")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file) as f:
                c = yaml.safe_load(f) or {}
                m = c.get("model")
                if isinstance(m, dict):
                    res = m.get("default") or m.get("name")
                    if res:
                        return res
                elif isinstance(m, str) and m:
                    return m
        except Exception:
            pass

    # 2. From container env
    if info:
        envs = info.get("Config", {}).get("Env", [])
        for e in envs:
            if e.startswith("VENICE_MODEL=") or e.startswith("MODEL="):
                return e.split("=", 1)[1].strip()

    # 3. From key-meta if present
    meta_file = os.path.join(agent_dir, "key-meta.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file) as f:
                km = json.load(f)
                if km.get("model"):
                    return km["model"]
        except Exception:
            pass

    return "deepseek-v4-flash"


def connect_agent(cname_or_id, force=False, notify=True):
    """Adopts and connects an agent container to the Fleet Manager."""
    info = inspect_podman_container(cname_or_id)
    if not info:
        print(f"[-] Error: Could not inspect container '{cname_or_id}'", file=sys.stderr)
        return False

    raw_name = info.get("Name", "").lstrip("/")
    if raw_name in EXCLUDED_NAMES:
        print(f"[*] Skipping excluded system container '{raw_name}'")
        return False

    if not is_hermes_container(info) and not force:
        print(f"[-] Container '{raw_name}' is not recognized as a Hermes agent. Use --force to adopt anyway.")
        return False

    agent_name = get_agent_name(raw_name)
    agent_dir = os.path.join(AGENTS_DIR, agent_name)
    is_new = not os.path.exists(agent_dir)
    os.makedirs(agent_dir, exist_ok=True)

    c_id = info.get("Id", "")[:12]
    c_status = info.get("State", {}).get("Status", "unknown")
    c_ip = get_container_ip(info)

    print(f"[*] Connecting agent '{agent_name}' (container: {raw_name}, ID: {c_id}, IP: {c_ip})...")

    # 1. Initialize or inspect config.yaml
    cfg_file = os.path.join(agent_dir, "config.yaml")
    if not os.path.exists(cfg_file):
        # Try extracting from container if /opt/data/config.yaml exists
        copied = False
        try:
            subprocess.run(
                ["podman", "cp", f"{raw_name}:/opt/data/config.yaml", cfg_file],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            copied = True
        except Exception:
            pass

        if not copied:
            # Generate default fleet-connected config
            default_cfg = {
                "_config_version": 12,
                "model": {
                    "default": "deepseek-v4-flash",
                    "provider": "custom",
                    "base_url": "https://api.venice.ai/api/v1",
                    "api_key": "${VENICE_API_KEY}"
                },
                "quick_commands": {
                    "pair": {"type": "exec", "command": "python3 /opt/fleet/bin/fleet-pair.py --tg"},
                    "report": {"type": "exec", "command": "python3 /opt/fleet/bin/fleet-report.py --stdout"},
                    "fleet": {"type": "exec", "command": "python3 /opt/fleet/bin/fleet-scan.py --status"},
                    "status": {"type": "exec", "command": "python3 /opt/fleet/bin/fleet-scan.py --status"},
                    "privacy": {"type": "exec", "command": "python3 /opt/fleet/bin/venice-resolve-model.py privacy-summary"},
                    "backup": {"type": "exec", "command": "/opt/fleet/bin/fleet-backup.sh"}
                },
                "skills": {
                    "external_dirs": ["/opt/fleet/skills"]
                }
            }
            with open(cfg_file, "w") as f:
                yaml.dump(default_cfg, f, default_flow_style=False)
            os.chmod(cfg_file, 0o644)

    # 2. Extract model & generate key-meta.json
    model = extract_model(info, agent_dir)
    meta_file = os.path.join(agent_dir, "key-meta.json")
    if not os.path.exists(meta_file):
        meta_data = {
            "name": agent_name,
            "container": raw_name,
            "container_id": c_id,
            "quality": "medium",
            "model": model,
            "daily_usd_limit": 0.50,
            "auto_connected": True,
            "connected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        with open(meta_file, "w") as f:
            json.dump(meta_data, f, indent=2)

    # 3. Initialize firewall state
    fw_file = os.path.join(agent_dir, "firewall.state")
    if not os.path.exists(fw_file):
        with open(fw_file, "w") as f:
            f.write("full\n")

    # 4. Synchronize Fleet Skills into agent skills directory
    agent_skills_dir = os.path.join(agent_dir, "skills")
    os.makedirs(agent_skills_dir, exist_ok=True)
    if os.path.exists(SKILLS_DIR):
        for s in ("pair", "report", "fleet", "privacy", "backup", "pull"):
            src_s = os.path.join(SKILLS_DIR, s)
            dst_s = os.path.join(agent_skills_dir, s)
            if os.path.exists(src_s) and not os.path.exists(dst_s):
                try:
                    subprocess.run(["cp", "-r", src_s, dst_s], check=True)
                except Exception:
                    pass

    # In-container skill injection if running
    if c_status == "running":
        try:
            subprocess.run(
                ["podman", "exec", raw_name, "mkdir", "-p", "/opt/data/skills"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            subprocess.run(
                ["podman", "cp", f"{SKILLS_DIR}/.", f"{raw_name}:/opt/data/skills/"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    # 5. Coordinate SOUL.md with swarm orders
    soul_file = os.path.join(agent_dir, "SOUL.md")
    orders_header = "## Hermes Swarm Fleet Coordination Orders"
    if os.path.exists(soul_file):
        with open(soul_file) as f:
            content = f.read()
        if orders_header not in content:
            orders = (
                f"\n\n{orders_header}\n"
                "- You are a managed worker node in the Hermes Swarm.\n"
                "- Primary Controller: fleet-controller (http://10.88.0.1:8642)\n"
                "- Real-Time Web Dashboard: " + get_tunnel_url() + "\n"
                "- Shared Workspace: /opt/fleet/shared-workspace\n"
                "- Coordinate swarm workloads and honor your allocated daily budget.\n"
            )
            with open(soul_file, "a") as f:
                f.write(orders)
    else:
        with open(soul_file, "w") as f:
            f.write(
                f"# Child Hermes Agent: {agent_name}\n\n"
                f"{orders_header}\n"
                "- You are a managed worker node in the Hermes Swarm.\n"
                "- Primary Controller: fleet-controller (http://10.88.0.1:8642)\n"
                "- Real-Time Web Dashboard: " + get_tunnel_url() + "\n"
                "- Shared Workspace: /opt/fleet/shared-workspace\n"
                "- Coordinate swarm workloads and honor your allocated daily budget.\n"
            )

    # Set correct ownership for Hermes container user (UID 10000)
    try:
        subprocess.run(["chown", "-R", "10000:10000", agent_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # 6. Apply Network Firewall Tracking
    fw_script = os.path.join(FLEET_DIR, "bin", "fleet-firewall.sh")
    if os.path.exists(fw_script) and c_ip:
        try:
            subprocess.run([fw_script, agent_name, "full"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # 7. Record in Changelog
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {
        "timestamp": ts,
        "event": "agent_auto_connected",
        "agent": agent_name,
        "container": raw_name,
        "container_id": c_id,
        "model": model,
        "ip": c_ip,
        "is_new": is_new
    }
    with open(CHANGELOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # 8. Refresh Inventory
    scan_script = os.path.join(FLEET_DIR, "bin", "fleet-scan.py")
    if os.path.exists(scan_script):
        try:
            subprocess.run(["python3", scan_script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # 9. Send Telegram Notification if new or requested
    if notify:
        tunnel_url = get_tunnel_url()
        notify_script = os.path.join(FLEET_DIR, "bin", "fleet-telegram-notify.sh")
        if os.path.exists(notify_script):
            msg = (
                f"🤖 *Hermes Agent Auto-Connected to Manager*\n\n"
                f"• *Agent Name*: `{agent_name}`\n"
                f"• *Container*: `{raw_name}` (`{c_id}`)\n"
                f"• *Model*: `{model}`\n"
                f"• *Container IP*: `{c_ip or 'dynamic'}`\n"
                f"• *Status*: `{c_status}`\n"
                f"• *Fleet Skills*: Linked\n"
                f"• *Shared Workspace*: Connected\n\n"
                f"🌐 *Web Dashboard*: {tunnel_url}"
            )
            try:
                subprocess.run([notify_script, msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    print(f"[✓] Agent '{agent_name}' successfully connected to Fleet Manager.")
    return True


def scan_and_connect_all():
    """Scans all running Podman containers and connects any unmanaged Hermes agents."""
    try:
        raw_ps = subprocess.check_output(
            ["podman", "ps", "-a", "--format", "json"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8")
        containers = json.loads(raw_ps) if raw_ps.strip() else []
    except Exception as e:
        print(f"[-] Error querying podman: {e}", file=sys.stderr)
        return 0

    connected_count = 0
    for c in containers:
        names = c.get("Names", [])
        if not names:
            continue
        cname = names[0]
        if cname in EXCLUDED_NAMES or cname.lstrip("/") in EXCLUDED_NAMES:
            continue

        agent_name = get_agent_name(cname)
        agent_dir = os.path.join(AGENTS_DIR, agent_name)

        # Check if already connected with all essential files
        is_already_connected = (
            os.path.exists(agent_dir) and
            os.path.exists(os.path.join(agent_dir, "config.yaml")) and
            os.path.exists(os.path.join(agent_dir, "firewall.state")) and
            os.path.exists(os.path.join(agent_dir, "key-meta.json"))
        )

        if not is_already_connected:
            info = inspect_podman_container(cname)
            if is_hermes_container(info):
                if connect_agent(cname, notify=True):
                    connected_count += 1

    return connected_count


def main():
    parser = argparse.ArgumentParser(description="Connect Hermes agent(s) to Fleet Manager")
    parser.add_argument("agent", nargs="?", help="Container name, ID, or agent name to connect")
    parser.add_argument("--scan", action="store_true", help="Scan and auto-connect all unmanaged Hermes agents")
    parser.add_argument("--force", action="store_true", help="Force connection even if image tag is unconventional")
    parser.add_argument("--no-notify", action="store_true", help="Suppress Telegram notification")

    args = parser.parse_args()

    if args.scan or not args.agent:
        count = scan_and_connect_all()
        print(f"[✓] Scan complete. Auto-connected {count} agent(s).")
        sys.exit(0)

    ok = connect_agent(args.agent, force=args.force, notify=not args.no_notify)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
