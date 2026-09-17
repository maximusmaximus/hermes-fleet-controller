#!/usr/bin/env bash
# /opt/fleet/bin/fleet-attach-keys.sh
# Prompt and attach a new Venice API key and Telegram Bot token to the Fleet Controller.

set -e

SECRETS_FILE="/opt/fleet/secrets.env"

# Parse optional command-line flags
NEW_VENICE_KEY=""
NEW_TG_TOKEN=""
NEW_TG_USERS=""
NEW_TG_BOT_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venice-key)
      NEW_VENICE_KEY="$2"
      shift 2
      ;;
    --tg-token)
      NEW_TG_TOKEN="$2"
      shift 2
      ;;
    --tg-users)
      NEW_TG_USERS="$2"
      shift 2
      ;;
    --tg-bot-name)
      NEW_TG_BOT_NAME="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Read existing values if file exists
EXISTING_VENICE_KEY=""
EXISTING_TG_TOKEN=""
EXISTING_TG_USERS=""
EXISTING_TG_BOT=""

if [ -f "$SECRETS_FILE" ]; then
  EXISTING_VENICE_KEY=$(grep -E '^VENICE_API_KEY=' "$SECRETS_FILE" | cut -d'=' -f2- || true)
  EXISTING_TG_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$SECRETS_FILE" | cut -d'=' -f2- || true)
  EXISTING_TG_USERS=$(grep -E '^TELEGRAM_ALLOWED_USERS=' "$SECRETS_FILE" | cut -d'=' -f2- || true)
  EXISTING_TG_BOT=$(grep -E '^TELEGRAM_BOT_NAME=' "$SECRETS_FILE" | cut -d'=' -f2- || true)
fi

echo "============================================================"
echo "    Hermes Fleet Controller — Key Configuration Setup"
echo "============================================================"

# Prompt for Venice API key if not provided
if [ -z "$NEW_VENICE_KEY" ]; then
  if [ -t 0 ]; then
    echo ""
    echo "Enter new Venice API Key (Admin key recommended for child quota allocation):"
    read -r -s -p "Venice API Key [Leave blank to keep existing]: " INPUT_VKEY
    echo ""
    if [ -n "$INPUT_VKEY" ]; then
      NEW_VENICE_KEY="$INPUT_VKEY"
    else
      NEW_VENICE_KEY="$EXISTING_VENICE_KEY"
    fi
  else
    NEW_VENICE_KEY="$EXISTING_VENICE_KEY"
  fi
fi

if [ -z "$NEW_VENICE_KEY" ]; then
  echo "Error: Venice API key cannot be empty."
  exit 1
fi

# Validate Venice Key
echo -n "[*] Validating Venice API key... "
MODELS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://api.venice.ai/api/v1/models -H "Authorization: Bearer $NEW_VENICE_KEY")
if [ "$MODELS_STATUS" != "200" ]; then
  echo "FAILED (HTTP $MODELS_STATUS). Please check key."
  exit 1
fi
echo "OK (Models accessible)"

# Check if Admin key
echo -n "[*] Checking Venice Key Admin privileges for key generation... "
ADMIN_STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://api.venice.ai/api/v1/api_keys -H "Authorization: Bearer $NEW_VENICE_KEY")
if [ "$ADMIN_STATUS" = "200" ]; then
  echo "ACTIVE (Admin permissions verified - sub-key creation & daily inference limits enabled)"
  VENICE_KEY_TYPE="ADMIN"
else
  echo "INFERENCE (Standard inference key - child agents will inherit master key limits)"
  VENICE_KEY_TYPE="INFERENCE"
fi

# Prompt for Telegram Token if not provided
if [ -z "$NEW_TG_TOKEN" ]; then
  if [ -t 0 ]; then
    echo ""
    echo "Enter Telegram Bot Token (from @BotFather):"
    read -r -s -p "Telegram Bot Token [Leave blank to keep existing]: " INPUT_TG
    echo ""
    if [ -n "$INPUT_TG" ]; then
      NEW_TG_TOKEN="$INPUT_TG"
    else
      NEW_TG_TOKEN="$EXISTING_TG_TOKEN"
    fi
  else
    NEW_TG_TOKEN="$EXISTING_TG_TOKEN"
  fi
fi

if [ -z "$NEW_TG_TOKEN" ]; then
  echo "Error: Telegram Bot Token cannot be empty."
  exit 1
fi

# Validate Telegram Bot Token and get bot name
echo -n "[*] Validating Telegram Bot Token... "
TG_RESP=$(curl -s "https://api.telegram.org/bot${NEW_TG_TOKEN}/getMe")
TG_OK=$(python3 -c "import json; d=json.loads('''$TG_RESP'''); print(d.get('ok', False))" 2>/dev/null || echo "False")

if [ "$TG_OK" != "True" ]; then
  echo "FAILED. Invalid Telegram Bot Token."
  exit 1
fi

DISCOVERED_BOT_NAME=$(python3 -c "import json; d=json.loads('''$TG_RESP'''); print(d.get('result', {}).get('username', ''))" 2>/dev/null || echo "")
echo "OK (Bot @$DISCOVERED_BOT_NAME verified)"
NEW_TG_BOT_NAME="${NEW_TG_BOT_NAME:-$DISCOVERED_BOT_NAME}"

# Prompt for Telegram Allowed Users
if [ -z "$NEW_TG_USERS" ]; then
  if [ -t 0 ]; then
    echo ""
    echo "Enter authorized numeric Telegram user ID(s) (comma-separated):"
    read -r -p "Allowed Telegram User IDs [default: ${EXISTING_TG_USERS:-8293122782}]: " INPUT_USERS
    if [ -n "$INPUT_USERS" ]; then
      NEW_TG_USERS="$INPUT_USERS"
    else
      NEW_TG_USERS="${EXISTING_TG_USERS:-8293122782}"
    fi
  else
    NEW_TG_USERS="${EXISTING_TG_USERS:-8293122782}"
  fi
fi

if [ -z "$NEW_TG_USERS" ]; then
  echo "Error: TELEGRAM_ALLOWED_USERS is REQUIRED. Refusing to run an open bot."
  exit 1
fi

# Write to /opt/fleet/secrets.env safely
umask 077
cat <<EOF > "$SECRETS_FILE"
VENICE_API_KEY=${NEW_VENICE_KEY}
VENICE_BASE_URL=https://api.venice.ai/api/v1
VENICE_KEY_TYPE=${VENICE_KEY_TYPE}
TELEGRAM_BOT_NAME=${NEW_TG_BOT_NAME}
TELEGRAM_BOT_TOKEN=${NEW_TG_TOKEN}
TELEGRAM_ALLOWED_USERS=${NEW_TG_USERS}
CONTROLLER_MODEL=kimi-k3
FLEET_DIR=/opt/fleet
INVENTORY_FILE=/opt/fleet/inventory.yaml
CHANGELOG_FILE=/opt/fleet/changelog.jsonl
EOF

chmod 0600 "$SECRETS_FILE"
chown root:root "$SECRETS_FILE"

echo ""
echo "[✓] Configuration successfully updated in $SECRETS_FILE (mode 0600 root:root)"
echo "    - Venice Key Type: $VENICE_KEY_TYPE"
echo "    - Telegram Bot: @$NEW_TG_BOT_NAME"
echo "    - Allowed User IDs: $NEW_TG_USERS"

# Restart services if active
if systemctl is-active --quiet hermes-fleet-controller.service 2>/dev/null; then
  echo "[*] Restarting hermes-fleet-controller.service..."
  systemctl restart hermes-fleet-controller.service
fi

if systemctl is-active --quiet hermes-gateway.service 2>/dev/null; then
  echo "[*] Restarting hermes-gateway.service..."
  systemctl restart hermes-gateway.service
fi

echo "[✓] Key update completed."
