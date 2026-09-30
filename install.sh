#!/usr/bin/env bash
# Hermes Fleet Controller — Automatic Installation Script
set -e

echo "============================================================"
echo "    🚀 Installing Hermes Fleet Controller on Guest VM"
echo "============================================================"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 0. Pre-flight System Resource Audit & Swarm Sizing Validation
echo "[*] Running pre-flight system resource audit & swarm capacity check..."
"${SCRIPT_DIR}/bin/fleet-audit.sh" --tune || true

# 1. Ensure Python dependencies are present
echo "[*] Checking Python runtime dependencies (FastAPI, Uvicorn, WebSockets, PyYAML)..."
python3 -c "import fastapi, uvicorn, websockets, yaml" 2>/dev/null || {
    echo "[*] Installing required Python libraries via pip3..."
    pip3 install fastapi uvicorn websockets pyyaml || true
}

# 2. Ensure cloudflared is present
if ! command -v cloudflared &>/dev/null; then
    echo "[*] Installing cloudflared binary..."
    curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cf.deb
    dpkg -i /tmp/cf.deb 2>/dev/null || apt-get install -f -y
    rm -f /tmp/cf.deb
fi

FLEET_DIR="/opt/fleet"

mkdir -p "${FLEET_DIR}"/{bin,skills,souls,config,agents/fleet-controller,systemd,quadlets,ssh,backups,shared-workspace,patches,memory}
chmod 755 "${FLEET_DIR}"
chmod 700 "${FLEET_DIR}/ssh"
chmod 700 "${FLEET_DIR}/backups"
chmod 777 "${FLEET_DIR}/shared-workspace"
chown -R 10000:10000 "${FLEET_DIR}/shared-workspace" 2>/dev/null || true

# 3. Copy operational scripts
cp -r "${SCRIPT_DIR}/bin/"* "${FLEET_DIR}/bin/"
chmod +x "${FLEET_DIR}/bin/"*

# 4. Symlink operational tools to PATH
ln -sf "${FLEET_DIR}/bin/status" /usr/local/bin/status
ln -sf "${FLEET_DIR}/bin/fleet-audit.sh" /usr/local/bin/fleet-audit
ln -sf "${FLEET_DIR}/bin/fleet-publish-gh.sh" /usr/local/bin/publish-gh
ln -sf "${FLEET_DIR}/bin/fleet-backup.sh" /usr/local/bin/fleet-backup
ln -sf "${FLEET_DIR}/bin/fleet-restore.sh" /usr/local/bin/fleet-restore
ln -sf "${FLEET_DIR}/bin/fleet-firewall.sh" /usr/local/bin/fleet-firewall
ln -sf "${FLEET_DIR}/bin/fleet-pair" /usr/local/bin/fleet-pair
ln -sf "${FLEET_DIR}/bin/fleet-factory.py" /usr/local/bin/fleet-factory
ln -sf "${FLEET_DIR}/bin/fleet-report" /usr/local/bin/fleet-report
ln -sf "${FLEET_DIR}/bin/fleet-self-improve.py" /usr/local/bin/fleet-self-improve
ln -sf "${FLEET_DIR}/bin/fleet-rollback.sh" /usr/local/bin/fleet-rollback
ln -sf "${FLEET_DIR}/bin/venice-resolve-model.sh" /usr/local/bin/venice-resolve-model
ln -sf "${FLEET_DIR}/bin/fleet-watchdog.py" /usr/local/bin/fleet-watchdog
ln -sf "${FLEET_DIR}/bin/fleet-connect-agent.py" /usr/local/bin/fleet-connect-agent
ln -sf "${FLEET_DIR}/bin/fleet-boot-check.sh" /usr/local/bin/fleet-boot-check

# 5. Copy skills, souls, and patches
cp -r "${SCRIPT_DIR}/skills/"* "${FLEET_DIR}/skills/"
cp -r "${SCRIPT_DIR}/souls/"* "${FLEET_DIR}/souls/"
cp -r "${SCRIPT_DIR}/patches/"* "${FLEET_DIR}/patches/" 2>/dev/null || true

# 6. Copy systemd units
cp "${SCRIPT_DIR}/systemd/"* /etc/systemd/system/
ln -sf /etc/systemd/system/hermes-fleet-controller.service /etc/systemd/system/hermes-gateway.service

# Setup config templates and required mount files
if [ ! -f "${FLEET_DIR}/vm-map.yaml" ]; then
  cp "${SCRIPT_DIR}/config/vm-map.example.yaml" "${FLEET_DIR}/vm-map.yaml"
fi
if [ ! -f "${FLEET_DIR}/agents/fleet-controller/config.yaml" ]; then
  cp "${SCRIPT_DIR}/config/config.example.yaml" "${FLEET_DIR}/agents/fleet-controller/config.yaml"
fi
touch "${FLEET_DIR}/inventory.yaml"
touch "${FLEET_DIR}/changelog.jsonl"

# 7. Run key setup
echo ""
echo "[*] Launching key attachment and credentials setup..."
"${FLEET_DIR}/bin/fleet-attach-keys.sh" "$@"

# 8. Enable and start systemd units
systemctl daemon-reload

# 8.1. Enable Podman container restart on system boot
systemctl enable podman-restart.service 2>/dev/null || true

# 8.2. Enable and start all Hermes swarm agent services
for unit in /etc/systemd/system/hermes-*.service; do
    [ -f "$unit" ] && systemctl enable --now "$(basename "$unit")" 2>/dev/null || true
done

# 8.3. Enable and start core infrastructure daemons
systemctl enable --now fleet-dashboard.service
systemctl enable --now fleet-tunnel.service
systemctl enable --now fleet-agent-watcher.service
systemctl enable fleet-boot.service 2>/dev/null || true

# 8.4. Enable and start scheduled timers
systemctl enable --now fleet-watchdog.timer
systemctl enable --now fleet-health-digest.timer
systemctl enable --now fleet-prune.timer
systemctl enable --now fleet-pull.timer
systemctl enable --now fleet-ha-log-watcher.timer
systemctl enable --now fleet-report.timer
systemctl enable --now fleet-daily.timer
systemctl enable --now fleet-doc-sync.timer
systemctl enable --now fleet-backup.timer
systemctl enable --now fleet-update.timer
systemctl enable --now fleet-self-improve.timer

echo ""
echo "============================================================"
echo "    🎉 Hermes Fleet Controller Successfully Deployed!"
echo "============================================================"
echo "  • Dashboard Service   : fleet-dashboard.service (Port 8650)"
echo "  • Cloudflare Tunnel   : fleet-tunnel.service"
echo "  • Device Pairing PIN  : Run 'fleet-pair' on this terminal"
echo "  • Status & Audit      : status (or status --audit)"
echo "  • Health Watchdog     : fleet-watchdog (--heal / --json / --digest)"
echo "  • Boot Validator      : fleet-boot.service & fleet-boot-check"
echo "  • Agent Discovery     : fleet-agent-watcher.service"
echo "  • Daily Operations    : fleet-report (--send / --stdout)"
echo "  • Network Firewall    : fleet-firewall <agent> <full|restricted|isolated>"
echo "  • AI Agent Factory    : fleet-factory generate <name> <spec>"
echo "  • Self-Improvement    : fleet-self-improve.timer (Sun 23:00)"
echo "  • Hot-Backup & WAL    : fleet-backup (Vault ceiling: 500 MB)"
echo "  • Disaster Rollback   : fleet-rollback"
echo "  • Remote Publish      : publish-gh"
echo "============================================================"