#!/usr/bin/env bash
# /opt/fleet/bin/fleet-attach-agent-tg.sh
# Configures Telegram bot credentials and gateway routing for a Hermes Fleet agent.
set -euo pipefail

AGENT_NAME=""
TG_TOKEN=""
TG_BOT_NAME=""
TG_USERS="8293122782"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      AGENT_NAME="$2"
      shift 2
      ;;
    --token)
      TG_TOKEN="$2"
      shift 2
      ;;
    --bot-name)
      TG_BOT_NAME="$2"
      shift 2
      ;;
    --allowed-users)
      TG_USERS="$2"
      shift 2
      ;;
    *)
      if [ -z "$AGENT_NAME" ]; then
        AGENT_NAME="$1"
      elif [ -z "$TG_TOKEN" ]; then
        TG_TOKEN="$1"
      elif [ -z "$TG_BOT_NAME" ]; then
        TG_BOT_NAME="$1"
      fi
      shift
      ;;
  esac
done

if [ -z "$AGENT_NAME" ] || [ -z "$TG_TOKEN" ] || [ -z "$TG_BOT_NAME" ]; then
  echo "Usage: $0 --name <agent-name> --token <bot-token> --bot-name <username> [--allowed-users <id>]" >&2
  exit 1
fi

AGENT_DIR="/opt/fleet/agents/${AGENT_NAME}"
if [ ! -d "$AGENT_DIR" ]; then
  echo "Error: Agent directory $AGENT_DIR does not exist." >&2
  exit 1
fi

# Strip leading @ from bot username if provided
TG_BOT_NAME="${TG_BOT_NAME#@}"

# Update .env
if [ -f "${AGENT_DIR}/.env" ]; then
  grep -v '^TELEGRAM_' "${AGENT_DIR}/.env" > "${AGENT_DIR}/.env.tmp" || true
  mv "${AGENT_DIR}/.env.tmp" "${AGENT_DIR}/.env"
fi
cat <<EOF >> "${AGENT_DIR}/.env"
TELEGRAM_BOT_TOKEN=${TG_TOKEN}
TELEGRAM_BOT_NAME=${TG_BOT_NAME}
TELEGRAM_ALLOWED_USERS=${TG_USERS}
EOF
chmod 600 "${AGENT_DIR}/.env"

# Update config.yaml with gateway.telegram and platforms.telegram
python3 -c "
import yaml, os
cfg_path = '${AGENT_DIR}/config.yaml'
cfg = yaml.safe_load(open(cfg_path)) if os.path.exists(cfg_path) else {}
cfg.setdefault('gateway', {})['telegram'] = {
    'enabled': True,
    'bot_token': '\${TELEGRAM_BOT_TOKEN}',
    'allowed_users': '\${TELEGRAM_ALLOWED_USERS}'
}
cfg.setdefault('platforms', {})['telegram'] = {
    'allowed_chats': [u.strip() for u in '${TG_USERS}'.split(',') if u.strip()]
}
yaml.dump(cfg, open(cfg_path, 'w'), default_flow_style=False)
"
chmod 644 "${AGENT_DIR}/config.yaml"

# Update key-meta.json
python3 -c "
import json, os
meta_path = '${AGENT_DIR}/key-meta.json'
meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
meta['telegram_bot'] = '${TG_BOT_NAME}'
meta['telegram_allowed_users'] = '${TG_USERS}'
json.dump(meta, open(meta_path, 'w'), indent=2)
"

# Set permissions for hermes container UID 10000
chown -R 10000:10000 "$AGENT_DIR"

# Restart agent service to pick up new telegram gateway
systemctl restart "hermes-${AGENT_NAME}.service"

echo "Successfully attached @${TG_BOT_NAME} to ${AGENT_NAME} and restarted hermes-${AGENT_NAME}.service."
