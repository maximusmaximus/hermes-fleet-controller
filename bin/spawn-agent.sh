#!/usr/bin/env bash
# /opt/fleet/bin/spawn-agent.sh
# Spawns a new child Hermes agent with isolated config, resolved Venice model tier,
# dedicated daily inference allocation, and systemd service supervision.

set -e

NAME=""
QUALITY="medium"
SKILL_DESC=""
DAILY_USD="2.0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    --quality)
      QUALITY="$2"
      shift 2
      ;;
    --skill)
      SKILL_DESC="$2"
      shift 2
      ;;
    --daily-usd)
      DAILY_USD="$2"
      shift 2
      ;;
    *)
      # Positional fallback: name quality [skill...]
      if [ -z "$NAME" ]; then
        NAME="$1"
      elif [ -z "$QUALITY" ]; then
        QUALITY="$1"
      else
        SKILL_DESC="$SKILL_DESC $1"
      fi
      shift
      ;;
  esac
done

if [ -z "$NAME" ]; then
  echo "Error: agent name is required. Usage: spawn-agent.sh --name <name> --quality <high|medium|low> --skill <description> [--daily-usd <usd>]" >&2
  exit 1
fi

# 1. Validate name
if [[ ! "$NAME" =~ ^[a-z0-9-]+$ ]]; then
  echo "Error: Invalid agent name '$NAME'. Must match ^[a-z0-9-]+$" >&2
  exit 1
fi

if [ "$NAME" = "fleet-controller" ] || [ "$NAME" = "controller" ]; then
  echo "Error: Refusing to overwrite fleet-controller" >&2
  exit 1
fi

SKILL_DESC="${SKILL_DESC:-General purpose task worker}"

# 2. Resolve model
MODEL_ID=$(/opt/fleet/bin/venice-resolve-model.sh "$QUALITY")
if [ -z "$MODEL_ID" ]; then
  echo "Error: Could not resolve model for quality tier $QUALITY" >&2
  exit 1
fi

# 3. Create agent directory and issue Venice key with daily inference budget
AGENT_DIR="/opt/fleet/agents/${NAME}"
mkdir -p "$AGENT_DIR"
chmod 750 "$AGENT_DIR"

/opt/fleet/bin/venice-manage-keys.py create-key --name "$NAME" --daily-usd "$DAILY_USD"

# 4. Write specialized system/skill file
cat <<EOF > "${AGENT_DIR}/SOUL.md"
# Child Hermes Agent: ${NAME}
Quality Tier: ${QUALITY}
Model: ${MODEL_ID}
Parent: fleet-controller
Track Updates: latest-${QUALITY}
Daily Budget: \$${DAILY_USD}/day

## Assigned Mission
${SKILL_DESC}

## Standing Rules
- Your inference provider is Venice only.
- Do not attempt to rebind controller model.
EOF
chmod 644 "${AGENT_DIR}/SOUL.md"

# 5. Write config.yaml
cat <<EOF > "${AGENT_DIR}/config.yaml"
_config_version: 12
model:
  default: "${MODEL_ID}"
  provider: "custom"
  base_url: "https://api.venice.ai/api/v1"
  api_key: "\${VENICE_API_KEY}"
EOF
chmod 644 "${AGENT_DIR}/config.yaml"

# Update key-meta with quality & skill
python3 -c "
import json, os
path = '${AGENT_DIR}/key-meta.json'
meta = json.load(open(path)) if os.path.exists(path) else {}
meta.update({
    'quality': '${QUALITY}',
    'model': '${MODEL_ID}',
    'skill': '''${SKILL_DESC}''',
    'track': 'latest-${QUALITY}',
    'daily_usd_limit': float('${DAILY_USD}')
})
json.dump(meta, open(path, 'w'), indent=2)
"

# 6. Allocate port (controller keeps 8642; count existing child agents to increment)
CHILD_COUNT=$(find /opt/fleet/agents -mindepth 1 -maxdepth 1 -type d | grep -v fleet-controller | wc -l)
PORT=$((8642 + CHILD_COUNT))

# 7. Create systemd unit
SERVICE_FILE="/etc/systemd/system/hermes-${NAME}.service"
cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Hermes child agent ${NAME}
After=network-online.target hermes-fleet-controller.service
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=5
TimeoutStartSec=180
ExecStartPre=-/usr/bin/podman rm -f hermes-${NAME}
ExecStart=/usr/bin/podman run --name hermes-${NAME} \\
  --env-file ${AGENT_DIR}/.env \\
  -e HERMES_AGENT_NAME=${NAME} \\
  -v ${AGENT_DIR}:/opt/data:Z \\
  -v /opt/fleet/skills:/opt/fleet/skills:ro,Z \\
  -p 127.0.0.1:${PORT}:8642 \\
  docker.io/nousresearch/hermes-agent:latest
ExecStop=/usr/bin/podman stop -t 10 hermes-${NAME}
ExecStopPost=-/usr/bin/podman rm -f hermes-${NAME}

[Install]
WantedBy=multi-user.target
EOF

# 8. Reload & start
systemctl daemon-reload
systemctl enable --now "hermes-${NAME}.service"

# Wait for active
sleep 3
if systemctl is-active --quiet "hermes-${NAME}.service"; then
  STATE="active"
else
  STATE="activating"
fi

# Refresh inventory
/opt/fleet/bin/fleet-scan.py >/dev/null 2>&1 || true

# 9. Send Telegram notification
MSG=$(cat <<EOF
🚀 Spawned ${NAME}
• Quality: ${QUALITY} (model: ${MODEL_ID})
• Daily Allocation: \$${DAILY_USD}/day
• Skill: ${SKILL_DESC}
• Unit: hermes-${NAME}.service (state: ${STATE}, port: ${PORT})
EOF
)

/opt/fleet/bin/fleet-telegram-notify.sh "$MSG" || true

echo "$MSG"
