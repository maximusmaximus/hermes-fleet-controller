#!/usr/bin/env bash
# /opt/fleet/bin/fleet-pull.sh
# Automated pull and deployment synchronizer for Hermes Fleet Controller.
# Pulls the latest commits from GitHub origin/main, syncs binaries and skills
# into /opt/fleet, updates systemd services if needed, and re-registers commands.

set -euo pipefail

REPO_DIR="/home/molt/hermes-fleet-controller"
OPT_DIR="/opt/fleet"
BIN_DIR="$OPT_DIR/bin"
SKILLS_DIR="$OPT_DIR/skills"
AGENT_SKILLS_DIR="$OPT_DIR/agents/fleet-controller/skills"
CHANGELOG="$OPT_DIR/changelog.jsonl"
FORCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force|-f)
      FORCE=true
      shift
      ;;
    -h|--help)
      echo "Usage: fleet-pull.sh [--force]"
      echo "Pulls the latest code from GitHub and synchronizes /opt/fleet binaries and skills."
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "[-] Error: Git repository not found at $REPO_DIR" >&2
  exit 1
fi

cd "$REPO_DIR"

# 1. Fetch remote tracking branch
git fetch origin main --quiet

LOCAL_COMMIT=$(git rev-parse HEAD)
REMOTE_COMMIT=$(git rev-parse origin/main)

if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ] && [ "$FORCE" = false ]; then
  echo "🔄 Fleet Controller is already on the latest commit (${LOCAL_COMMIT:0:7})."
  exit 0
fi

echo "============================================================"
echo "    🚀 Syncing Hermes Fleet Controller from GitHub"
echo "============================================================"
echo "Local HEAD  : ${LOCAL_COMMIT:0:7}"
echo "Remote HEAD : ${REMOTE_COMMIT:0:7}"

# 2. Pull changes
PULL_OUTPUT=$(git pull --rebase --autostash origin main)
NEW_COMMIT=$(git rev-parse HEAD)
echo "[✓] Git pull completed: ${NEW_COMMIT:0:7}"

# 3. Synchronize binaries to /opt/fleet/bin
echo "[*] Synchronizing binaries to $BIN_DIR..."
if [ -d "$REPO_DIR/bin" ]; then
  sudo cp -u "$REPO_DIR"/bin/* "$BIN_DIR"/ 2>/dev/null || sudo cp "$REPO_DIR"/bin/* "$BIN_DIR"/
  sudo chmod +x "$BIN_DIR"/*
fi

# 4. Synchronize skills to /opt/fleet/skills
echo "[*] Synchronizing skills to $SKILLS_DIR..."
if [ -d "$REPO_DIR/skills" ]; then
  sudo mkdir -p "$SKILLS_DIR"
  sudo cp -r "$REPO_DIR"/skills/* "$SKILLS_DIR"/
  sudo chmod -R 755 "$SKILLS_DIR"
fi

# 5. Synchronize controller agent skills
if [ -d "$AGENT_SKILLS_DIR" ]; then
  echo "[*] Synchronizing controller agent skills..."
  for skill_dir in "$REPO_DIR"/skills/*; do
    if [ -d "$skill_dir" ]; then
      sname=$(basename "$skill_dir")
      sudo mkdir -p "$AGENT_SKILLS_DIR/$sname"
      sudo cp -r "$skill_dir"/* "$AGENT_SKILLS_DIR/$sname"/ 2>/dev/null || true
    fi
  done
  sudo chown -R 10000:10000 "$OPT_DIR/agents/fleet-controller" 2>/dev/null || true
fi

# 6. Synchronize patches if present
if [ -d "$REPO_DIR/patches" ]; then
  echo "[*] Synchronizing patches..."
  sudo mkdir -p "$OPT_DIR/patches"
  sudo cp -r "$REPO_DIR"/patches/* "$OPT_DIR/patches"/
fi

# 7. Check if dashboard or controller services need restart
if git diff --name-only "$LOCAL_COMMIT" "$NEW_COMMIT" | grep -q "bin/fleet-dashboard.py"; then
  echo "[*] Restarting fleet-dashboard.service due to code update..."
  sudo systemctl restart fleet-dashboard.service || true
fi

if git diff --name-only "$LOCAL_COMMIT" "$NEW_COMMIT" | grep -q "patches/"; then
  echo "[*] Restarting hermes-fleet-controller.service due to patch update..."
  sudo systemctl restart hermes-fleet-controller.service || true
fi

# 8. Refresh Telegram Bot Commands & Keyboard
if [ -f "$BIN_DIR/fleet-tg-keyboard.py" ]; then
  echo "[*] Updating Telegram commands and keyboard..."
  sudo python3 "$BIN_DIR/fleet-tg-keyboard.py" --setup || true
fi

# 9. Record event in changelog.jsonl
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
COMMIT_MSG=$(git log -1 --pretty=%B | head -n 1)
ENTRY=$(printf '{"timestamp":"%s","event":"git_pull","previous_commit":"%s","current_commit":"%s","message":"%s"}\n' \
  "$TIMESTAMP" "${LOCAL_COMMIT:0:7}" "${NEW_COMMIT:0:7}" "$COMMIT_MSG")
echo "$ENTRY" | sudo tee -a "$CHANGELOG" >/dev/null

echo "============================================================"
echo "    🎉 Fleet Controller successfully updated to latest!"
echo "============================================================"
echo "Version: ${NEW_COMMIT:0:7} ($COMMIT_MSG)"
