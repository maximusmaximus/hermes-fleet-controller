---
name: pull
description: Pull and synchronize the latest updates from the GitHub repository.
---

# Fleet Auto-Pull Skill

Use this skill when the user sends `/pull` or asks to update the fleet controller from GitHub.

## Instructions
Execute the pull script:
```bash
/opt/fleet/bin/fleet-pull.sh
```
Return the output directly to the user.
