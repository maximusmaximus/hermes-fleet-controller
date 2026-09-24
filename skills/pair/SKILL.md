---
name: pair
description: Generate a 6-digit login PIN and direct Cloudflare link to authenticate remote devices on the Hermes Fleet Web Dashboard.
---

# Dashboard Pairing & Remote Login Skill

Use this skill when the user sends `/pair`, `/login`, or asks for the Web Dashboard link or login PIN.

## Instructions
Execute the pairing script:
```bash
python3 /opt/fleet/bin/fleet-pair.py --tg
```
Return the output directly to the user in Telegram.
