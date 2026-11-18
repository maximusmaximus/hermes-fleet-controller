# Fleet Operational Learnings & Workflow Distillations

Daily distillations of operational patterns and heuristics discovered across the agent fleet.


### Sync: 2026-10-07 08:00 PST
- **Agent `venice-key-agent`**:
  * No recent tasks or refined operational guidelines exist in this session.
  * The memory store remains nearly empty with no new tools or best practices recorded.

### Sync: 2026-10-07 08:00 PST
- **Agent `venice-key-agent`**:
  - No new operational guidelines or tools have been refined from recent tasks, as this session has not yet completed any workflows.
  - Existing standard protocols remain in effect without recent modifications or recorded best practice updates.

### Sync: 2026-10-08 08:00 PST
- **Agent `venice-key-agent`**:
  * No recent tasks have been completed in this session to generate new operational guidelines or tool best practices.
  * Existing workflows and constraints remain as initially configured without any recent updates or refinements.

### Sync: 2026-10-08 08:00 PST
- **Agent `venice-key-agent`**:
  * No new operational guidelines, tools, or best practices have been established in this session as no tasks have been completed yet.
  * The current memory state contains only static environment facts, with no accumulated task-derived workflows or refinements to summarize.

### Sync: 2026-10-10 08:00 PST
- **Agent `venice-key-agent`**:
  * No new operational guidelines, tools, or best practices have been identified or refined from recent tasks in this session.
  * The memory store remains minimal, indicating no persistent learnings or workflow adjustments have been captured yet.

### Sync: 2026-10-10 08:00 PST
- **Agent `venice-key-agent`**:
  *   No new operational guidelines, tools, or best practices were refined during recent tasks in this session.
  *   Existing standard operating procedures remain in effect without any modifications or updates.

### Sync: 2026-10-15 08:00 PST
- **Agent `janitor`**:
  No recent cleanup or operational tasks have been executed in this session to abstract lessons from. No refined guidelines, new tools, or updated best practices have emerged — no cycles have run yet.
- **Agent `venice-key-agent`**:
  * No tasks have been completed in this session, so no operational guidelines or best practices were newly refined.
  * The memory store remains minimal, containing no new workflows, tools, or session-specific learnings since the last recorded update.

### Sync: 2026-10-16 08:00 PST
- **Agent `venice-key-agent`**:
  * No new tasks or operational refinements have been recorded in this session.
  * Consequently, there are no updated tools, guidelines, or best practices to summarize.

### Sync: 2026-10-16 08:00 PST
- **Agent `venice-key-agent`**:
  * Recent tasks did not produce any refined operational guidelines, tools, or best practices.
  * Current workflows remain stable with no new artifacts stored in memory.

### Sync: 2026-10-17 08:00 PST
- **Agent `venice-key-agent`**:
  - No recent tasks have been completed in this session to generate refined operational guidelines or best practices.
  - The current memory state indicates a fresh session with no accumulated tool workflows or session-specific operational notes.

### Sync: 2026-10-17 08:00 PST
- **Agent `venice-key-agent`**:
  - No prior tasks have been executed in this session, so no operational refinements exist yet.
  - The workspace and memory remain in their default state without new best practices or tools added.

### Sync: 2026-10-26 08:00 PST
- **Agent `venice-key-agent`**:
  * No new operational guidelines, tools, or best practices were refined during recent tasks.
  * The memory store remains clear of recent task history or workflow updates.

### Sync: 2026-10-26 08:00 PST
- **Agent `janitor`**:
  No recent cleanup or operational tasks have been executed in this session. I was dispatched fresh with no prior runs to draw refinements from, so there is nothing new to abstract yet.

### Sync: 2026-11-11 08:00 PST
- **Agent `venice-key-agent`**:
  Based on recent session activity and memory logs:
  *   **Operational Focus**: Prioritizing zero-data-retention (ZDR) and hardware enclave (E2EE) model reporting for confidential inference tasks.
  *   **Provisioning Protocol**: Enforcing strict mandatory naming for all API keys and unified dual-endpoint delivery (Internal Mesh vs. External Gateway) for immediate agent connectivity.

### Sync: 2026-11-12 08:00 PST
- **Agent `venice-key-agent`**:
  Based on recent operational activity:
  *   **Unified Endpoint Delivery**: Standardized the simultaneous delivery of internal (mesh) and external (web) connection endpoints whenever provisioning API keys to ensure immediate agent connectivity.
  *   **Strict Naming Enforcement**: Implemented a mandatory naming protocol for all minted keys and sub-keys to prevent the creation of anonymous or default-labeled credentials.

### Sync: 2026-11-12 08:00 PST
- **Agent `janitor`**:
  No recent cleanup or storage hygiene tasks have been executed in this session, so there are no newly refined operational guidelines, tools, or best practices to abstract yet. The Fleet Janitor was dispatched fresh with no prior task history to draw from.
- **Agent `venice-key-agent`**:
  Based on recent session activity:
  *   **Dual-Endpoint Delivery**: Established a mandatory protocol for delivering API keys that requires providing both the internal mesh (Tailscale) and external (Cloudflare) endpoints in a single message alongside ready-to-use code snippets.
  *   **Strict Naming Enforcement**: Implemented a "pause-and-ask" requirement for key generation, prohibiting the use of anonymous or default labels to ensure all provisioned assets are explicitly identified.

### Sync: 2026-11-13 08:00 PST
- **Agent `ha-agent`**:
  No significant new operational refinements have emerged from this session's work. My standing guidelines remain in effect:
  - **Zone-scoped lighting only** — bulk commands target the 6 lighting groups + 13 interior fixtures; `back Light` (parking lot) is excluded unless explicitly named.
  - **Context-first before any action** — always query `GetLiveContext` before adjusting or reporting, and never reference entities absent from the live snapshot.
- **Agent `venice-key-agent`**:
  Based on the current session context and memory, there are no newly refined operational guidelines, tools, or best practices to report, as no prior tasks have been completed in this session.

### Sync: 2026-11-13 08:00 PST
- **Agent `janitor`**:
  No recent cleanup tasks have been executed in this session, so there are no newly refined operational guidelines or best practices to abstract yet. All prior memory entries are placeholders awaiting actual cleanup cycles from which lessons could be drawn.
- **Agent `venice-key-agent`**:
  Based on recent operational data, here are the refined guidelines:
  * **Key Provisioning Protocol**: Enforce a mandatory naming rule for all API keys and deliver them alongside dual connection endpoints (internal mesh and external DNS) in a single, consolidated message.
  * **Sub-Key Allocation Logic**: Standardize delegated sub-key budgets to a default of $0.25 per project per day unless explicitly overridden by an administrator.

### Sync: 2026-11-14 08:00 PST
- **Agent `ha-agent`**:
  No material refinements to report. The existing operational guidelines — context-first live entity querying, LocalTuya offline-resilient lighting topology, Button 4 Red Low scene handling, and the 20-hour inactivity Away Mode rule — remain current and unchanged from their last formalization. No new tools, deprecations, or workflow shifts have been absorbed in recent tasks.
- **Agent `janitor`**:
  No recently refined guidelines, tools, or best practices to report. This session has not yet executed any cleanup tasks, so there is nothing new to abstract. The existing **storage-cleaner** skill and **fleet-prune.sh** script remain the current operating procedures. A clear cycle against a live filesystem would be needed before any operational refinement or lesson can be drawn.
- **Agent `venice-key-agent`**:
  Based on the current session history and memory, there are no newly refined operational guidelines, tools, or best practices to report; the system is operating on its base configuration.

### Sync: 2026-11-14 08:00 PST
- **Agent `venice-key-agent`**:
  Based on recent operations, here are the refined guidelines:
  *   **Key Provisioning Protocol**: All API key deliveries must now be bundled with dual connection endpoints (Internal Mesh and External DNS) and ready-to-use client snippets in a single message to ensure immediate connectivity.
  *   **Strict Naming Requirement**: A "no anonymous keys" policy is enforced; agents must pause and explicitly request a unique label from the user before minting any native or external keys.

### Sync: 2026-11-15 08:00 PST
- **Agent `venice-key-agent`**:
  Based on current session history and memory, there are no newly refined operational guidelines, tools, or best practices to summarize at this time.

### Sync: 2026-11-15 08:00 PST
- **Agent `venice-key-agent`**:
  Based on recent activity, here are the refined operational guidelines:
  * **Execution-First Deliverables**: Prioritize producing working artifacts backed by real tool output over plans or descriptions; report honest blockers rather than simulating results.
  * **Efficient Tool Batching**: Group independent read or search operations into single assistant turns to reduce latency and avoid redundant conversation overhead.

### Sync: 2026-11-16 08:00 PST
- **Agent `ha-agent`**:
  Based on recent operational findings recorded across recent sessions:
  **1. LocalTuya offline-resilient lighting topology is fully autonomous post-outage** — The 12 core presence fixtures operate on a local TCP control plane with sub-15ms latency, completely decoupled from cloud dependencies. After power blips, automated recovery daemons re-synchronize bulbs to their intended color/brightness holds rather than defaulting to 100% cold white, eliminating the need for manual scene restoration.
  **2. Aqara Hub M3 and FP2 radar sensors have periodic Zigbee connectivity dropouts** — These can persist for extended periods before auto-recovering. When FP2 zones or the Hub M3 show `unavailable`, a Zigbee hub power cycle restores them. Additionally, the Hub M3's onboard temp/humidity reading consistently reports `unknown` — this is a non-functional placeholder, not a real sensor.
- **Agent `janitor`**:
  No newly refined operational guidelines, tools, or best practices have been developed from recent tasks. This session has not yet executed any cleanup, pruning, or storage hygiene work, so there is no delta to abstract.

### Sync: 2026-11-16 08:00 PST
- **Agent `ha-agent`**:
  Based on the memory and context I have, here are the two most notable recent operational refinements:
  1. **LocalTuya offline-resilient lighting topology** — All 12 core presence fixtures now operate over local TCP (port 6668, sub-15ms latency), fully decoupled from Tuya Cloud. Post-outage auto-sync daemons reapply color holds after power blips instead of leaving bulbs stuck at 100% cold white.
  2. **Time-of-day dynamic resume (no state restore)** — The "Reset to Auto" routine no longer restores previous snapshot scenes. It clears all override flags, checks real-time zone occupancy, then applies the correct circadian scene for the time of day (circadian white during daytime, ambient red or 2000K at night). Vacant zones are auto-turned-off to avoid lingering nighttime lights.
- **Agent `janitor`**:
  No recent task activity exists in this session — no operational refinements, tool updates, or best practices were developed or encountered yet. Nothing to abstract.

### Sync: 2026-11-17 08:00 PST
- **Agent `janitor`**:
  No newly refined operational guidelines, tools, or best practices emerged from this session — no tasks have been executed yet. This session just started and no storage hygiene, pruning, or coordination work has been performed.
- **Agent `venice-key-agent`**:
  Based on the current session history and memory, there are no newly refined operational guidelines, tools, or best practices to report.

### Sync: 2026-11-17 08:00 PST
- **Agent `ha-agent`**:
  Here are two abstracted refinements from recent operational learning:
  1. **Lighting command scope boundary** — Broad/global lighting commands (e.g., "turn on all lights," "shut everything down") should target only interior lighting groups and fixtures. Exterior/perimeter fixtures are excluded from bulk operations unless explicitly named by the user. This prevents unintended activation of outdoor or utility-area lighting during routine commands.
  2. **Sensor availability heuristics** — Several environmental and gateway sensors exhibit predictable intermittent behaviors (brief unavailability after network disruptions with auto-recovery, or placeholder state readings that are normal rather than fault conditions). These should be acknowledged as expected rather than flagged as actionable alerts, reducing unnecessary diagnostics on healthy-but-intermittent hardware.
- **Agent `janitor`**:
  No tasks have been executed yet in this session, so there are no newly refined operational guidelines, tools, or best practices to report.
- **Agent `venice-key-agent`**:
  Based on the current session state, there are no newly refined operational guidelines, tools, or best practices to report. The memory store remains at its baseline state with no task-specific procedures recorded.

### Sync: 2026-11-18 08:00 PST
- **Agent `venice-key-agent`**:
  Based on recent session activity and memory updates:
  *   **Procedural Knowledge Capture**: Shifted toward recording non-trivial workflows and pitfalls as dedicated "skills" via `skill_manage` rather than general memory to optimize context windows.
  *   **Verified Execution**: Established a requirement for producing working artifacts backed by real tool output, replacing descriptive plans or stubs with verified results.

### Sync: 2026-11-18 08:00 PST
- **Agent `venice-key-agent`**:
  Based on the current session history and memory state:
  *   **Operational Baseline**: No new refined guidelines or best practices have been established in this session.
  *   **Tooling Status**: The agent is operating with the standard Venice Key Master configuration and Hermes Swarm fleet coordination protocols.
