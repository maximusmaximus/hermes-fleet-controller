---
name: spawn-hermes-agent
description: Spawn a specialized child Hermes agent in its own Podman container supervised by systemd, with resolved Venice model tiers, daily inference allocation, and optional MCP integration.
---

# Spawn Hermes Agent Skill

Use this skill when asked to spawn, create, or launch a new child Hermes agent.

## Usage
Run the spawn script:
```bash
/opt/fleet/bin/spawn-agent.sh --name <agent-name> --quality <high|medium|low> --skill "<one-line description of the role>" [--daily-usd <amount>] [--mcp-ha]
```

## Parameters
- `name`: Alphanumeric name with hyphens (`^[a-z0-9-]+$`). Cannot be `fleet-controller`.
- `quality`: `high` | `medium` | `low`. Resolved dynamically from the newest live Venice models in that tier.
- `skill`: Specialized mission or standing order for the child agent.
- `daily-usd`: Daily inference budget in USD (default: `2.0`).
- `--mcp-ha`: Automatically attaches <sibling-node> MCP server (`/api/mcp`) with bearer token credentials.

## Post-Spawn Actions
- The child agent is automatically registered in `/opt/fleet/inventory.yaml`.
- The controller can interact with the child or check its status via `/status`.
- You can message the child or manage its daily budget via `/opt/fleet/bin/venice-manage-keys.py allocate-daily --name <name> --daily-usd <amount>`.
