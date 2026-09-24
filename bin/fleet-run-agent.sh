#!/usr/bin/env bash
# /opt/fleet/bin/fleet-run-agent.sh
# Convenient launcher to run a new Hermes agent container connected to this manager.
# Mounts fleet skills, shared workspace, patches, and registers with the manager.

set -euo pipefail

NAME=""
QUALITY="medium"
MODEL=""
PORT=""
MEMORY="500m"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name|-n)
      NAME="$2"
      shift 2
      ;;
    --quality|-q)
      QUALITY="$2"
      shift 2
      ;;
    --model|-m)
      MODEL="$2"
      shift 2
      ;;
    --port|-p)
      PORT="$2"
      shift 2
      ;;
    --memory)
      MEMORY="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: fleet-run-agent.sh --name <agent-name> [--quality <high|medium|low>] [--model <model-id>] [--port <host-port>]"
      exit 0
      ;;
    *)
      if [ -z "$NAME" ]; then
        NAME="$1"
      fi
      shift
      ;;
  esac
done

if [ -z "$NAME" ]; then
  echo "[-] Error: --name is required." >&2
  exit 1
fi

# Clean name
NAME=$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')
CNAME="hermes-${NAME}"

# Check if already running
if podman ps --format "{{.Names}}" | grep -qw "$CNAME"; then
  echo "[!] Container '$CNAME' is already running."
  exit 0
fi

# Remove existing stopped container
podman rm -f "$CNAME" 2>/dev/null || true

# Resolve model if not provided
if [ -z "$MODEL" ]; then
  MODEL=$(/opt/fleet/bin/venice-resolve-model.sh "$QUALITY" 2>/dev/null || echo "deepseek-v4-flash")
fi

# Allocate port if not specified
if [ -z "$PORT" ]; then
  AGENT_COUNT=$(podman ps -a --format "{{.Names}}" | grep -c "hermes-" || echo 1)
  PORT=$((8642 + AGENT_COUNT))
fi

AGENT_DIR="/opt/fleet/agents/${NAME}"
mkdir -p "$AGENT_DIR/skills" "$AGENT_DIR/shared-workspace"
chown -R 10000:10000 "$AGENT_DIR" 2>/dev/null || true

echo "============================================================"
echo "    🚀 Launching Hermes Agent: $NAME"
echo "============================================================"
echo "Container : $CNAME"
echo "Model     : $MODEL ($QUALITY tier)"
echo "Port      : 127.0.0.1:$PORT -> 8642"
echo "Memory    : $MEMORY limit"
echo "Data Dir  : $AGENT_DIR"

# Launch container
podman run -d --name "$CNAME" \
  --memory "$MEMORY" \
  --env-file /opt/fleet/secrets.env \
  -e HERMES_AGENT_NAME="$NAME" \
  -e VENICE_MODEL="$MODEL" \
  -v "${AGENT_DIR}:/opt/data:Z" \
  -v /opt/fleet/skills:/opt/fleet/skills:ro,Z \
  -v /opt/fleet/shared-workspace:/opt/fleet/shared-workspace:rw,Z \
  -v /opt/fleet/patches/run_inbound.py:/opt/hermes/gateway/run_inbound.py:ro,Z \
  -p "127.0.0.1:${PORT}:8642" \
  docker.io/nousresearch/hermes-agent:latest hermes gateway run

sleep 2

# Connect to manager
/opt/fleet/bin/fleet-connect-agent.py "$CNAME" || true

echo "[✓] Container '$CNAME' successfully launched and connected to Fleet Manager!"
