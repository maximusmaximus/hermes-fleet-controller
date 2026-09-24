---
name: dashboard-pairing
description: Generate a Telegram access key, one-click direct login link, and 6-digit PIN to authenticate remote devices (phones, laptops) on the Hermes Fleet Web Dashboard.
---

# Dashboard Pairing & Remote Login Skill

Use this skill when the user asks for the web dashboard link, requests a login code or PIN, wants to pair a device (phone/laptop), or sends `/pair`, `/login`, or `/code`.

## Usage
Run the pairing tool:
```bash
/opt/fleet/bin/fleet-pair.py --tg
```

## Output Behavior
- Returns the one-click auto-login link (`https://<tunnel>/?key=<access_key>`) which bypasses the gate automatically.
- Returns the pasteable Telegram access key and 6-digit pairing PIN.
- Explains that the Web Dashboard cannot be loaded without this Telegram-generated key or PIN.
- Explains that the key expires in 10 minutes and that 5 failed attempts locks the IP for 15 minutes.
- Directly display the formatted message back to the user in Telegram chat.

