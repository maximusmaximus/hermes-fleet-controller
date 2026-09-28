---
name: storage-cleaner
description: Inspect host storage health, monitor disk pressure, and execute safe non-destructive cache and container pruning.
required_tools:
  - bash
  - terminal
---

# Storage Cleaner Skill

Use this skill when responding to `/clean`, `/prune`, `/disk`, `/storage`, or when proactive disk monitoring detects utilization >= 80%.

## Workflows

### 1. Check Current Storage Health
To inspect disk space without making modifications:
```bash
/opt/fleet/bin/fleet-prune.sh --dry-run
```

### 2. Execute Safe Non-Destructive Cleanup
To perform the standard safe pruning routine:
```bash
/opt/fleet/bin/fleet-prune.sh
```

### 3. Force Cleanup (Ignore Utilization Threshold)
When the operator explicitly requests pruning even if disk usage is below the 80% watermark:
```bash
/opt/fleet/bin/fleet-prune.sh --force
```

### 4. Interactive Chat Response
Always return the formatted metric card emitted by `fleet-prune.sh` directly to the user.
