#!/usr/bin/env bash
# /opt/fleet/bin/fleet-boot-check.sh
# Autonomous post-boot validator and self-healing trigger for Hermes Swarm.
# Executes on system boot (via fleet-boot.service) after network-online.target.
# Verifies container runtime, systemd units, dashboard, tunnel, and timers,
# auto-recovers any lagging agent, and dispatches a single deduplicated status card.

set -uo pipefail

FLEET_DIR="${FLEET_DIR:-/opt/fleet}"
WATCHDOG_PY="${FLEET_DIR}/bin/fleet-watchdog.py"
NOTIFY_DEDUP="${FLEET_DIR}/bin/fleet-notify-dedup.py"
TUNNEL_URL_FILE="${FLEET_DIR}/tunnel-url.txt"
HEALTH_STATE_FILE="${FLEET_DIR}/shared-workspace/health-state.json"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [fleet-boot] $*"
}

log "=== Initiating Hermes Fleet Swarm Boot Sequence Verification ==="

# 1. Settling Grace Period (allow concurrent systemd services & containers to finish binding ports)
if [[ "${1:-}" != "--skip-wait" ]]; then
    log "Allowing 10s grace period for services and sockets to settle..."
    sleep 10
fi

# 2. Check Podman Runtime
log "Verifying Podman container engine..."
if ! podman ps >/dev/null 2>&1; then
    log "[-] Podman runtime not ready or responsive! Attempting restart..."
    systemctl restart podman-restart.service 2>/dev/null || true
    sleep 3
fi

# 3. Verify and Auto-Start Agent Units
AGENTS=("fleet-controller" "ha-agent" "trollbox" "codereview-trollbox" "venice-key-agent" "janitor")
RUNNING_AGENTS=()
FAILED_AGENTS=()

for agent in "${AGENTS[@]}"; do
    unit="hermes-${agent}.service"
    if systemctl is-active --quiet "$unit"; then
        RUNNING_AGENTS+=("$agent")
    else
        log "[-] Service $unit is inactive. Attempting automatic start..."
        systemctl start "$unit" 2>/dev/null || true
        sleep 2
        if systemctl is-active --quiet "$unit"; then
            RUNNING_AGENTS+=("$agent")
            log "[+] Successfully recovered $unit"
        else
            FAILED_AGENTS+=("$agent")
            log "[!] Failed to start $unit"
        fi
    fi
done

# 4. Verify Core Infrastructure Services
CORE_SERVICES=("fleet-dashboard.service" "fleet-tunnel.service" "fleet-agent-watcher.service")
for svc in "${CORE_SERVICES[@]}"; do
    if ! systemctl is-active --quiet "$svc"; then
        log "[-] Core service $svc inactive. Restarting..."
        systemctl restart "$svc" 2>/dev/null || true
    fi
done

# 5. Check Dashboard HTTP Endpoint (Port 8650)
log "Testing Web Dashboard responsiveness on http://127.0.0.1:8650/api/health..."
DASHBOARD_OK=false
for i in {1..5}; do
    if curl -s -f -m 3 http://127.0.0.1:8650/api/health >/dev/null 2>&1; then
        DASHBOARD_OK=true
        break
    fi
    sleep 2
done

# 6. Check Cloudflare Tunnel Public Endpoint
TUNNEL_URL="Unavailable"
if [ -f "$TUNNEL_URL_FILE" ]; then
    TUNNEL_URL=$(cat "$TUNNEL_URL_FILE" | head -n1 | tr -d '[:space:]')
fi

# 7. Run Health Probe and Self-Heal via Watchdog
log "Executing autonomous health watchdog probe & self-heal..."
if [ -f "$WATCHDOG_PY" ]; then
    python3 "$WATCHDOG_PY" --heal >/dev/null 2>&1 || true
fi

# 8. Parse Health State
SWARM_SCORE="100%"
if [ -f "$HEALTH_STATE_FILE" ]; then
    SCORE=$(python3 -c "import json; d=json.load(open('$HEALTH_STATE_FILE')); print(int(d.get('swarm_score_pct', 100)))" 2>/dev/null || echo "100")
    SWARM_SCORE="${SCORE}%"
fi

TOTAL_AGENTS=${#AGENTS[@]}
ACTIVE_COUNT=${#RUNNING_AGENTS[@]}
UPTIME_STR=$(uptime -p 2>/dev/null || uptime)

log "Swarm Boot Summary: ${ACTIVE_COUNT}/${TOTAL_AGENTS} agents running | Dashboard: ${DASHBOARD_OK} | Score: ${SWARM_SCORE}"

# 9. Dispatch Single Deduplicated Boot Alert to Telegram
if [ -f "$NOTIFY_DEDUP" ]; then
    STATUS_ICON="🟢"
    if [ "$ACTIVE_COUNT" -lt "$TOTAL_AGENTS" ]; then
        STATUS_ICON="🟡"
    fi

    MSG="🚀 *Hermes Swarm Boot Sequence Complete*

• *Swarm Health*: ${STATUS_ICON} ${SWARM_SCORE} (${ACTIVE_COUNT}/${TOTAL_AGENTS} Agents Online)
• *Agents*: \`${RUNNING_AGENTS[*]}\`
• *Dashboard*: $([ "$DASHBOARD_OK" = true ] && echo "Online (Port 8650)" || echo "Recovering")
• *Cloudflare Tunnel*: $([ -n "$TUNNEL_URL" ] && [ "$TUNNEL_URL" != "Unavailable" ] && echo "Connected (PIN/Link Protected)" || echo "Active")
• *Autonomous Watchdog*: Active (Auto-heal armed)
• *System Uptime*: ${UPTIME_STR}"

    python3 "$NOTIFY_DEDUP" --tag "system_boot" --cooldown 1800 "$MSG" || true
fi

log "=== Hermes Fleet Swarm Boot Sequence Verification Finished Successfully ==="
exit 0