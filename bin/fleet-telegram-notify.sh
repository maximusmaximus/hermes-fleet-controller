#!/usr/bin/env bash
# /opt/fleet/bin/fleet-telegram-notify.sh
# Send a notification to the authorized Telegram user.

set -e

SECRETS_FILE="/opt/fleet/secrets.env"
if [ ! -f "$SECRETS_FILE" ]; then
  echo "Secrets file missing" >&2
  exit 1
fi

TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$SECRETS_FILE" | cut -d'=' -f2- | tr -d '"'\'' ')
USERS=$(grep -E '^TELEGRAM_ALLOWED_USERS=' "$SECRETS_FILE" | cut -d'=' -f2- | tr -d '"'\'' ')

if [ -z "$TOKEN" ] || [ -z "$USERS" ]; then
  echo "Missing bot token or user ID" >&2
  exit 1
fi

MESSAGE="$1"
if [ -z "$MESSAGE" ]; then
  MESSAGE=$(cat -)
fi

IFS=',' read -ra ADDR <<< "$USERS"
for USER_ID in "${ADDR[@]}"; do
  USER_ID=$(echo "$USER_ID" | tr -d ' ')
  if [ -n "$USER_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      -d "chat_id=${USER_ID}" \
      --data-urlencode "text=${MESSAGE}" >/dev/null || true
  fi
done
