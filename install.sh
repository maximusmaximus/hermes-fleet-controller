#!/usr/bin/env bash
# Hermes Fleet Controller — Automatic Installation Script
set -e

echo "============================================================"
echo "    🚀 Installing Hermes Fleet Controller on Guest VM"
echo "============================================================"

FLEET_DIR="/opt/fleet"

mkdir -p "${FLEET_DIR}"/{bin,skills,souls,config,agents/fleet-controller,systemd,quadlets,ssh}
chmod 755 "${FLEET_DIR}"
chmod 700 "${FLEET_DIR}/ssh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy operational scripts
cp -r "${SCRIPT_DIR}/bin/"* "${FLEET_DIR}/bin/"
chmod +x "${FLEET_DIR}/bin/"*

# Symlink status, publish-gh, fleet-backup, and fleet-restore commands to PATH
ln -sf "${FLEET_DIR}/bin/status" /usr/local/bin/status
ln -sf "${FLEET_DIR}/bin/fleet-publish-gh.sh" /usr/local/bin/publish-gh
ln -sf "${FLEET_DIR}/bin/fleet-backup.sh" /usr/local/bin/fleet-backup
ln -sf "${FLEET_DIR}/bin/fleet-restore.sh" /usr/local/bin/fleet-restore

# Copy skills and souls
cp -r "${SCRIPT_DIR}/skills/"* "${FLEET_DIR}/skills/"
cp -r "${SCRIPT_DIR}/souls/"* "${FLEET_DIR}/souls/"

# Copy systemd units
cp "${SCRIPT_DIR}/systemd/"* /etc/systemd/system/
ln -sf /etc/systemd/system/hermes-fleet-controller.service /etc/systemd/system/hermes-gateway.service

# Setup config template if not present
if [ ! -f "${FLEET_DIR}/vm-map.yaml" ]; then
  cp "${SCRIPT_DIR}/config/vm-map.example.yaml" "${FLEET_DIR}/vm-map.yaml"
fi

# Run interactive key setup
echo ""
echo "[*] Launching key attachment and credentials setup..."
"${FLEET_DIR}/bin/fleet-attach-keys.sh"

# Enable and start systemd units
systemctl daemon-reload
systemctl enable --now hermes-fleet-controller.service
systemctl enable --now fleet-daily.timer
systemctl enable --now fleet-doc-sync.timer
systemctl enable --now fleet-backup.timer
systemctl enable --now fleet-update.timer

echo ""
echo "[✓] Hermes Fleet Controller successfully deployed!"
echo "    • Status command: status"
echo "    • GitHub publish: publish-gh"
echo "    • Hot backup: fleet-backup (Vault ceiling: 500 MB)"
echo "    • Disaster recovery: fleet-restore (--list / --latest)"
echo "    • Daily backup timer: fleet-backup.timer (02:00 local)"
echo "    • Daily digest timer: fleet-daily.timer (09:00 local)"
echo "    • Doc sync timer: fleet-doc-sync.timer (08:00 PST)"
echo "    • Weekly update timer: fleet-update.timer (Sun 03:30 local)"
