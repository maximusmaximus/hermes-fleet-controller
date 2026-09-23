---
name: privacy
description: Inspect hardware-isolated Venice E2EE confidential models and zero data retention status.
---

# Venice Privacy Models Skill

Use this skill when the user sends `/privacy` or inquires about hardware-isolated enclaves (AMD SEV-SNP/Intel TDX) or Zero Data Retention models.

## Instructions
Execute the privacy resolver summary:
```bash
python3 /opt/fleet/bin/venice-resolve-model.py privacy-summary
```
Return the output directly to the user.
