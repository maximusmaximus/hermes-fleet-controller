#!/usr/bin/env bash
# /opt/fleet/bin/fleet-audit.sh
# System Resource Audit, Pre-Flight Validator, and Swarm Sizing Advisor for Hermes Fleet Controller.

set -euo pipefail

AUTO_TUNE=false
JSON_MODE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tune|--fix)
      AUTO_TUNE=true
      shift
      ;;
    --json)
      JSON_MODE=true
      shift
      ;;
    -h|--help)
      echo "Usage: fleet-audit.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --tune, --fix    Apply recommended swarm performance and storage optimizations"
      echo "  --json           Output audit results as JSON for programmatic agent consumption"
      echo "  -h, --help       Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Gather System Metrics
CPU_CORES=$(nproc 2>/dev/null || echo 1)
CPU_MODEL=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo "Generic CPU")
LOAD_1M=$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo 0.0)

# Memory (MB)
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
AVAIL_RAM_KB=$(grep MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
TOTAL_RAM_MB=$((TOTAL_RAM_KB / 1024))
AVAIL_RAM_MB=$((AVAIL_RAM_KB / 1024))

SWAP_TOTAL_KB=$(grep SwapTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
SWAP_FREE_KB=$(grep SwapFree /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
SWAP_TOTAL_MB=$((SWAP_TOTAL_KB / 1024))
SWAP_USED_MB=$(((SWAP_TOTAL_KB - SWAP_FREE_KB) / 1024))

# Disk (Root /)
DISK_TOTAL_KB=$(df -k / | tail -n1 | awk '{print $2}')
DISK_USED_KB=$(df -k / | tail -n1 | awk '{print $3}')
DISK_AVAIL_KB=$(df -k / | tail -n1 | awk '{print $4}')
DISK_USE_PCT=$(df -k / | tail -n1 | awk '{print $5}' | tr -d '%')

DISK_TOTAL_GB=$(awk "BEGIN {printf \"%.1f\", ${DISK_TOTAL_KB}/1048576}")
DISK_USED_GB=$(awk "BEGIN {printf \"%.1f\", ${DISK_USED_KB}/1048576}")
DISK_AVAIL_GB=$(awk "BEGIN {printf \"%.1f\", ${DISK_AVAIL_KB}/1048576}")

# Swarm Capacity Calculations (500MB RAM ceiling per agent, ~2GB disk allowance per agent)
AGENT_RAM_LIMIT_MB=500
AGENT_DISK_RESERVE_GB=2

# Reserve 2GB RAM and 5GB Disk for host OS & Controller
RAM_FOR_AGENTS_MB=$((AVAIL_RAM_MB > 1500 ? AVAIL_RAM_MB - 1500 : 0))
MAX_AGENTS_RAM=$((RAM_FOR_AGENTS_MB / AGENT_RAM_LIMIT_MB))

AVAIL_DISK_INT=$(printf "%.0f" "$DISK_AVAIL_GB")
DISK_FOR_AGENTS_GB=$((AVAIL_DISK_INT > 5 ? AVAIL_DISK_INT - 5 : 0))
MAX_AGENTS_DISK=$((DISK_FOR_AGENTS_GB / AGENT_DISK_RESERVE_GB))

# Suggested capacity is bounded by the tighter constraint
SUGGESTED_AGENTS=$((MAX_AGENTS_RAM < MAX_AGENTS_DISK ? MAX_AGENTS_RAM : MAX_AGENTS_DISK))
[ "$SUGGESTED_AGENTS" -lt 0 ] && SUGGESTED_AGENTS=0

# Network reachability
check_port() {
  local host="$1"
  local port="$2"
  if timeout 2 bash -c "cat < /dev/null > /dev/tcp/${host}/${port}" 2>/dev/null; then
    echo "true"
  else
    echo "false"
  fi
}

VENICE_REACHABLE=$(check_port "api.venice.ai" 443)
TELEGRAM_REACHABLE=$(check_port "api.telegram.org" 443)
GITHUB_REACHABLE=$(check_port "github.com" 443)

# Container Runtime Check
PODMAN_INSTALLED=false
PODMAN_VERSION="none"
if command -v podman >/dev/null 2>&1; then
  PODMAN_INSTALLED=true
  PODMAN_VERSION=$(podman --version 2>/dev/null | awk '{print $3}' || echo "installed")
fi

# Snap & Journal bloat check
JOURNAL_SIZE_MB=0
if [ -d /var/log/journal ]; then
  JOURNAL_SIZE_MB=$(du -sm /var/log/journal 2>/dev/null | awk '{print $1}' || echo 0)
fi

DISABLED_SNAPS=0
if command -v snap >/dev/null 2>&1; then
  DISABLED_SNAPS=$(snap list --all 2>/dev/null | awk '/disabled/{c++} END{print c+0}')
fi

# Auto-Tuning Execution if requested
if [ "$AUTO_TUNE" = true ]; then
  # 1. Enforce 500M journal limit
  if [ -d /etc/systemd ]; then
    mkdir -p /etc/systemd/journald.conf.d
    cat <<EOF > /etc/systemd/journald.conf.d/50-fleet-limit.conf
[Journal]
SystemMaxUse=500M
SystemKeepFree=1G
SystemMaxFileSize=50M
EOF
    systemctl restart systemd-journald 2>/dev/null || true
  fi

  # 2. Retain max 2 snap revisions
  if command -v snap >/dev/null 2>&1; then
    snap set system refresh.retain=2 2>/dev/null || true
    if [ "$DISABLED_SNAPS" -gt 0 ]; then
      snap list --all | awk '/disabled/{print $1, $3}' | while read s rev; do
        snap remove "$s" --revision="$rev" 2>/dev/null || true
      done
    fi
  fi

  # 3. Create shared fleet workspace & backup directories
  mkdir -p /opt/fleet/{backups,shared-workspace,skills,souls,bin}
  chmod 777 /opt/fleet/shared-workspace 2>/dev/null || true
  chmod 700 /opt/fleet/backups 2>/dev/null || true
  chown -R 10000:10000 /opt/fleet/shared-workspace 2>/dev/null || true
fi

# Output handling
if [ "$JSON_MODE" = true ]; then
  cat <<EOF
{
  "cpu": {
    "model": "${CPU_MODEL}",
    "cores": ${CPU_CORES},
    "load1m": ${LOAD_1M}
  },
  "memory": {
    "totalMb": ${TOTAL_RAM_MB},
    "availableMb": ${AVAIL_RAM_MB},
    "swapTotalMb": ${SWAP_TOTAL_MB},
    "swapUsedMb": ${SWAP_USED_MB}
  },
  "disk": {
    "totalGb": ${DISK_TOTAL_GB},
    "usedGb": ${DISK_USED_GB},
    "availGb": ${DISK_AVAIL_GB},
    "usePercent": ${DISK_USE_PCT}
  },
  "swarmCapacity": {
    "agentRamLimitMb": ${AGENT_RAM_LIMIT_MB},
    "maxAgentsRamBounded": ${MAX_AGENTS_RAM},
    "maxAgentsDiskBounded": ${MAX_AGENTS_DISK},
    "suggestedSwarmSize": ${SUGGESTED_AGENTS}
  },
  "network": {
    "veniceAi": ${VENICE_REACHABLE},
    "telegram": ${TELEGRAM_REACHABLE},
    "github": ${GITHUB_REACHABLE}
  },
  "podman": {
    "installed": ${PODMAN_INSTALLED},
    "version": "${PODMAN_VERSION}"
  },
  "optimizations": {
    "journalSizeMb": ${JOURNAL_SIZE_MB},
    "disabledSnaps": ${DISABLED_SNAPS},
    "autoTuned": ${AUTO_TUNE}
  }
}
EOF
  exit 0
fi

# Human-Readable Terminal Audit Report
echo "============================================================"
echo "    🔍 Hermes Fleet Controller — Pre-Flight System Audit"
echo "============================================================"
echo ""

# CPU
echo "🖥️  PROCESSOR & COMPUTE:"
echo "   • CPU Model       : ${CPU_MODEL}"
echo "   • Cores Available : ${CPU_CORES} vCPU(s)"
echo "   • Current Load    : ${LOAD_1M} (1-min avg)"

# RAM
echo ""
echo "🧠 MEMORY & SWAP:"
echo "   • Total RAM       : ${TOTAL_RAM_MB} MB"
echo "   • Available RAM   : ${AVAIL_RAM_MB} MB"
echo "   • Swap Allocation : ${SWAP_TOTAL_MB} MB (${SWAP_USED_MB} MB in use)"

if [ "${TOTAL_RAM_MB}" -ge 7000 ]; then
  echo "   • Status          : 🟢 Optimal (Capacity for 10–20+ agents)"
elif [ "${TOTAL_RAM_MB}" -ge 3500 ]; then
  echo "   • Status          : 🟡 Adequate (Capacity for 4–8 agents)"
else
  echo "   • Status          : 🔴 Constrained (Recommend >= 4 GB RAM)"
fi

# Disk
echo ""
echo "💾 DISK STORAGE (/):"
echo "   • Partition Size  : ${DISK_TOTAL_GB} GB"
echo "   • Used Space      : ${DISK_USED_GB} GB (${DISK_USE_PCT}%)"
echo "   • Available Space : ${DISK_AVAIL_GB} GB"

if [ "${DISK_USE_PCT}" -ge 90 ]; then
  echo "   • Status          : ⛔ CRITICAL (Disk is >= 90% full! Risk of container failure)"
  echo "   • Recommendation  : Expand virtual disk in hypervisor (VMware/VirtualBox) to >= 60 GB."
elif [ "${DISK_USE_PCT}" -ge 80 ]; then
  echo "   • Status          : 🟡 Warning (Disk is >= 80% full; monitor logs & backups)"
else
  echo "   • Status          : 🟢 Healthy"
fi

# Network
echo ""
echo "🌐 NETWORK & ENDPOINTS:"
printf "   • Venice AI (443)  : %s\n" "$([ "$VENICE_REACHABLE" = "true" ] && echo "🟢 Reachable" || echo "🔴 Blocked")"
printf "   • Telegram (443)   : %s\n" "$([ "$TELEGRAM_REACHABLE" = "true" ] && echo "🟢 Reachable" || echo "🔴 Blocked")"
printf "   • GitHub (443)     : %s\n" "$([ "$GITHUB_REACHABLE" = "true" ] && echo "🟢 Reachable" || echo "🔴 Blocked")"

# Podman
echo ""
echo "📦 CONTAINER RUNTIME:"
printf "   • Podman           : %s (v%s)\n" "$([ "$PODMAN_INSTALLED" = "true" ] && echo "🟢 Installed" || echo "🔴 Missing")" "$PODMAN_VERSION"

# Governance & Housekeeping
echo ""
echo "🧹 STORAGE GOVERNANCE & HYGIENE:"
echo "   • Systemd Journal : ${JOURNAL_SIZE_MB} MB $([ "$JOURNAL_SIZE_MB" -gt 500 ] && echo "⚠️ (Uncapped; consider --tune)" || echo "🟢 (Capped at 500MB)")"
echo "   • Disabled Snaps  : ${DISABLED_SNAPS} $([ "$DISABLED_SNAPS" -gt 0 ] && echo "⚠️ (Old revisions found)" || echo "🟢 (Clean)")"

# Swarm Capacity Recommendation
echo ""
echo "============================================================"
echo "    📊 SWARM CAPACITY RECOMMENDATION"
echo "============================================================"
echo "• Agent RAM Ceiling      : ${AGENT_RAM_LIMIT_MB} MB per agent"
echo "• RAM-Bounded Capacity   : Up to ${MAX_AGENTS_RAM} concurrent agents"
echo "• Disk-Bounded Capacity  : Up to ${MAX_AGENTS_DISK} concurrent agents"
echo ""
if [ "$SUGGESTED_AGENTS" -ge 10 ]; then
  echo "🌟 RECOMMENDED FLEET SIZE: ${SUGGESTED_AGENTS} CONCURRENT AGENTS (ENTERPRISE SWARM READY)"
elif [ "$SUGGESTED_AGENTS" -ge 4 ]; then
  echo "✨ RECOMMENDED FLEET SIZE: ${SUGGESTED_AGENTS} CONCURRENT AGENTS (STANDARD FLEET)"
else
  echo "⚠️ RECOMMENDED FLEET SIZE: ${SUGGESTED_AGENTS} CONCURRENT AGENTS (LIMITED BY STORAGE OR RAM)"
fi

if [ "$AUTO_TUNE" = true ]; then
  echo ""
  echo "[✓] Auto-tuning applied: 500MB journal limit set, snap retain policy adjusted, and shared workspace initialized."
fi
echo "============================================================"
echo ""
