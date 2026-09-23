---
name: backup
description: Trigger an immediate zero-lock hot WAL snapshot and SQLite backup of the fleet state.
---

# Fleet Hot Backup Skill

Use this skill when the user sends `/backup` or requests an immediate database backup.

## Instructions
Execute the backup script:
```bash
/opt/fleet/bin/fleet-backup.sh
```
Return the output directly to the user.
