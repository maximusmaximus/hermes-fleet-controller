---
name: dashboard-pairing
description: Generate a 6-digit login PIN and direct Cloudflare link to authenticate remote devices (phones, laptops) on the Hermes Fleet Web Dashboard.
---

# Dashboard Pairing & Remote Login Skill

Use this skill when the user asks for the web dashboard link, requests a login code or PIN, wants to pair a device (phone/laptop), or sends `/pair`, `/login`, or `/code`.

## Usage
Run the pairing tool:
```bash
/opt/fleet/bin/fleet-pair.py --tg
```

## Output Behavior
- Returns the active 6-digit pairing PIN.
- Provides the live, clickable Cloudflare Web Dashboard URL.
- Explains that the PIN expires in 10 minutes and that 5 failed attempts locks the IP for 15 minutes.
- Directly display the formatted message back to the user in Telegram chat.
