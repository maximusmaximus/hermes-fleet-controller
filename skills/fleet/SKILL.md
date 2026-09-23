---
name: fleet
description: Check the live real-time status and health of all Hermes agents, containers, and services.
---

# Fleet Health & Status Skill

Use this skill when the user sends `/fleet`, `/status`, or asks for an overview of virtual machines, containers, and agents.

## Instructions
Execute the status scanner:
```bash
python3 /opt/fleet/bin/fleet-scan.py --status
```
Return the full status output directly to the user.
