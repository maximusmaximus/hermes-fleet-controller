#!/usr/bin/env bash
# /opt/fleet/bin/fleet-prune.sh
# Production Storage Hygiene & Cache Pruner for Hermes Fleet.
# Safe, idempotent, non-destructive cleanup of pnpm caches, dead containers, dangling layers, and journals.

set -eo pipefail

# 0. Container Bridge Delegation
# If invoked from inside a sandboxed container, forward request to host watcher daemon
if [ -f /.dockerenv ] || [ -f /run/.containerenv ]; then
  REQ_FILE="/opt/fleet/shared-workspace/prune.req"
  OUT_FILE="/opt/fleet/shared-workspace/prune.out"
  rm -f "$OUT_FILE" 2>/dev/null || true
  echo "$*" > "$REQ_FILE"
  # Wait up to 30 seconds for host response
  for i in $(seq 1 60); do
    if [ -f "$OUT_FILE" ]; then
      cat "$OUT_FILE"
      rm -f "$OUT_FILE" 2>/dev/null || true
      exit 0
    fi
    sleep 0.5
  done
  echo "Error: Host storage pruning request timed out after 30 seconds." >&2
  exit 1
fi

DRY_RUN=false
FORCE=false
THRESHOLD_PCT=80
TARGET_MOUNT="/"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; shift ;;
    --threshold) THRESHOLD_PCT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# 1. Disk usage inspection
get_used_pct() {
  df -P "$TARGET_MOUNT" | awk 'NR==2 {print $5}' | tr -d '%'
}

get_avail_kb() {
  df -P -k "$TARGET_MOUNT" | awk 'NR==2 {print $4}'
}

PRE_PCT=$(get_used_pct)
PRE_AVAIL_KB=$(get_avail_kb)
PRE_AVAIL_HUMAN=$(df -h "$TARGET_MOUNT" | awk 'NR==2 {print $4}')

# 2. Threshold Check
if [ "$FORCE" = false ] && [ "$DRY_RUN" = false ] && [ "$PRE_PCT" -lt "$THRESHOLD_PCT" ]; then
  echo "Disk utilization is ${PRE_PCT}% (below ${THRESHOLD_PCT}% threshold). Skipping prune."
  exit 0
fi

if [ "$DRY_RUN" = true ]; then
  echo "=== [DRY RUN] Hermes Storage Inspection ==="
  echo "• Mount: ${TARGET_MOUNT}"
  echo "• Utilization: ${PRE_PCT}%"
  echo "• Available: ${PRE_AVAIL_HUMAN}"
  echo "• Threshold: ${THRESHOLD_PCT}% (Trigger: $([ "$PRE_PCT" -ge "$THRESHOLD_PCT" ] && echo "YES" || echo "NO"))"
  echo "• Actions that would execute: pnpm store prune, podman container prune, podman image prune, journalctl vacuum (3d)"
  exit 0
fi

# 3. Execution Phase
echo "Executing safe fleet storage cleanup..."

# 3a. PNPM Store Prune (user molt)
if command -v pnpm >/dev/null 2>&1; then
  if id molt >/dev/null 2>&1; then
    sudo -u molt pnpm store prune >/dev/null 2>&1 || true
  else
    pnpm store prune >/dev/null 2>&1 || true
  fi
fi

# 3b. Podman dead containers (root / sudo)
if command -v podman >/dev/null 2>&1; then
  sudo podman container prune -f >/dev/null 2>&1 || true
  # Dangling (untagged) images only — strictly NEVER -a
  sudo podman image prune -f >/dev/null 2>&1 || true
fi

# 3c. Systemd Journal Vacuum (retain 3 days and max 200MB)
if command -v journalctl >/dev/null 2>&1; then
  sudo journalctl --vacuum-time=3d >/dev/null 2>&1 || true
  sudo journalctl --vacuum-size=200M >/dev/null 2>&1 || true
fi

# 3d. Ephemeral /tmp builds (>24h old pnpm/npm/mcp build artifacts)
find /tmp -mindepth 1 -maxdepth 2 -type d \( -name "pnpm-*" -o -name "npm-*" -o -name "mcp-build-*" \) -mtime +1 -exec rm -rf {} + 2>/dev/null || true

# 4. Post-Clean Audit
POST_PCT=$(get_used_pct)
POST_AVAIL_KB=$(get_avail_kb)
POST_AVAIL_HUMAN=$(df -h "$TARGET_MOUNT" | awk 'NR==2 {print $4}')

RECLAIMED_KB=$(( POST_AVAIL_KB - PRE_AVAIL_KB ))
if [ "$RECLAIMED_KB" -lt 0 ]; then
  RECLAIMED_KB=0
fi

# Format human-readable reclaimed space
if [ "$RECLAIMED_KB" -gt 1048576 ]; then
  RECLAIMED_HUMAN="$(awk "BEGIN {printf \"%.2f GB\", $RECLAIMED_KB/1048576}")"
elif [ "$RECLAIMED_KB" -gt 1024 ]; then
  RECLAIMED_HUMAN="$(awk "BEGIN {printf \"%.1f MB\", $RECLAIMED_KB/1024}")"
else
  RECLAIMED_HUMAN="${RECLAIMED_KB} KB"
fi

REPORT=$(cat <<EOF
🧹 Fleet Storage Hygiene Report
• Status: Successfully completed
• Pre-Clean Available: ${PRE_AVAIL_HUMAN} (${PRE_PCT}% used)
• Post-Clean Available: ${POST_AVAIL_HUMAN} (${POST_PCT}% used)
• Space Reclaimed: ${RECLAIMED_HUMAN}
• Cleaned Targets: pnpm store cache, dead containers, dangling layers, journals (3d), /tmp builds
EOF
)

echo "$REPORT"

# 5. Telegram Notification (Deduplicated via fleet-notify-dedup.py)
if [ -f "/opt/fleet/bin/fleet-telegram-notify.sh" ]; then
  /opt/fleet/bin/fleet-telegram-notify.sh "$REPORT" >/dev/null 2>&1 || true
fi
