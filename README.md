# Hermes Fleet Controller

Autonomous, multi-agent fleet controller orchestrating distributed **Hermes Agents** across virtual machines (VMware Workstation/Pro, VirtualBox, Proxmox, or Bare Metal Linux) with containerized execution via **Podman**, inference routing via **Venice AI**, and messaging management via **Telegram**.

---

## Key Features

- **Controller Architecture**: Runs a persistent primary Hermes Controller (`fleet-controller`) locked to Venice `kimi-k3` with s6-overlay process supervision and boot-without-login reliability.
- **Dynamic Model Tier Resolution**: Automatically polls the live Venice API (`/api/v1/models`) to resolve the newest frontier and private text models by quality band:
  - `high` &rarr; Frontier / Flagship models (e.g. `kimi-k3`, `claude-opus`, `grok`)
  - `medium` &rarr; Fast mid-tier models (e.g. `deepseek-v4-flash`, `kimi-k2.x`, `glm`, `qwen`)
  - `low` &rarr; Lightweight sub-$0.20 models (e.g. `llama-3.2-3b`, `mercury-2.5`)
- **Key Generation & Quota Allocation**: Automatically issues isolated Venice API sub-keys via `POST /api/v1/api_keys` and enforces daily inference budget limits (`consumptionLimit: { usd: X }, limitPeriod: "DAY"`) per child agent.
- **Dynamic Agent Spawning**: Includes the native `spawn-hermes-agent` skill allowing the controller to launch new specialized agents on demand with dedicated ports and systemd supervision.
- **Inventory & Topology Discovery**: Tracks host virtual machines, sibling guests, and local containers, generating `/opt/fleet/inventory.yaml`.
- **Daily Digest & Self-Healing**: Runs daily at 09:00 to diff inventory, auto-restart stopped agents, log changes to `changelog.jsonl`, and dispatch a Telegram digest.
- **Automated Fleet Updates**: Runs weekly (Sunday 03:30) to pull new images, track newest model tiers for child agents, run health-checks, and roll back on failure.

---

## Directory Structure

```text
hermes-fleet-controller/
├── bin/                              # Operational management scripts
│   ├── fleet-attach-keys.sh          # Credential setup & validation wizard
│   ├── venice-manage-keys.py         # Sub-key generator & daily inference budget allocator
│   ├── venice-resolve-model.sh/.py   # Dynamic Venice tier resolver with caching
│   ├── fleet-scan.py                 # Inventory builder & /status report generator
│   ├── status                        # Shell wrapper for /status command
│   ├── fleet-daily.py                # Daily inventory diff & self-healing restarter
│   ├── fleet-update.sh               # Self-update orchestrator with rollback guard
│   ├── spawn-agent.sh                # Child agent spawn orchestrator
│   └── fleet-telegram-notify.sh      # Telegram notification dispatcher
├── skills/                           # Hermes skill library
│   └── spawn-hermes-agent/           # Native skill allowing controller to spawn child agents
├── souls/                            # Persona definitions & standing orders
│   ├── controller-soul.md            # Primary controller standing orders
│   ├── worker-soul.md                # Task worker persona
│   ├── devops-soul.md                # DevOps/SRE sentinel persona
│   └── analyst-soul.md               # Research analyst persona
├── config/                           # Environment & configuration templates
│   ├── secrets.env.example           # Secrets template (0600 root:root)
│   ├── config.example.yaml           # Hermes agent config template
│   └── vm-map.example.yaml           # VM topology map template
├── systemd/                          # Systemd service and timer units
│   ├── hermes-fleet-controller.service
│   ├── fleet-daily.service & .timer
│   └── fleet-update.service & .timer
├── quadlets/                         # Rootful Podman Quadlet examples (Podman >= 4.4)
├── install.sh                        # One-line automated installation script
└── README.md
```

---

## Quick Start Installation

### Prerequisites
- Ubuntu Server / Desktop (24.04 LTS or 20.04+ LTS) or Debian
- Podman (`sudo apt-get install -y podman`)
- Python 3.8+ (`python3`, `python3-pip`, `python3-venv`, `jq`, `curl`)

### Deployment
1. Clone this repository:
   ```bash
   git clone https://github.com/your-org/hermes-fleet-controller.git
   cd hermes-fleet-controller
   ```
2. Run the automated installer with root privileges:
   ```bash
   sudo ./install.sh
   ```
3. Follow the interactive prompt to attach your **Venice API Key** (Admin key recommended for child quota allocation), **Telegram Bot Token**, and **Allowed User ID**.

---

## CLI & Telegram Commands

| Command | Environment | Description |
| :--- | :--- | :--- |
| `status` or `/status` | CLI / Telegram | Generates real-time report of VM states, agent units, Venice latency, and versions |
| `/attach-keys` | CLI | Interactive wizard to attach/rotate Venice and Telegram credentials |
| `/allocate-daily` | CLI | Set or adjust daily inference spending limit for a specific agent |
| `/update-fleet` | CLI / Timer | Trigger immediate image update, model tier refresh, and health-check |

---

## Adding Custom Souls & Skills

- **New Souls**: Add markdown persona files into `souls/<name>-soul.md`. These are injected into `SOUL.md` when spawning new agents.
- **New Skills**: Add skill folders into `skills/<skill-name>/SKILL.md`. All skills in this directory are mounted into containers at `/opt/fleet/skills:ro` and instantly discoverable by agents.

---

## Security

- All API keys, Telegram bot tokens, and user credentials reside exclusively in `/opt/fleet/secrets.env` (mode `0600`, owned by `root:root`).
- Zero secret leakage: Credentials are never echoed in chat logs, stdout, or systemd journal logs.
- Telegram gateway strictly validates `TELEGRAM_ALLOWED_USERS` before starting; open bots are strictly refused.

---

## License

Apache-2.0. Built with [NousResearch Hermes Agent](https://github.com/NousResearch/hermes-agent) and [Venice AI](https://venice.ai).
