#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-watchdog.py
Autonomous Health Watchdog, Self-Healing Sentinel & Reporting Engine for Hermes Fleet.

Features:
- Multi-tier deep probing: Systemd active state, Podman runtime status, and Gateway HTTP loopback latency.
- Anti-flapping circuit breaker: Halts auto-restarts after 3 failures in 15 minutes to prevent restart loops.
- Clean self-healing recovery: Safely stops and recreates failed agent containers and restarts systemd services.
- Persistent state ledger: Writes live metrics to /opt/fleet/run/agent_health_state.json for Dashboard telemetry.
- Scheduled and event-driven Telegram reports: Dispatches periodic health digests and immediate recovery alerts.
"""

import os
import sys
import time
import json
import socket
import argparse
import subprocess
from datetime import datetime

FLEET_DIR = "/opt/fleet"
AGENTS_DIR = os.path.join(FLEET_DIR, "agents")
RUN_DIR = os.path.join(FLEET_DIR, "run")
STATE_FILE = os.path.join(RUN_DIR, "agent_health_state.json")
NOTIFY_SCRIPT = os.path.join(FLEET_DIR, "bin", "fleet-telegram-notify.sh")

MAX_RESTARTS_PER_WINDOW = 3
FLAP_WINDOW_SECONDS = 900  # 15 minutes


def podman_cmd(*args):
    if os.geteuid() == 0:
        return ["podman", *args]
    return ["sudo", "podman", *args]


def systemctl_cmd(*args):
    if os.geteuid() == 0:
        return ["systemctl", *args]
    return ["sudo", "systemctl", *args]


def ensure_run_dir():
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
    except Exception:
        pass


def load_state():
    ensure_run_dir()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "timestamp": 0,
        "swarm_score_pct": 100.0,
        "total_agents": 0,
        "healthy_agents": 0,
        "agents": {},
        "history": {}
    }


def save_state(state):
    ensure_run_dir()
    try:
        tmp_file = f"{STATE_FILE}.tmp.{os.getpid()}"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_file, STATE_FILE)
    except Exception as e:
        print(f"[-] Warning: Failed to persist health state: {e}", file=sys.stderr)


def get_system_metrics():
    """Captures CPU load, memory headroom, and root disk availability."""
    load1, load5, load15 = os.getloadavg()
    
    # Disk
    st = os.statvfs("/")
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
    used_pct = round(((total_gb - free_gb) / total_gb) * 100, 1)

    # RAM
    mem_total_mb = 0
    mem_avail_mb = 0
    if os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_mb = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_avail_mb = int(line.split()[1]) // 1024

    mem_used_pct = round(((mem_total_mb - mem_avail_mb) / mem_total_mb) * 100, 1) if mem_total_mb else 0.0

    # Time Synchronization (VBoxService / VirtualBox Host Sync)
    time_synced = True
    try:
        ts_check = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized"],
            capture_output=True, text=True, timeout=2
        )
        if "NTPSynchronized=no" in ts_check.stdout:
            time_synced = False
            # Self-heal time sync: ensure virtualbox-guest-utils is active
            subprocess.run(["systemctl", "restart", "virtualbox-guest-utils.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    return {
        "load": f"{load1:.2f}, {load5:.2f}, {load15:.2f}",
        "disk_free_gb": round(free_gb, 1),
        "disk_used_pct": used_pct,
        "mem_total_mb": mem_total_mb,
        "mem_avail_mb": mem_avail_mb,
        "mem_used_pct": mem_used_pct,
        "time_synced": time_synced
    }


def discover_agents():
    """Discovers all active and registered Hermes agents."""
    agents = set()
    if os.path.exists(AGENTS_DIR):
        for item in os.listdir(AGENTS_DIR):
            if os.path.isdir(os.path.join(AGENTS_DIR, item)):
                agents.add(item)
    
    # Inspect running podman containers
    try:
        out = subprocess.check_output(
            podman_cmd("ps", "-a", "--format", "{{.Names}}"),
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            cname = line.strip()
            if cname.startswith("hermes-"):
                clean = cname[len("hermes-"):]
                agents.add(clean)
    except Exception:
        pass

    if not agents:
        agents.add("fleet-controller")

    return sorted(list(agents))


def probe_agent(name):
    """
    Executes multi-tier probing for a single agent:
    Tier 1: Systemd unit status
    Tier 2: Podman container state & OOM check
    Tier 3: Gateway HTTP loopback probe (aiohttp server response in <3s)
    """
    unit_name = "hermes-fleet-controller.service" if name == "fleet-controller" else f"hermes-{name}.service"
    cname = "hermes-fleet-controller" if name == "fleet-controller" else f"hermes-{name}"
    
    result = {
        "name": name,
        "unit": unit_name,
        "container": cname,
        "systemd_active": False,
        "container_running": False,
        "oom_killed": False,
        "gateway_responsive": False,
        "latency_ms": -1.0,
        "status": "failed",
        "diagnosis": "",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    # 1. Systemd probe
    try:
        ret = subprocess.call(systemctl_cmd("is-active", "--quiet", unit_name), stderr=subprocess.DEVNULL)
        result["systemd_active"] = (ret == 0)
    except Exception:
        result["systemd_active"] = False

    # 2. Podman inspect
    try:
        raw_inspect = subprocess.check_output(
            podman_cmd("inspect", "-f", "{{.State.Status}}|{{.State.OOMKilled}}|{{.State.ExitCode}}", cname),
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        parts = raw_inspect.split("|")
        if len(parts) >= 3:
            c_status, oom, exit_code = parts[0], parts[1], parts[2]
            result["container_running"] = (c_status == "running")
            result["oom_killed"] = (oom.lower() == "true")
            result["exit_code"] = exit_code
    except Exception:
        result["container_running"] = False

    if not result["systemd_active"] and not result["container_running"]:
        result["status"] = "stopped"
        result["diagnosis"] = "Systemd unit and Podman container are stopped."
        return result

    if result["oom_killed"]:
        result["status"] = "failed"
        result["diagnosis"] = "Container was terminated by kernel Out-Of-Memory (OOM) killer."
        return result

    if not result["container_running"]:
        result["status"] = "failed"
        result["diagnosis"] = f"Container is not running (state: exited, exit_code: {result.get('exit_code', '?')})."
        return result

    # 3. Gateway HTTP probe inside container namespace
    t0 = time.time()
    try:
        # Run fast curl against 127.0.0.1:8642 inside container
        proc = subprocess.run(
            podman_cmd("exec", cname, "curl", "-sI", "-m", "3", "http://127.0.0.1:8642/"),
            capture_output=True, text=True, timeout=4
        )
        elapsed = round((time.time() - t0) * 1000, 1)
        # Any HTTP status response indicates aiohttp is alive
        if proc.returncode == 0 and "HTTP/" in proc.stdout:
            result["gateway_responsive"] = True
            result["latency_ms"] = elapsed
            if elapsed > 2500:
                result["status"] = "degraded"
                result["diagnosis"] = f"Gateway slow response latency ({elapsed}ms)."
            else:
                result["status"] = "healthy"
                result["diagnosis"] = "Operational and responsive."
        else:
            result["status"] = "failed"
            result["diagnosis"] = "Gateway HTTP port 8642 unreachable inside container."
    except subprocess.TimeoutExpired:
        result["status"] = "failed"
        result["diagnosis"] = "Gateway HTTP probe timed out (>3.0s, possible event loop deadlock)."
    except Exception as e:
        result["status"] = "failed"
        result["diagnosis"] = f"Gateway probe error: {str(e)[:50]}"

    # 4. Telegram Platform State & Polling Probe (for messaging agents)
    agent_dir = os.path.join(AGENTS_DIR, name)
    gw_state_file = os.path.join(agent_dir, "gateway_state.json")
    if result["status"] in ("healthy", "degraded") and os.path.exists(gw_state_file):
        try:
            with open(gw_state_file, "r", encoding="utf-8") as f:
                gw_data = json.load(f)
            platforms = gw_data.get("platforms", {})
            tg_info = platforms.get("telegram")
            if isinstance(tg_info, dict):
                tg_state = (tg_info.get("state") or "").lower()
                if tg_state in ("error", "disconnected", "retrying"):
                    result["status"] = "failed"
                    result["diagnosis"] = f"Telegram bot platform in '{tg_state}' state: {tg_info.get('error_message') or 'polling halted'}."
                    return result
        except Exception:
            pass

    # 4b. Check recent error log for stuck long-polling heartbeat
    err_log = os.path.join(agent_dir, "logs", "errors.log")
    if result["status"] in ("healthy", "degraded") and os.path.exists(err_log):
        try:
            if time.time() - os.path.getmtime(err_log) < 300:
                with open(err_log, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-40:]
                for l in reversed(lines):
                    if "Telegram polling heartbeat: 1 update(s) queued but not consumed" in l:
                        result["status"] = "failed"
                        result["diagnosis"] = "Telegram polling loop stuck (heartbeat unconsumed update detected)."
                        return result
        except Exception:
            pass

    # 5. Integration probe (specific to ha-agent / HAMagnolia)
    if name == "ha-agent" and result["status"] in ("healthy", "degraded"):
        try:
            ha_check = subprocess.run(
                podman_cmd("exec", cname, "curl", "-sI", "-m", "2", "http://192.168.50.106:8123/manifest.json"),
                capture_output=True, text=True, timeout=3
            )
            if ha_check.returncode != 0 and "HTTP/" not in ha_check.stdout:
                result["status"] = "degraded"
                result["diagnosis"] = "Home Assistant core (192.168.50.106:8123) unreachable from container."
        except Exception:
            pass

    return result


def is_circuit_broken(history, name):
    """Checks if agent has exceeded failure threshold within the flap window."""
    now = time.time()
    records = history.get(name, [])
    recent = [t for t in records if now - t < FLAP_WINDOW_SECONDS]
    return len(recent) >= MAX_RESTARTS_PER_WINDOW, len(recent)


def record_remediation(history, name):
    now = time.time()
    if name not in history:
        history[name] = []
    # Keep timestamps within 24h
    history[name] = [t for t in history[name] if now - t < 86400]
    history[name].append(now)


def self_heal_agent(probe_res, history):
    """Executes safe self-healing recovery and re-tests liveness."""
    name = probe_res["name"]
    cname = probe_res["container"]
    unit_name = probe_res["unit"]

    broken, count = is_circuit_broken(history, name)
    if broken:
        print(f"🚨 [CIRCUIT BREAKER] {name} has failed {count} times in 15m. Pausing auto-remediation.", file=sys.stderr)
        return {
            "success": False,
            "circuit_broken": True,
            "count": count,
            "message": f"Circuit breaker tripped ({count} failures in 15m). Auto-recovery paused."
        }

    print(f"🔄 [SELF-HEALING] Remediating unhealthy agent {name} (reason: {probe_res['diagnosis']})...")
    record_remediation(history, name)

    # 1. Clean stop and purge old container
    subprocess.call(podman_cmd("stop", "-t", "5", cname), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.call(podman_cmd("rm", "-f", cname), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 2. Restart systemd unit
    t0 = time.time()
    ret = subprocess.call(systemctl_cmd("restart", unit_name), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 3. Grace period polling for container & gateway startup (up to 12s)
    post_probe = None
    for attempt in range(6):
        time.sleep(2)
        post_probe = probe_agent(name)
        if post_probe["status"] in ("healthy", "degraded"):
            break

    restart_duration = round(time.time() - t0, 1)

    if post_probe and post_probe["status"] in ("healthy", "degraded"):
        msg = f"🟢 Agent {name} self-healed successfully in {restart_duration}s."
        print(f"[✓] {msg}")
        return {
            "success": True,
            "circuit_broken": False,
            "count": count + 1,
            "restart_duration": restart_duration,
            "post_probe": post_probe,
            "message": msg
        }
    else:
        diag = post_probe["diagnosis"] if post_probe else "Unknown failure"
        msg = f"❌ Self-healing attempt failed for {name}: {diag}"
        print(f"[-] {msg}", file=sys.stderr)
        return {
            "success": False,
            "circuit_broken": False,
            "count": count + 1,
            "restart_duration": restart_duration,
            "post_probe": post_probe,
            "message": msg
        }


def notify_telegram(message, force=False):
    """Sends notification through the deduplicating Telegram gateway."""
    if not os.path.exists(NOTIFY_SCRIPT):
        return
    args = [NOTIFY_SCRIPT]
    if os.geteuid() != 0:
        args = ["sudo", NOTIFY_SCRIPT]
    if force:
        args.append("--force")
    args.append(message)
    try:
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass


def run_probe_cycle(auto_heal=True):
    """Main health sweep across all agents."""
    state = load_state()
    agent_names = discover_agents()
    history = state.get("history", {})

    results = {}
    healed_events = []
    cb_events = []

    for name in agent_names:
        res = probe_agent(name)
        broken, count = is_circuit_broken(history, name)
        res["circuit_broken"] = broken
        res["restarts_24h"] = len(history.get(name, []))

        # Check self-healing
        if res["status"] in ("failed", "stopped") and auto_heal:
            heal_res = self_heal_agent(res, history)
            if heal_res["circuit_broken"]:
                res["status"] = "quarantined"
                res["diagnosis"] = heal_res["message"]
                cb_events.append((name, heal_res))
            elif heal_res["success"]:
                res = heal_res["post_probe"]
                res["circuit_broken"] = False
                res["restarts_24h"] = len(history.get(name, []))
                healed_events.append((name, heal_res))
            else:
                res["diagnosis"] = heal_res["message"]

        results[name] = res

    # Calculate overall health
    total = len(results)
    healthy = sum(1 for r in results.values() if r["status"] == "healthy")
    degraded = sum(1 for r in results.values() if r["status"] == "degraded")
    score_pct = round(((healthy + (degraded * 0.5)) / total) * 100, 1) if total else 100.0

    state["timestamp"] = int(time.time())
    state["iso_timestamp"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    state["total_agents"] = total
    state["healthy_agents"] = healthy
    state["swarm_score_pct"] = score_pct
    state["system"] = get_system_metrics()
    state["agents"] = results
    state["history"] = history

    save_state(state)

    # Dispatch alerts for self-healed incidents
    for name, h_info in healed_events:
        card = (
            f"🔄 Fleet Self-Healing Executed\n"
            f"• Target: hermes-{name}\n"
            f"• Action: Automated recreation & systemd unit restart\n"
            f"• Recovery: 🟢 Restored in {h_info.get('restart_duration', '?')}s\n"
            f"• Latency: {h_info.get('post_probe', {}).get('latency_ms', -1)}ms\n"
            f"• Restarts in Window: {h_info.get('count', 1)}/{MAX_RESTARTS_PER_WINDOW}"
        )
        notify_telegram(card, force=False)

    # Dispatch alerts for circuit breaker trips
    for name, cb_info in cb_events:
        card = (
            f"🚨 Fleet Circuit Breaker Tripped!\n"
            f"• Target: hermes-{name}\n"
            f"• Incident: Exceeded {MAX_RESTARTS_PER_WINDOW} restarts within 15 minutes\n"
            f"• Action: Auto-remediation paused to prevent host resource starvation\n"
            f"• Status: ⚠️ Quarantined (Investigate via /logs {name})"
        )
        notify_telegram(card, force=True)

    return state


def generate_digest(tg=False):
    """Generates the periodic 6-hour comprehensive health digest card."""
    state = run_probe_cycle(auto_heal=True)
    sys_metrics = state.get("system", {})
    agents = state.get("agents", {})
    score = state.get("swarm_score_pct", 100.0)
    total = state.get("total_agents", 0)
    healthy = state.get("healthy_agents", 0)

    status_icon = "🟢" if score >= 90 else ("🟡" if score >= 60 else "🔴")

    lines = [
        f"🏥 Hermes Fleet Health Digest",
        f"• Status: {status_icon} {healthy}/{total} Agents Operational ({score}% score)",
        f"• CPU Load: {sys_metrics.get('load', 'N/A')}",
        f"• Memory: {sys_metrics.get('mem_avail_mb', 0)}MB free / {sys_metrics.get('mem_total_mb', 0)}MB ({sys_metrics.get('mem_used_pct', 0)}% used)",
        f"• Root Storage: {sys_metrics.get('disk_free_gb', 0)} GB free ({sys_metrics.get('disk_used_pct', 0)}% used)",
        f"• Agent Roster:"
    ]

    for name, a in sorted(agents.items()):
        astatus = a.get("status", "unknown")
        icon = "🟢" if astatus == "healthy" else ("🟡" if astatus == "degraded" else ("⚠️" if astatus == "quarantined" else "🔴"))
        lat = f"{a.get('latency_ms', -1)}ms" if a.get('latency_ms', -1) >= 0 else "N/A"
        restarts = a.get("restarts_24h", 0)
        rst_note = f" (restarts: {restarts})" if restarts > 0 else ""
        lines.append(f"  - {name}: {icon} {astatus.title()} ({lat}){rst_note}")

    digest_text = "\n".join(lines)

    if tg:
        notify_telegram(digest_text, force=False)

    return digest_text


def main():
    parser = argparse.ArgumentParser(description="Hermes Fleet Health Watchdog & Self-Healing Sentinel")
    parser.add_argument("--probe", action="store_true", help="Execute single multi-tier probe cycle with auto-healing")
    parser.add_argument("--status", action="store_true", help="Print live health summary")
    parser.add_argument("--digest", action="store_true", help="Generate 6-hour health digest card")
    parser.add_argument("--tg", action="store_true", help="Send output to Telegram")
    parser.add_argument("--heal", type=str, help="Explicitly force-heal a named agent")
    parser.add_argument("--json", action="store_true", help="Output raw JSON state")

    args = parser.parse_args()

    if args.heal:
        res = probe_agent(args.heal)
        state = load_state()
        history = state.get("history", {})
        h_res = self_heal_agent(res, history)
        state["history"] = history
        save_state(state)
        print(h_res["message"])
        sys.exit(0 if h_res["success"] else 1)

    if args.digest:
        text = generate_digest(tg=args.tg)
        print(text)
        sys.exit(0)

    # Default action: probe
    state = run_probe_cycle(auto_heal=True)
    if args.json:
        print(json.dumps(state, indent=2))
    elif args.status or not args.probe:
        print(f"=== Hermes Fleet Health Status ===")
        print(f"• Swarm Health Score: {state.get('swarm_score_pct', 0)}%")
        print(f"• Operational: {state.get('healthy_agents', 0)} / {state.get('total_agents', 0)}")
        for name, a in sorted(state.get("agents", {}).items()):
            icon = "🟢" if a['status'] == 'healthy' else ("🟡" if a['status'] == 'degraded' else "🔴")
            print(f"  [{icon}] {name:20}: {a['status'].upper():10} (latency: {a['latency_ms']}ms, restarts_24h: {a['restarts_24h']})")


if __name__ == "__main__":
    main()
