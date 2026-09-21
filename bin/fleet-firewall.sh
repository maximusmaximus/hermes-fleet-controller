#!/usr/bin/env bash
# /opt/fleet/bin/fleet-firewall.sh
# Granular per-agent network egress firewall and bandwidth monitoring.
# Modes: full, restricted (AI & DNS only), isolated.

set -eo pipefail

FLEET_DIR="${FLEET_DIR:-/opt/fleet}"
BRIDGE_SUBNET="10.88.0.0/16"
VENICE_HOST="api.venice.ai"

usage() {
    echo "Usage: $0 <agent-name> [full|restricted|isolated|status]"
    echo "       $0 --list"
    echo "       $0 --stats <agent-name>"
    exit 1
}

get_container_ip() {
    local cname="hermes-$1"
    podman inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$cname" 2>/dev/null || true
}

resolve_venice_ips() {
    # Resolve IPv4 addresses for api.venice.ai
    getent ahostsv4 "$VENICE_HOST" 2>/dev/null | awk '{print $1}' | sort -u || true
}

clean_agent_rules() {
    local ip="$1"
    [ -z "$ip" ] && return 0
    # Remove existing FORWARD rules for this container IP
    while iptables -D FORWARD -s "$ip" -j DROP 2>/dev/null; do :; done
    while iptables -D FORWARD -s "$ip" -j ACCEPT 2>/dev/null; do :; done
    while iptables -D FORWARD -s "$ip" -m comment --comment "fleet-$2" -j DROP 2>/dev/null; do :; done
    while iptables -D FORWARD -s "$ip" -m comment --comment "fleet-$2" -j ACCEPT 2>/dev/null; do :; done
    # Clean any user chain if present
    iptables -D FORWARD -j "FLEET_$2" 2>/dev/null || true
    iptables -F "FLEET_$2" 2>/dev/null || true
    iptables -X "FLEET_$2" 2>/dev/null || true
}

apply_full() {
    local agent="$1"
    local ip="$2"
    clean_agent_rules "$ip" "$agent"
    mkdir -p "$FLEET_DIR/agents/$agent"
    echo "full" > "$FLEET_DIR/agents/$agent/firewall.state"
    echo "[OK] Firewall for '$agent' ($ip) set to FULL (unrestricted external WAN)."
}

apply_restricted() {
    local agent="$1"
    local ip="$2"
    clean_agent_rules "$ip" "$agent"

    local chain="FLEET_$agent"
    iptables -N "$chain" 2>/dev/null || iptables -F "$chain"

    # 1. Always allow established/related traffic
    iptables -A "$chain" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

    # 2. Safeguard 3: Always whitelist DNS (UDP/TCP port 53) so Venice API resolves
    iptables -A "$chain" -p udp --dport 53 -j ACCEPT
    iptables -A "$chain" -p tcp --dport 53 -j ACCEPT

    # 3. Whitelist local bridge subnet (fleet inter-agent communication & host gateway)
    iptables -A "$chain" -d "$BRIDGE_SUBNET" -j ACCEPT
    iptables -A "$chain" -d 127.0.0.0/8 -j ACCEPT

    # 4. Whitelist api.venice.ai IP addresses on port 443
    local venice_ips
    venice_ips=$(resolve_venice_ips)
    if [ -n "$venice_ips" ]; then
        for vip in $venice_ips; do
            iptables -A "$chain" -p tcp -d "$vip" --dport 443 -j ACCEPT
        done
    else
        # Fallback to general HTTPS if DNS resolve fails at setup time
        iptables -A "$chain" -p tcp --dport 443 -j ACCEPT
    fi

    # 5. Drop everything else outbound
    iptables -A "$chain" -j DROP

    # Attach chain to FORWARD table for this container source IP
    iptables -I FORWARD 1 -s "$ip" -j "$chain"

    mkdir -p "$FLEET_DIR/agents/$agent"
    echo "restricted" > "$FLEET_DIR/agents/$agent/firewall.state"
    echo "[OK] Firewall for '$agent' ($ip) set to RESTRICTED (Venice AI, DNS, & LAN only)."
}

apply_isolated() {
    local agent="$1"
    local ip="$2"
    clean_agent_rules "$ip" "$agent"

    local chain="FLEET_$agent"
    iptables -N "$chain" 2>/dev/null || iptables -F "$chain"

    # Allow local bridge only for controller RPC if necessary, or drop all WAN
    iptables -A "$chain" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -A "$chain" -d "$BRIDGE_SUBNET" -j ACCEPT
    # Drop all WAN egress
    iptables -A "$chain" -j DROP

    iptables -I FORWARD 1 -s "$ip" -j "$chain"

    mkdir -p "$FLEET_DIR/agents/$agent"
    echo "isolated" > "$FLEET_DIR/agents/$agent/firewall.state"
    echo "[OK] Firewall for '$agent' ($ip) set to ISOLATED (no external internet egress)."
}

get_status() {
    local agent="$1"
    local state_file="$FLEET_DIR/agents/$agent/firewall.state"
    local ip
    ip=$(get_container_ip "$agent")

    local mode="full"
    if [ -f "$state_file" ]; then
        mode=$(cat "$state_file" | tr -d '[:space:]')
    fi

    # Check if container is running
    local cname="hermes-$agent"
    local running
    running=$(podman inspect -f '{{.State.Running}}' "$cname" 2>/dev/null || echo "false")

    echo "{\"agent\": \"$agent\", \"mode\": \"$mode\", \"ip\": \"$ip\", \"running\": $running}"
}

get_stats() {
    local agent="$1"
    local cname="hermes-$agent"
    podman stats --no-stream --format json "$cname" 2>/dev/null || echo "{}"
}

list_all() {
    local agents=()
    if [ -d "$FLEET_DIR/agents" ]; then
        for d in "$FLEET_DIR/agents"/*; do
            [ -d "$d" ] && agents+=("$(basename "$d")")
        done
    fi

    echo "["
    local first=1
    for a in "${agents[@]}"; do
        [ $first -eq 0 ] && echo ","
        first=0
        get_status "$a"
    done
    echo "]"
}

# --- CLI Dispatch ---
[ $# -eq 0 ] && usage

case "$1" in
    --list|-l)
        list_all
        exit 0
        ;;
    --stats)
        [ -z "$2" ] && usage
        get_stats "$2"
        exit 0
        ;;
    *)
        AGENT="$1"
        ACTION="${2:-status}"
        IP=$(get_container_ip "$AGENT")

        case "$ACTION" in
            full)
                [ -z "$IP" ] && echo "[WARN] Container hermes-$AGENT is not running; saving state."
                apply_full "$AGENT" "$IP"
                ;;
            restricted)
                [ -z "$IP" ] && echo "[WARN] Container hermes-$AGENT is not running; saving state."
                apply_restricted "$AGENT" "$IP"
                ;;
            isolated)
                [ -z "$IP" ] && echo "[WARN] Container hermes-$AGENT is not running; saving state."
                apply_isolated "$AGENT" "$IP"
                ;;
            status)
                get_status "$AGENT"
                ;;
            *)
                usage
                ;;
        esac
        ;;
esac
