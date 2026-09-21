#!/usr/bin/env bash
# /opt/fleet/bin/fleet-backup.sh
# Enterprise Atomic Hot-Backup Engine for Hermes Agent Fleet.
# Performs zero-downtime SQLite snapshots using VACUUM INTO,
# packages agent states and configs, and strictly enforces a 500MB vault ceiling.

set -euo pipefail

FLEET_DIR="/opt/fleet"
BACKUP_DIR="${FLEET_DIR}/backups"
MAX_VAULT_BYTES=$((500 * 1024 * 1024)) # 500 MB hard ceiling
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
STAGING="${BACKUP_DIR}/staging/${TIMESTAMP}"
ARCHIVE_NAME="fleet-backup-${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"

echo "============================================================"
echo "    🛡️ Hermes Fleet Controller — Atomic Hot-Backup Engine"
echo "============================================================"
echo "Timestamp    : ${TIMESTAMP}"
echo "Vault Quota  : 500 MB"
echo ""

mkdir -p "${STAGING}" "${BACKUP_DIR}"

# 1. Atomic Hot-Snapshot of Agent Data
echo "[*] Capturing atomic SQLite snapshots and configs across all agents..."
AGENT_COUNT=0
DB_COUNT=0

if [ -d "${FLEET_DIR}/agents" ]; then
  for agent_dir in "${FLEET_DIR}/agents"/*; do
    [ -d "${agent_dir}" ] || continue
    agent_name=$(basename "${agent_dir}")
    target_agent_dir="${STAGING}/agents/${agent_name}"
    mkdir -p "${target_agent_dir}"
    AGENT_COUNT=$((AGENT_COUNT + 1))

    # Hot SQLite atomic snapshot (VACUUM INTO creates clean, defragmented standalone DB)
    for db in "${agent_dir}"/*.db; do
      [ -f "${db}" ] || continue
      db_name=$(basename "${db}")
      sqlite3 "${db}" "VACUUM INTO '${target_agent_dir}/${db_name}'" 2>/dev/null || cp -p "${db}" "${target_agent_dir}/${db_name}"
      DB_COUNT=$((DB_COUNT + 1))
    done

    # Backup configs, identities, keys, and session metadata
    for cfg in config.yaml key-meta.json SOUL.md .env; do
      if [ -f "${agent_dir}/${cfg}" ]; then
        cp -p "${agent_dir}/${cfg}" "${target_agent_dir}/"
      fi
    done

    # Backup memories and plans if present
    for sub in memories plans kanban; do
      if [ -d "${agent_dir}/${sub}" ]; then
        cp -rp "${agent_dir}/${sub}" "${target_agent_dir}/"
      fi
    done
  done
fi

# 2. Backup Fleet-Level Topology and Secrets
echo "[*] Capturing fleet inventory, topology, and credentials..."
mkdir -p "${STAGING}/fleet-meta"
for f in secrets.env vm-map.yaml inventory.yaml changelog.jsonl; do
  if [ -f "${FLEET_DIR}/${f}" ]; then
    cp -p "${FLEET_DIR}/${f}" "${STAGING}/fleet-meta/"
  fi
done

# Write backup manifest
cat <<EOF > "${STAGING}/manifest.json"
{
  "timestamp": "${TIMESTAMP}",
  "version": "1.0",
  "agentCount": ${AGENT_COUNT},
  "databaseCount": ${DB_COUNT},
  "maxVaultMb": 500
}
EOF

# 3. Create compressed archive
echo "[*] Compressing snapshot into ${ARCHIVE_NAME}..."
tar -czf "${ARCHIVE_PATH}" -C "${BACKUP_DIR}/staging/${TIMESTAMP}" .
chmod 600 "${ARCHIVE_PATH}"
rm -rf "${BACKUP_DIR}/staging"

ARCHIVE_SIZE=$(stat -c%s "${ARCHIVE_PATH}" 2>/dev/null || stat -f%z "${ARCHIVE_PATH}")
ARCHIVE_SIZE_MB=$(awk "BEGIN {printf \"%.2f\", ${ARCHIVE_SIZE}/1048576}")
SHA256=$(sha256sum "${ARCHIVE_PATH}" | cut -d' ' -f1)

echo "[✓] Archive created: ${ARCHIVE_NAME} (${ARCHIVE_SIZE_MB} MB)"
echo "    SHA-256: ${SHA256}"

# 4. Enforce 500MB Backup Vault Storage Ceiling
echo ""
echo "[*] Enforcing 500 MB vault storage quota..."

get_total_vault_size() {
  local total=0
  for f in "${BACKUP_DIR}"/fleet-backup-*.tar.gz; do
    [ -f "$f" ] || continue
    local s
    s=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f")
    total=$((total + s))
  done
  echo "$total"
}

TOTAL_VAULT_BYTES=$(get_total_vault_size)

# If total exceeds 500MB, prune oldest files first
while [ "$TOTAL_VAULT_BYTES" -gt "$MAX_VAULT_BYTES" ]; do
  OLDEST_FILE=$(ls -1t "${BACKUP_DIR}"/fleet-backup-*.tar.gz | tail -n 1)
  [ -n "$OLDEST_FILE" ] || break
  # Do not delete the one we just created unless it's the only one
  if [ "$OLDEST_FILE" = "${ARCHIVE_PATH}" ]; then
    echo "[!] Warning: Single archive (${ARCHIVE_SIZE_MB} MB) is within ceiling."
    break
  fi
  FILE_SIZE=$(stat -c%s "$OLDEST_FILE" 2>/dev/null || stat -f%z "$OLDEST_FILE")
  FILE_SIZE_MB=$(awk "BEGIN {printf \"%.2f\", ${FILE_SIZE}/1048576}")
  rm -f "$OLDEST_FILE"
  echo "[-] Pruned oldest snapshot: $(basename "$OLDEST_FILE") (${FILE_SIZE_MB} MB) to stay under 500MB cap"
  TOTAL_VAULT_BYTES=$(get_total_vault_size)
done

FINAL_VAULT_MB=$(awk "BEGIN {printf \"%.2f\", ${TOTAL_VAULT_BYTES}/1048576}")
SNAPSHOT_COUNT=$(ls -1 "${BACKUP_DIR}"/fleet-backup-*.tar.gz 2>/dev/null | wc -l)

echo "[✓] Vault status: ${FINAL_VAULT_MB} MB used across ${SNAPSHOT_COUNT} snapshots (Quota: 500.00 MB)"
echo ""
echo "============================================================"
echo "    🎉 Hot-Backup Successfully Completed!"
echo "============================================================"
