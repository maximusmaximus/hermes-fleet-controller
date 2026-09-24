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

running = True


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


def watch_podman_events():
    """Streams live container events from Podman to react with sub-second latency."""
    global running
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
                if cname and not cname.startswith("fleet-controller"):
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

    # 2. Start polling thread
    poll_thread = threading.Thread(target=periodic_scan_loop, args=(15,))
    poll_thread.daemon = True
    poll_thread.start()

    # 3. Stream live podman events on main thread
    watch_podman_events()
    print("[✓] Watcher terminated gracefully.")


if __name__ == "__main__":
    main()
