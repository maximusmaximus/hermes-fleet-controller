---
name: spawn-hermes-agent
description: Spawn a specialized child Hermes agent in its own Podman container supervised by systemd, with resolved Venice model tiers, daily inference allocation, optional MCP integration, and optional Telegram interface with interactive buttons.
---

# Spawn Hermes Agent Skill

Use this skill when asked to spawn, create, or launch a new child Hermes agent.

## Usage
Run the spawn script:
```bash
/opt/fleet/bin/spawn-agent.sh --name <agent-name> --quality <high|medium|low> --skill "<description>" [--daily-usd <amount>] [--mcp-ha] [--telegram] [--tg-token <token> --tg-bot-name <name>]
```

## Parameters
- `name`: Alphanumeric name with hyphens (`^[a-z0-9-]+$`). Cannot be `fleet-controller`.
- `quality`: `high` | `medium` | `low`. Resolved dynamically from the newest live Venice models in that tier.
- `skill`: Specialized mission or standing order for the child agent.
- `daily-usd`: Daily inference budget in USD (default: `2.0`).
- `--mcp-ha`: Automatically attaches <sibling-node> MCP server (`/api/mcp`) with bearer token credentials.
- `--telegram`: Enables Telegram interface for the child agent. Automatically equips the agent with the `telegram-interface` skill, configures mobile card formatting, and injects rules to present interactive buttons via the `clarify` tool for options and decisions.
- `--tg-token`, `--tg-bot-name`: Optional token and username for the Telegram bot.

## Post-Spawn Actions
- The child agent is automatically registered in `/opt/fleet/inventory.yaml`.
- The controller can interact with the child or check its status via `/status`.
- If Telegram was not configured at spawn time, attach it anytime using:
  ```bash
  /opt/fleet/bin/fleet-attach-agent-tg.sh --name <agent-name> --token <bot-token> --bot-name <username>
  ```
  This automatically activates the `telegram-interface` skill on the target agent.
