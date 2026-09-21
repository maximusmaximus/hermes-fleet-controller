# Hermes Fleet Controller

Autonomous, multi-agent fleet controller orchestrating distributed **Hermes Agents** across virtual machines (VMware Workstation/Pro, VirtualBox, Proxmox, or Bare Metal Linux) with containerized execution via **Podman**, inference routing via **Venice AI**, and messaging management via **Telegram**.

---

## Key Features

- **Controller Architecture**: Runs a persistent primary Hermes Controller (`fleet-controller`) locked to Venice `kimi-k3` with s6-overlay process supervision and boot-without-login reliability.
- **Dynamic Model Tier Resolution**: Automatically polls the live Venice API (`/api/v1/models`) to resolve the newest frontier and private text models by quality band:
  - `high` &rarr; Frontier / Flagship models (e.g. `kimi-k3`, `claude-opus`, `grok`)
  - `medium` &rarr; Fast mid-tier models (e.g. `deepseek-v4-flash`, `kimi-k2.x`, `glm`, `qwen`)
  - `low` &rarr; Lightweight sub-$0.20 models (e.g. `llama-3.2-3b`, `mercury-2.5`)
- **Key Generation & Quota Allocation**: Automatically issues isolated Venice API sub-keys via `POST /api/v1/api_keys` and enforces daily inference budget limits (`consumptionLimit: { usd: X }, limitPeriod: "EPOCH"`) per child agent.
- **Dynamic Agent Spawning**: Includes the native `spawn-hermes-agent` skill allowing the controller to launch new specialized agents on demand with dedicated ports and systemd supervision.
- **Inventory & Topology Discovery**: Tracks host virtual machines, sibling guests, and local containers, generating `/opt/fleet/inventory.yaml`.
- **Daily Digest & Self-Healing**: Runs daily at 09:00 to diff inventory, auto-restart stopped agents, log changes to `changelog.jsonl`, and dispatch a Telegram digest.
- **Daily Docs Sync & Sanitization (08:00 PST)**: Automatically polls all fleet agents daily for soul and skill updates or architectural learnings, rigorously redacts sensitive identifiers (API keys, bot tokens, user IDs, IPs, hostnames), and updates the public docs catalog.
- **Automated Fleet Updates**: Runs weekly (Sunday 03:30) to pull new images, track newest model tiers for child agents, run health-checks, and roll back on failure.
- **Automated GitHub Publishing**: Integrated `publish-gh` tool with pre-flight zero-leak credential validation to safely push generalized souls, skills, and configuration to GitHub.

---

## Directory Structure

```text
hermes-fleet-controller/
├── bin/                              # Operational management scripts
│   ├── fleet-audit.sh                # Pre-flight system resource audit & swarm capacity sizing
│   ├── fleet-backup.sh               # Atomic SQLite VACUUM INTO hot-backup engine (500MB cap)
│   ├── fleet-restore.sh              # One-click disaster recovery & snapshot restoration
│   ├── fleet-publish-gh.sh           # Automated GitHub publisher with zero-leak verification
│   ├── fleet-attach-keys.sh          # Credential setup & validation wizard
│   ├── venice-manage-keys.py         # Sub-key generator & daily inference budget allocator
│   ├── venice-resolve-model.sh/.py   # Dynamic Venice tier resolver with caching
│   ├── fleet-scan.py                 # Inventory builder & /status report generator
│   ├── status                        # Shell wrapper for /status command
│   ├── fleet-sync-docs.py            # Daily 08:00 PST agent polling & de-identified doc sync
│   ├── fleet-daily.py                # Daily inventory diff & self-healing restarter
│   ├── fleet-update.sh               # Self-update orchestrator with rollback guard
│   ├── spawn-agent.sh                # Child agent spawn orchestrator
│   └── fleet-telegram-notify.sh      # Telegram notification dispatcher
├── skills/                           # Hermes skill library
│   └── spawn-hermes-agent/           # Native skill allowing controller to spawn child agents
├── souls/                            # Persona definitions & standing orders
│   ├── controller-soul.md            # Primary controller standing orders
│   ├── ha-agent-soul.md              # Home Assistant specialist persona
│   ├── trollbox-soul.md              # Trollbox / Grok specialist persona
│   ├── devops-soul.md                # DevOps/SRE sentinel persona
│   ├── analyst-soul.md               # Research analyst persona
│   └── worker-soul.md                # Task worker persona
├── config/                           # Environment & configuration templates
│   ├── secrets.env.example           # Secrets template (0600 root:root)
│   ├── config.example.yaml           # Hermes agent config template
│   └── vm-map.example.yaml           # VM topology map template
├── systemd/                          # Systemd service and timer units
│   ├── hermes-fleet-controller.service
│   ├── fleet-doc-sync.service & .timer
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
- Python 3.8+ (`python3`, `python3-pip`, `python3-venv`, `jq`, `curl`, `gh`)

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

## Publishing to GitHub (`publish-gh`)

The included `publish-gh` tool provides safe, automated publishing to GitHub with automated pre-flight zero-leak credential checking.

### Usage

```bash
# Interactive publish (creates or updates https://github.com/<user>/hermes-fleet-controller)
publish-gh

# Headless publish using a GitHub Personal Access Token (PAT)
publish-gh --token ghp_xxxxxxxxxxxxxxxxxxxx

# Publish as a private repository
publish-gh --private

# Dry run verification (runs full zero-leak scan without pushing)
publish-gh --dry-run
```

### Pre-Flight Safety Verification
Before any code or docs are pushed to GitHub, `publish-gh`:
1. Reads `/opt/fleet/secrets.env` and all `/opt/fleet/agents/*/.env` & `key-meta.json` files.
2. Checks all API keys, bot tokens, and passwords against the git history and working tree using exact fixed-string pattern matching.
3. If ANY credential match is found, publishing is immediately aborted with a critical alert.
4. If clean, it pushes to GitHub or creates the remote repository if it does not yet exist.

---

## Atomic Hot-Backup & Disaster Recovery (`fleet-backup` / `fleet-restore`)

The fleet includes an automated zero-downtime backup engine utilizing SQLite's atomic `VACUUM INTO` API to guarantee database consistency without locking active agent sessions.

### Usage

```bash
# Capture immediate atomic hot-snapshot
fleet-backup

# List all available snapshots in the vault
fleet-restore --list

# Restore the newest available snapshot
fleet-restore --latest

# Restore a specific snapshot archive
fleet-restore --file /opt/fleet/backups/fleet-backup-20260920_120000.tar.gz
```

### 500 MB Fleet Resource Governance
To ensure high stability and avoid resource starvation on resource-constrained nodes:
- **Agent Memory Ceiling**: Every agent container is capped at **500 MB RAM** (`--memory 500m`).
- **Backup Vault Quota**: Total backup storage in `/opt/fleet/backups` is hard-capped at **500 MB** with automatic oldest-first snapshot rotation.
- **Journal Log Ceiling**: Systemd journal log storage is capped at **500 MB** (`SystemMaxUse=500M`).

## Pre-Flight System Audit & Swarm Sizing (`fleet-audit`)

The integrated system audit tool validates node hardware, storage hygiene, and egress network reachability, providing calculated sizing recommendations for your swarm.

### Usage

```bash
# Run interactive system audit and swarm sizing report
fleet-audit

# Run system audit and automatically apply recommended storage optimizations
fleet-audit --tune

# Output audit metrics as JSON for programmatic agent consumption
fleet-audit --json
```

### Audited Metrics & Swarm Sizing Logic
- **Compute & Memory**: Evaluates vCPUs, current load, and available RAM. Sizing computes `floor((available_ram - 1.5GB) / 500MB)`.
- **Disk Storage**: Evaluates root partition capacity and free space. Warns if usage &ge; 80% and blocks/flags critical storage exhaustion at &ge; 90%.
- **Network Reachability**: Verifies TCP egress to Venice AI (`api.venice.ai:443`), Telegram (`api.telegram.org:443`), and GitHub (`github.com:443`).
- **Hygiene Auto-Tuning (`--tune`)**: Establishes 500MB systemd journal ceiling, adjusts Snap retention to 2 revisions, and pre-allocates shared fleet workspaces.

---

## Inter-Agent Collaboration & Shared Stack Resources

All fleet agents share coordinated communication and storage layers:
- **Shared Service Registry (`/opt/fleet/registry.json`)**: Live discovery registry populated on every scan detailing active agent roles, models, ports, endpoints, and daily USD inference limits.
- **Shared Agent Workspace (`/opt/fleet/shared-workspace`)**: High-speed bridge mounted with read-write permissions into every agent container (`/opt/fleet/shared-workspace:rw,Z`), enabling agents to pass files, code reviews, and structured task deliverables directly to sibling agents.
- **Global Skill Library (`/opt/fleet/skills`)**: Mounted read-only into all containers so custom skills are immediately discoverable across the swarm.

---

## CLI & Telegram Commands

| Command | Environment | Description |
| :--- | :--- | :--- |
| `status` or `/status` | CLI / Telegram | Generates real-time report of VM states, agent units, Venice latency, and versions |
| `fleet-audit` | CLI | Pre-flight system resource audit, storage hygiene check, and swarm capacity advisor |
| `fleet-backup` | CLI / Timer | Captures atomic zero-lock SQLite snapshot across all agents (500MB vault ceiling) |
| `fleet-restore` | CLI | One-click disaster recovery restoring fleet state and databases |
| `publish-gh` | CLI | Publishes sanitized fleet repository, souls, and skills to GitHub with zero-leak verification |
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
