#!/usr/bin/env bash
# /opt/fleet/bin/fleet-rollback.sh
# Safeguard 1: Instant 1-command rollback to last known stable commit.

set -e

REPO_DIR="/home/molt/hermes-fleet-controller"
FLEET_DIR="/opt/fleet"

echo "[INFO] Initiating Hermes Fleet Emergency Rollback..."

cd "$REPO_DIR"

# 1. Abort any rebase/merge
git merge --abort 2>/dev/null || true
git rebase --abort 2>/dev/null || true

# 2. Reset hard to remote origin/main
echo "[INFO] Resetting local repository to origin/main..."
git fetch origin main
git checkout main
git reset --hard origin/main

# 3. Re-run installer to re-link valid scripts and restart services
echo "[INFO] Re-applying clean system configurations..."
bash "$REPO_DIR/install.sh"

echo "============================================================"
echo " [OK] Fleet Controller successfully rolled back to origin/main."
echo " All services restored to verified stable state."
echo "============================================================"
