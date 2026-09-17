#!/usr/bin/env bash
# /opt/fleet/bin/fleet-update.sh
# Fleet self-update and child agent reconciliation with health check and rollback.

set -e

echo "=================================================="
echo "    🚀 Hermes Fleet Controller — Fleet Update"
echo "=================================================="

# 1. Snapshot inventory
cp -f /opt/fleet/inventory.yaml /opt/fleet/inventory.pre-update.yaml 2>/dev/null || true

OLD_H_VER=$(hermes --version 2>/dev/null | head -n 1 || echo "v0.21.3")

# 2. Pull newest image / update
echo "[*] Pulling latest Hermes container image..."
podman pull docker.io/nousresearch/hermes-agent:latest || true

# Refresh Venice model tiers
echo "[*] Refreshing Venice model cache..."
/opt/fleet/bin/venice-resolve-model.sh refresh || true

RESTARTED_UNITS=()
FAILED_UNITS=()

# 3. Update child agents if track enabled
for AGENT_DIR in /opt/fleet/agents/*; do
  if [ -d "$AGENT_DIR" ] && [ "$(basename "$AGENT_DIR")" != "fleet-controller" ]; then
    NAME=$(basename "$AGENT_DIR")
    META_FILE="${AGENT_DIR}/key-meta.json"
    if [ -f "$META_FILE" ]; then
      QUALITY=$(python3 -c "import json; print(json.load(open('$META_FILE')).get('quality', 'medium'))" 2>/dev/null || echo "medium")
      TRACK=$(python3 -c "import json; print(json.load(open('$META_FILE')).get('track', 'latest-medium'))" 2>/dev/null || echo "")
      if [[ "$TRACK" =~ ^latest- ]]; then
        NEW_MODEL=$(/opt/fleet/bin/venice-resolve-model.sh "$QUALITY" 2>/dev/null || true)
        if [ -n "$NEW_MODEL" ]; then
          # Update config.yaml with resolved model
          sed -i -E "s/default: .*/default: \"${NEW_MODEL}\"/" "${AGENT_DIR}/config.yaml"
          sed -i -E "s/model: .*/model: \"${NEW_MODEL}\"/" "${AGENT_DIR}/config.yaml" 2>/dev/null || true
          echo "[*] Agent ${NAME} tracked to latest ${QUALITY}: ${NEW_MODEL}"
        fi
      fi
    fi
  fi
done

# 4. Restart controller and child units
echo "[*] Restarting hermes-fleet-controller.service..."
systemctl restart hermes-fleet-controller.service
RESTARTED_UNITS+=("hermes-fleet-controller.service")

for UNIT in $(systemctl list-unit-files 'hermes-*.service' --state=enabled --no-legend | awk '{print $1}'); do
  if [ "$UNIT" != "hermes-fleet-controller.service" ] && [ "$UNIT" != "hermes-gateway.service" ]; then
    echo "[*] Restarting $UNIT..."
    systemctl restart "$UNIT" || true
    RESTARTED_UNITS+=("$UNIT")
  fi
done

# 5. Wait and Health Check
echo "[*] Running health checks..."
sleep 6

for UNIT in "${RESTARTED_UNITS[@]}"; do
  ATTEMPTS=0
  HEALTHY=0
  while [ $ATTEMPTS -lt 2 ]; do
    if systemctl is-active --quiet "$UNIT"; then
      HEALTHY=1
      break
    fi
    ATTEMPTS=$((ATTEMPTS + 1))
    echo "[!] $UNIT inactive, retrying ($ATTEMPTS/2)..."
    systemctl restart "$UNIT" || true
    sleep 4
  done

  if [ $HEALTHY -eq 0 ]; then
    FAILED_UNITS+=("$UNIT")
  fi
done

# 6. Re-scan fleet
/opt/fleet/bin/fleet-scan.py >/dev/null 2>&1 || true

NEW_H_VER=$(hermes --version 2>/dev/null | head -n 1 || echo "v0.21.3")

# 7. Compose and send Telegram notification
REPORT=$(cat <<EOF
🔄 Fleet Update Report
• Controller Version: ${NEW_H_VER}
• Restarted: ${RESTARTED_UNITS[*]}
• Live Units: $(( ${#RESTARTED_UNITS[@]} - ${#FAILED_UNITS[@]} ))/${#RESTARTED_UNITS[@]}
• Failures: ${FAILED_UNITS[*]:-none}
EOF
)

/opt/fleet/bin/fleet-telegram-notify.sh "$REPORT" || true
echo "$REPORT"
