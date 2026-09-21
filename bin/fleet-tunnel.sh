#!/usr/bin/env bash
# /opt/fleet/bin/fleet-tunnel.sh
# Automates Cloudflare Tunnel (Quick Tunnel or Custom Domain via token)
# Exposes the Fleet Controller Web Dashboard (port 8650) securely.

set -e

FLEET_DIR="${FLEET_DIR:-/opt/fleet}"
SECRETS_FILE="$FLEET_DIR/secrets.env"
TUNNEL_URL_FILE="$FLEET_DIR/tunnel-url.txt"
LOG_FILE="/var/log/fleet-tunnel.log"
DASHBOARD_PORT="8650"

# Check if cloudflared is installed
if ! command -v cloudflared &>/dev/null; then
    echo "[INFO] cloudflared not found. Installing..."
    curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cf.deb
    dpkg -i /tmp/cf.deb 2>/dev/null || apt-get install -f -y
    rm -f /tmp/cf.deb
fi

# Check for custom tunnel token in secrets
CF_TOKEN=""
if [ -f "$SECRETS_FILE" ]; then
    CF_TOKEN=$(grep -E "^CLOUDFLARE_TUNNEL_TOKEN=" "$SECRETS_FILE" 2>/dev/null | cut -d'=' -f2- | sed "s/[\"']//g" || true)
fi

if [ -n "$CF_TOKEN" ]; then
    echo "[INFO] Launching Named Cloudflare Tunnel via token..."
    exec cloudflared tunnel run --token "$CF_TOKEN"
else
    echo "[INFO] Launching Quick Cloudflare Tunnel to http://127.0.0.1:$DASHBOARD_PORT..."
    touch "$LOG_FILE"
    chmod 0640 "$LOG_FILE" 2>/dev/null || true

    # Run cloudflared in foreground, pipe stderr/stdout to log and extract URL
    cloudflared tunnel --url "http://127.0.0.1:$DASHBOARD_PORT" --no-autoupdate 2>&1 | tee -a "$LOG_FILE" | while read -r line; do
        if echo "$line" | grep -qE "https://[a-zA-Z0-9-]+\.trycloudflare\.com"; then
            URL=$(echo "$line" | grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" | head -n1)
            echo "$URL" > "$TUNNEL_URL_FILE"
            chmod 0644 "$TUNNEL_URL_FILE" 2>/dev/null || true
            echo "============================================================"
            echo " [OK] Fleet Dashboard Live Public URL:"
            echo " $URL"
            echo "============================================================"
        fi
    done
fi
