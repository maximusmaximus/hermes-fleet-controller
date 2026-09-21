#!/usr/bin/env bash
# /opt/fleet/bin/fleet-restore.sh
# Automated Disaster Recovery & Snapshot Restore Tool for Hermes Agent Fleet.

set -euo pipefail

FLEET_DIR="/opt/fleet"
BACKUP_DIR="${FLEET_DIR}/backups"

usage() {
  echo "Usage: fleet-restore.sh [OPTIONS]"
  echo ""
  echo "Options:"
  echo "  --list             List available backup snapshots with details"
  echo "  --latest           Restore the newest available snapshot"
  echo "  --file <path>      Restore a specific snapshot archive"
  echo "  -h, --help         Show this help message"
  exit 0
}

list_backups() {
  echo "============================================================"
  echo "    📦 Available Fleet Backup Snapshots (/opt/fleet/backups)"
  echo "============================================================"
  echo ""
  if ! ls -1 "${BACKUP_DIR}"/fleet-backup-*.tar.gz >/dev/null 2>&1; then
    echo "No backup snapshots found in ${BACKUP_DIR}"
    exit 0
  fi

  printf "%-32s %-12s %-20s\n" "ARCHIVE NAME" "SIZE" "CREATED AT"
  printf "%-32s %-12s %-20s\n" "--------------------------------" "------------" "--------------------"

  for f in $(ls -1t "${BACKUP_DIR}"/fleet-backup-*.tar.gz); do
    fname=$(basename "$f")
    size_bytes=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f")
    size_mb=$(awk "BEGIN {printf \"%.2f MB\", ${size_bytes}/1048576}")
    mod_time=$(stat -c'%y' "$f" 2>/dev/null | cut -d'.' -f1 || stat -f"%Sm" -t "%Y-%m-%d %H:%M:%S" "$f")
    printf "%-32s %-12s %-20s\n" "$fname" "$size_mb" "$mod_time"
  done
  echo ""
  exit 0
}

TARGET_ARCHIVE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)
      list_backups
      ;;
    --latest)
      if ! ls -1 "${BACKUP_DIR}"/fleet-backup-*.tar.gz >/dev/null 2>&1; then
        echo "[-] Error: No snapshots found in ${BACKUP_DIR}" >&2
        exit 1
      fi
      TARGET_ARCHIVE=$(ls -1t "${BACKUP_DIR}"/fleet-backup-*.tar.gz | head -n 1)
      shift
      ;;
    --file)
      TARGET_ARCHIVE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

if [ -z "$TARGET_ARCHIVE" ]; then
  echo "[-] Error: Specify --latest or --file <path> to restore." >&2
  usage
fi

if [ ! -f "$TARGET_ARCHIVE" ]; then
  echo "[-] Error: Archive file not found: $TARGET_ARCHIVE" >&2
  exit 1
fi

echo "============================================================"
echo "    ⚠️ RECOVERY: Restoring Fleet Snapshot"
echo "============================================================"
echo "Source Archive: $(basename "$TARGET_ARCHIVE")"
echo ""

# 1. Validate archive integrity
echo "[*] Validating archive gzip integrity..."
if ! tar -tzf "$TARGET_ARCHIVE" >/dev/null 2>&1; then
  echo "[-] CRITICAL: Backup archive is corrupted or invalid!" >&2
  exit 1
fi
echo "[✓] Archive integrity verified."

# 2. Stop running services before applying restore
echo "[*] Temporarily stopping active Hermes agent services..."
systemctl stop hermes-fleet-controller.service || true
for svc in /etc/systemd/system/hermes-*.service; do
  [ -f "$svc" ] || continue
  sname=$(basename "$svc")
  [ "$sname" = "hermes-gateway.service" ] && continue
  systemctl stop "$sname" || true
done

# 3. Unpack staging
TEMP_RESTORE=$(mktemp -d /tmp/fleet-restore.XXXXXX)
tar -xzf "$TARGET_ARCHIVE" -C "$TEMP_RESTORE"

# 4. Restore agent states and databases
if [ -d "${TEMP_RESTORE}/agents" ]; then
  echo "[*] Restoring agent configurations and databases..."
  for ag_src in "${TEMP_RESTORE}/agents"/*; do
    [ -d "$ag_src" ] || continue
    ag_name=$(basename "$ag_src")
    ag_dest="${FLEET_DIR}/agents/${ag_name}"
    mkdir -p "$ag_dest"

    # Copy files
    cp -rp "${ag_src}"/* "${ag_dest}/"

    # Fix ownership: Hermes container runs as UID/GID 10000
    chown -R 10000:10000 "${ag_dest}"
    chmod 700 "${ag_dest}"
    [ -f "${ag_dest}/.env" ] && chmod 600 "${ag_dest}/.env"
    echo "[✓] Restored agent: ${ag_name}"
  done
fi

# 5. Restore fleet meta
if [ -d "${TEMP_RESTORE}/fleet-meta" ]; then
  echo "[*] Restoring fleet metadata and configs..."
  for meta_file in "${TEMP_RESTORE}/fleet-meta"/*; do
    [ -f "$meta_file" ] || continue
    mname=$(basename "$meta_file")
    cp -p "$meta_file" "${FLEET_DIR}/${mname}"
    if [ "$mname" = "secrets.env" ]; then
      chmod 600 "${FLEET_DIR}/${mname}"
      chown root:root "${FLEET_DIR}/${mname}"
    fi
  done
fi

rm -rf "$TEMP_RESTORE"

# 6. Restart fleet services
echo "[*] Restarting fleet controller and agent services..."
systemctl daemon-reload
systemctl start hermes-fleet-controller.service

for svc in /etc/systemd/system/hermes-*.service; do
  [ -f "$svc" ] || continue
  sname=$(basename "$svc")
  [ "$sname" = "hermes-gateway.service" ] && continue
  [ "$sname" = "hermes-fleet-controller.service" ] && continue
  systemctl start "$sname" || true
done

echo ""
echo "============================================================"
echo "    🎉 SUCCESS: Fleet Successfully Restored from Snapshot!"
echo "============================================================"
echo "Restored from: $(basename "$TARGET_ARCHIVE")"
echo "Run 'status' to view fleet health."
echo ""
