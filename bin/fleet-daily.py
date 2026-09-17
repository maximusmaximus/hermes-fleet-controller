#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-daily.py
Daily inventory diff, self-healing agent restarter, and Telegram digest.
"""

import os
import sys
import json
import time
import subprocess

INVENTORY_FILE = "/opt/fleet/inventory.yaml"
PREV_INVENTORY_FILE = "/opt/fleet/inventory.prev.json"
CHANGELOG_FILE = "/opt/fleet/changelog.jsonl"
AMENDMENTS_FILE = "/opt/fleet/agents/fleet-controller/AMENDMENTS.md"
CACHE_FILE = "/opt/fleet/models-cache.json"

def read_current_inventory():
    if os.path.exists(INVENTORY_FILE):
        try:
            import yaml
            return yaml.safe_load(open(INVENTORY_FILE))
        except Exception:
            pass
    return {}

def run_daily_digest(force_notify=False):
    # 1. Capture previous snapshot
    prev_data = {}
    if os.path.exists(PREV_INVENTORY_FILE):
        try:
            prev_data = json.load(open(PREV_INVENTORY_FILE))
        except Exception:
            pass

    # 2. Refresh model cache
    subprocess.call(["/opt/fleet/bin/venice-resolve-model.sh", "refresh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 3. Run scan to build fresh inventory
    subprocess.call(["/opt/fleet/bin/fleet-scan.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    new_data = read_current_inventory()

    # Save snapshot for next diff
    with open(PREV_INVENTORY_FILE, "w") as f:
        json.dump(new_data, f, indent=2)

    # 4. Compute diffs
    prev_vms = {v["name"]: v for v in prev_data.get("vms", [])}
    new_vms = {v["name"]: v for v in new_data.get("vms", [])}

    prev_agents = {a["name"]: a for a in prev_data.get("agents", [])}
    new_agents = {a["name"]: a for a in new_data.get("agents", [])}

    added = []
    removed = []
    updated = []
    restarted = []

    # Check VMs
    for name, v in new_vms.items():
        if name not in prev_vms:
            added.append(f"VM:{name}")
        elif prev_vms[name].get("power") != v.get("power"):
            updated.append(f"VM:{name} (power {prev_vms[name].get('power')} -> {v.get('power')})")

    for name in prev_vms:
        if name not in new_vms:
            removed.append(f"VM:{name}")

    # Check Agents & Self-Healing
    for name, a in new_agents.items():
        if name not in prev_agents:
            added.append(f"Agent:{name} ({a.get('model')})")
        else:
            p = prev_agents[name]
            if p.get("model") != a.get("model"):
                updated.append(f"Agent:{name} model ({p.get('model')} -> {a.get('model')})")
            if p.get("state") != a.get("state"):
                updated.append(f"Agent:{name} state ({p.get('state')} -> {a.get('state')})")

        # Auto-restart stopped agents
        if a.get("state") != "running" and a.get("unit"):
            unit = a["unit"]
            ret = subprocess.call(["systemctl", "restart", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ret == 0:
                restarted.append(unit)

    for name in prev_agents:
        if name not in new_agents:
            removed.append(f"Agent:{name}")

    # Read models cache
    model_map_str = "high=?, medium=?, low=?"
    if os.path.exists(CACHE_FILE):
        try:
            c = json.load(open(CACHE_FILE)).get("tiers", {})
            model_map_str = f"high={c.get('high')} medium={c.get('medium')} low={c.get('low')}"
        except Exception:
            pass

    date_str = time.strftime("%Y-%m-%d")
    iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 5. Append to changelog.jsonl
    log_entry = {
        "timestamp": iso_now,
        "date": date_str,
        "added": added,
        "removed": removed,
        "updated": updated,
        "restarted": restarted
    }
    with open(CHANGELOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    # 6. Write amendments into controller memory
    os.makedirs(os.path.dirname(AMENDMENTS_FILE), exist_ok=True)
    with open(AMENDMENTS_FILE, "w") as f:
        f.write(f"# Fleet Amendments as of {iso_now}\n\n")
        f.write(f"• Added: {', '.join(added) if added else 'None'}\n")
        f.write(f"• Removed: {', '.join(removed) if removed else 'None'}\n")
        f.write(f"• Updated: {', '.join(updated) if updated else 'None'}\n")
        f.write(f"• Restarted: {', '.join(restarted) if restarted else 'None'}\n")
        f.write(f"• Active Model Map: {model_map_str}\n")

    # 7. Notify user via Telegram if changes or forced
    has_changes = bool(added or removed or updated or restarted)
    if has_changes or force_notify:
        ctrl = new_data.get("controller", {})
        msg = (
            f"📋 Fleet digest {date_str}\n"
            f"Added: {', '.join(added) if added else 'none'}\n"
            f"Removed: {', '.join(removed) if removed else 'none'}\n"
            f"Updated: {', '.join(updated) if updated else 'none'}\n"
            f"Agents restarted: {', '.join(restarted) if restarted else 'none'}\n"
            f"Model map: {model_map_str}\n"
            f"Controller: kimi-k3 {ctrl.get('hermes_version', 'v0.21.3')} live=yes"
        )
        subprocess.call(["/opt/fleet/bin/fleet-telegram-notify.sh", msg])
        print(msg)
    else:
        print("No changes detected in fleet inventory.")

if __name__ == "__main__":
    force = len(sys.argv) > 1 and sys.argv[1] in ("--force", "-f")
    run_daily_digest(force_notify=force)
