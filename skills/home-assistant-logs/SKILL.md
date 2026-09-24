---
name: home-assistant-logs
description: Review, diagnose, and resolve <sibling-node> Core log files, system errors, database corruptions, and supervisor health issues with interactive button support.
---

# <sibling-node> Core Log Diagnostics & Remediation Skill

Use this skill whenever inspecting system health, diagnosing <sibling-node> Core logs, or remediating database / integration errors.

## Core Capabilities
1. **Live Health & Log Inspection**:
   - Execute `/opt/fleet/bin/fleet-ha-health.py status` to get a structured JSON snapshot of:
     - API connectivity
     - Recorder and SQLite database health
     - Active `system_log` error count and messages
     - Supervisor resolution and filesystem health
     - Backup status

2. **Automated Venice AI Diagnosis**:
   - If any errors are present, execute `/opt/fleet/bin/fleet-ha-health.py diagnose`.
   - The diagnosis returns:
     - Plain-English summary of what broke
     - Root cause analysis
     - The recommended remediation action (`repair_db`, `restart_core`, `create_backup`, `clear_logs`)
     - Button label for user confirmation

3. **Interactive Button Support (The Button-First Rule)**:
   - When reporting logs or suggesting a solution, ALWAYS provide the choices via the `clarify` tool so Telegram displays clickable inline buttons:
     ```python
     clarify(questions=[{
         "question": "<sibling-node> Issue Detected: [Summary]. How would you like to proceed?",
         "choices": [
             "🛠️ Apply Proposed Fix",
             "📋 Show Full Trace",
             "🔄 Restart Core",
             "❌ Dismiss"
         ]
     }])
     ```

4. **Action Execution & Verification**:
   - To apply a fix, execute `/opt/fleet/bin/fleet-ha-health.py execute <action_type>`:
     - `repair_db`: Archives corrupt database, triggers core restart, confirms new database generation.
     - `restart_core`: Calls `<sibling-node>.restart` via REST API.
     - `create_backup`: Initiates automatic snapshot.
     - `clear_logs`: Clears in-memory system_log buffer.
   - Re-check `/opt/fleet/bin/fleet-ha-health.py status` to confirm <sibling-node> Core is clean.
   - Report the resolution back to the user with green checkmark confirmation.
