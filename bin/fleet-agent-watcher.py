#!/usr/bin/env python3
"""
/opt/fleet/bin/fleet-agent-watcher.py
Autonomous real-time watcher daemon for Hermes Fleet Controller.
Continuously monitors Podman container lifecycle events and systemd services.
Instantly detects new Hermes agents run on this system, triggers automated
onboarding, connects them to the manager, and syncs inventory.
"""

import os
import sys
import time
import json
import signal
import subprocess
import threading

FLEET_DIR = "/opt/fleet"
CONNECT_SCRIPT = os.path.join(FLEET_DIR, "bin", "fleet-connect-agent.py")
SCAN_SCRIPT = os.path.join(FLEET_DIR, "bin", "fleet-scan.py")
PRUNE_REQ = os.path.join(FLEET_DIR, "shared-workspace", "prune.req")
PRUNE_OUT = os.path.join(FLEET_DIR, "shared-workspace", "prune.out")
PRUNE_SCRIPT = os.path.join(FLEET_DIR, "bin", "fleet-prune.sh")

running = True


def watch_prune_requests():
    """Processes on-demand prune requests from sandboxed child agents."""
    global running
    while running:
        if os.path.exists(PRUNE_REQ):
            try:
                with open(PRUNE_REQ, "r", encoding="utf-8") as f:
                    args_line = f.read().strip()
                try:
                    os.remove(PRUNE_REQ)
                except Exception:
                    pass
                args = [PRUNE_SCRIPT]
                if args_line:
                    args.extend(args_line.split())
                res = subprocess.run(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                with open(PRUNE_OUT, "w", encoding="utf-8") as f:
                    f.write(res.stdout or "")
            except Exception as e:
                try:
                    with open(PRUNE_OUT, "w", encoding="utf-8") as f:
                        f.write(f"Error executing prune: {e}\n")
                except Exception:
                    pass
        time.sleep(0.5)



def handle_signal(sig, frame):
    global running
    print(f"[*] Received shutdown signal ({sig}). Exiting watcher...")
    running = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def periodic_scan_loop(interval=15):
    """Fallback polling loop to guarantee detection even if events stream drops."""
    global running
    while running:
        try:
            subprocess.run(
                ["python3", CONNECT_SCRIPT, "--scan"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"[-] Error in periodic scan: {e}", file=sys.stderr)
        
        for _ in range(interval):
            if not running:
                break
            time.sleep(1)


_recent_events = {}
_recent_lock = threading.Lock()


def watch_podman_events():
    """Streams live container events from Podman to react with sub-second latency."""
    global running, _recent_events
    while running:
        try:
            proc = subprocess.Popen(
                ["podman", "events", "--filter", "event=start", "--format", "{{.Name}}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            while running:
                line = proc.stdout.readline()
                if not line:
                    break
                cname = line.strip()
                if not cname or cname.startswith("fleet-controller"):
                    continue

                now = time.time()
                with _recent_lock:
                    last_time = _recent_events.get(cname, 0)
                    if now - last_time < 60:
                        # Debounce rapid restart storms
                        continue
                    _recent_events[cname] = now

                print(f"[*] Podman event: container '{cname}' started. Triggering manager connection...")
                # Run connection in background thread so events stream isn't blocked
                t = threading.Thread(
                    target=lambda name: subprocess.run(
                        ["python3", CONNECT_SCRIPT, name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    ),
                    args=(cname,)
                )
                t.daemon = True
                t.start()
        except Exception as e:
            if running:
                print(f"[-] Podman events stream error: {e}. Retrying in 5s...", file=sys.stderr)
                time.sleep(5)


def main():
    print("============================================================")
    print("    🛰️ Hermes Fleet Agent Auto-Discovery & Manager Watcher")
    print("============================================================")
    print(f"Started at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")

    # 1. Run initial scan on startup
    print("[*] Running initial agent discovery scan...")
    try:
        subprocess.run(["python3", CONNECT_SCRIPT, "--scan"], check=False)
    except Exception as e:
        print(f"[-] Initial scan warning: {e}", file=sys.stderr)

    # 2. Start polling thread & prune request listener
    poll_thread = threading.Thread(target=periodic_scan_loop, args=(15,))
    poll_thread.daemon = True
    poll_thread.start()

    prune_thread = threading.Thread(target=watch_prune_requests)
    prune_thread.daemon = True
    prune_thread.start()

    # 3. Stream live podman events on main thread
    watch_podman_events()
    print("[✓] Watcher terminated gracefully.")


if __name__ == "__main__":
    main()
