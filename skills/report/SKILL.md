---
name: report
description: Dispatch the daily operations, fleet status, and MCP tools digest.
---

# Swarm Daily Report Skill

Use this skill when the user sends `/report`, asks for a swarm operations update, or queries active MCP tools.

## Instructions
Execute the reporting tool:
```bash
python3 /opt/fleet/bin/fleet-report.py --stdout
```
Return the formatted markdown output directly to the user.
